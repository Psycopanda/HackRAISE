"""Write a session's Mongo-backed files to a real directory on disk.

The generated codebase for a session only ever exists as ``files`` documents
in MongoDB (see ``file_service``). Exporting to GitHub or serving a local
preview both need an actual directory tree, so this module is the single
place that turns those documents into files on disk.
"""

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from app.services import file_service

logger = logging.getLogger("vibecode.materialize")


async def materialize_session(session_id, base_dir: Optional[Path] = None) -> Path:
    """Write all of a session's files to a fresh temp directory.

    Names are treated as ``/``-delimited paths, reusing the same splitting
    convention as ``file_service.build_file_tree`` so the on-disk layout
    matches what the UI shows. Returns the directory path.
    """
    workdir = Path(
        tempfile.mkdtemp(prefix=f"vibecode_{session_id}_", dir=base_dir)
    )
    await write_files_into(session_id, workdir)
    return workdir


async def write_files_into(session_id, workdir: Path) -> None:
    """Write all of a session's files into an existing directory (e.g. a clone)."""
    files = await file_service.list_files(session_id)

    for document in files:
        parts = file_service.split_path(document.get("name", ""))
        if not parts or parts[-1] == ".keep":
            continue

        target = workdir.joinpath(*parts)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir():
                logger.warning(
                    "skipping file %r: a folder already exists at that path",
                    document.get("name"),
                )
                continue
            target.write_text(
                document.get("content", "") or "", encoding="utf-8", errors="replace"
            )
        except OSError:
            logger.warning(
                "failed to materialize file %r for session %s",
                document.get("name"), session_id, exc_info=True,
            )


def cleanup_materialized(path: Path) -> None:
    """Best-effort recursive removal of a materialized directory."""
    shutil.rmtree(path, ignore_errors=True)
