"""Push a materialized session to GitHub via the `gh` CLI, optionally wiring
up a GitHub Pages deploy workflow.

Relies entirely on an ambient, already-authenticated `gh` CLI (``gh auth
login``) on the machine running the backend — no GitHub token is stored or
managed by the application itself.
"""

import asyncio
import json
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from app.services import materialize_service, session_service

logger = logging.getLogger("vibecode.github")

_PAGES_WORKFLOW = """\
name: Deploy to GitHub Pages
on:
  push:
    branches: [main]
permissions:
  contents: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: true
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with:
          path: '.'
      - id: deployment
        uses: actions/deploy-pages@v4
"""

_GITIGNORE = "__pycache__/\n*.pyc\n.DS_Store\nnode_modules/\n"


class GitHubError(Exception):
    """Raised when a `git`/`gh` subprocess call fails."""


class GitHubCliMissingError(GitHubError):
    """Raised when the `gh` or `git` binaries aren't available."""


def check_gh_available() -> None:
    if shutil.which("git") is None:
        raise GitHubCliMissingError(
            "`git` est introuvable sur le PATH de ce serveur."
        )
    if shutil.which("gh") is None:
        raise GitHubCliMissingError(
            "`gh` (GitHub CLI) est introuvable sur le PATH de ce serveur. "
            "Installez-le depuis https://cli.github.com et lancez `gh auth login`."
        )


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "session"


def derive_repo_name(session: dict, session_id: str) -> str:
    title = (session or {}).get("title") or ""
    suffix = str(session_id)[-6:]
    if title.strip():
        return f"{_slugify(title)}-{suffix}"
    return f"vibecode-session-{suffix}"


def _run_sync(args: tuple, cwd: Optional[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=cwd, capture_output=True,
    )


async def _run(*args: str, cwd: Optional[Path] = None) -> str:
    # `asyncio.create_subprocess_exec` needs a ProactorEventLoop on Windows;
    # uvicorn's worker loop here is a SelectorEventLoop, which raises
    # NotImplementedError for subprocess pipes. Run synchronously in a
    # thread instead — works identically on Windows and Linux.
    result = await asyncio.to_thread(_run_sync, args, str(cwd) if cwd else None)
    if result.returncode != 0:
        raise GitHubError(
            f"`{' '.join(args)}` a échoué : {result.stderr.decode(errors='replace').strip()}"
        )
    return result.stdout.decode(errors="replace").strip()


async def enable_pages_via_actions(owner: str, repo: str) -> None:
    """Enable GitHub Pages (source: GitHub Actions) for a repo. Idempotent."""
    try:
        await _run(
            "gh", "api", f"repos/{owner}/{repo}/pages", "-X", "POST",
            "-f", "build_type=workflow",
        )
    except GitHubError as exc:
        if "already" in str(exc).lower() or "409" in str(exc):
            logger.info("Pages already enabled for %s/%s", owner, repo)
            return
        raise


def _write_extras(workdir: Path, add_pages_workflow: bool) -> None:
    (workdir / ".gitignore").write_text(_GITIGNORE, encoding="utf-8")
    if add_pages_workflow:
        workflow_dir = workdir / ".github" / "workflows"
        workflow_dir.mkdir(parents=True, exist_ok=True)
        (workflow_dir / "pages.yml").write_text(_PAGES_WORKFLOW, encoding="utf-8")


async def _prepare_reexport_workdir(session_id: str, full_name: str) -> Path:
    """Clone the already-exported repo and overlay the session's current
    files on top of it, so re-exporting is a normal fast-forward commit
    instead of a from-scratch history needing a force push."""
    workdir = Path(tempfile.mkdtemp(prefix=f"vibecode_{session_id}_"))
    workdir.rmdir()  # `git clone` expects the target path to not exist yet.
    remote_url = f"https://github.com/{full_name}.git"
    await _run("git", "clone", remote_url, str(workdir))

    for entry in workdir.iterdir():
        if entry.name == ".git":
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()

    await materialize_service.write_files_into(session_id, workdir)
    return workdir


async def push_session_to_github(
    session_id: str,
    *,
    visibility: str = "private",
    add_pages_workflow: bool = False,
) -> dict:
    check_gh_available()

    session = await session_service.get_session(session_id)
    if session is None:
        raise GitHubError("Session introuvable.")

    existing = (session.get("github_repo") or {}).get("full_name")
    if existing:
        workdir = await _prepare_reexport_workdir(session_id, existing)
    else:
        workdir = await materialize_service.materialize_session(session_id)

    try:
        _write_extras(workdir, add_pages_workflow)

        if existing:
            await _run("git", "-C", str(workdir), "add", "-A")
            status = await _run("git", "-C", str(workdir), "status", "--porcelain")
            if status.strip():
                await _run(
                    "git", "-c", "user.name=VibeCode", "-c", "user.email=vibecode@localhost",
                    "-C", str(workdir), "commit", "-m", "Export depuis VibeCode",
                )
                await _run("git", "-C", str(workdir), "push", "origin", "HEAD:main")
            repo_full_name = existing
            repo_url = f"https://github.com/{existing}"
        else:
            await _run("git", "init", "-b", "main", cwd=workdir)
            await _run("git", "-C", str(workdir), "add", "-A")
            await _run(
                "git", "-c", "user.name=VibeCode", "-c", "user.email=vibecode@localhost",
                "-C", str(workdir), "commit", "-m", "Export depuis VibeCode",
            )
            repo_name = derive_repo_name(session, session_id)
            try:
                await _run(
                    "gh", "repo", "create", repo_name, f"--{visibility}",
                    "--source=.", "--remote=origin", "--push",
                    cwd=workdir,
                )
            except GitHubError as exc:
                if "already exists" in str(exc).lower():
                    repo_name = f"{repo_name}-{session_id[-4:]}"
                    await _run(
                        "gh", "repo", "create", repo_name, f"--{visibility}",
                        "--source=.", "--remote=origin", "--push",
                        cwd=workdir,
                    )
                else:
                    raise
            # `gh repo create --push` also runs `git push`, whose own output
            # can end up as the last captured line — query the repo directly
            # instead of parsing free-text command output for the URL.
            view_output = await _run(
                "gh", "repo", "view", repo_name, "--json", "nameWithOwner,url",
                cwd=workdir,
            )
            view = json.loads(view_output)
            repo_full_name = view["nameWithOwner"]
            repo_url = view["url"]
            await session_service.set_github_repo(session_id, repo_full_name, repo_url)

        pages_url = None
        pages_status = None
        if add_pages_workflow:
            owner, repo = repo_full_name.split("/", 1)
            try:
                await enable_pages_via_actions(owner, repo)
                pages_url = f"https://{owner}.github.io/{repo}/"
                pages_status = "triggered"
            except GitHubError as exc:
                logger.warning("failed to enable Pages for %s: %s", repo_full_name, exc)
                pages_status = "failed"

        return {
            "repo_url": repo_url,
            "repo_full_name": repo_full_name,
            "pages_url": pages_url,
            "pages_status": pages_status,
        }
    finally:
        materialize_service.cleanup_materialized(workdir)
