"""Local static preview server for a session's materialized files.

Serves the session's files with Python's built-in ``http.server`` on a free
local port. Only static content (HTML/CSS/JS) is meaningfully previewable
this way; sessions with no HTML get an explicit "not servable" message
instead of a server that would serve nothing useful.
"""

import asyncio
import logging
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.services import file_service, materialize_service

logger = logging.getLogger("vibecode.preview")


@dataclass
class PreviewProcess:
    session_id: str
    port: int
    process: subprocess.Popen
    workdir: Path


_previews: dict[str, PreviewProcess] = {}


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _has_html(session_id) -> bool:
    files = await file_service.list_files(session_id)
    return any(
        file_service.split_path(doc.get("name", ""))
        and str(doc.get("name", "")).lower().endswith(".html")
        for doc in files
    )


def _spawn(port: int, workdir: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port),
         "--directory", str(workdir), "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def start_preview(session_id: str) -> dict:
    """Start (or return the existing) local preview server for a session."""
    # `asyncio.create_subprocess_exec` needs a ProactorEventLoop on Windows;
    # uvicorn's worker loop here is a SelectorEventLoop, which raises
    # NotImplementedError for subprocess pipes. Use plain `subprocess.Popen`
    # in a thread instead — works identically on Windows and Linux.
    existing = _previews.get(session_id)
    if existing is not None and existing.process.poll() is None:
        return {
            "url": f"http://127.0.0.1:{existing.port}/",
            "servable": True,
            "message": None,
        }

    if not await _has_html(session_id):
        return {
            "url": None,
            "servable": False,
            "message": (
                "Ce projet ne contient pas de page web statique (index.html) ; "
                "l'aperçu local n'est disponible que pour les projets HTML/CSS/JS."
            ),
        }

    workdir = await materialize_service.materialize_session(session_id)

    for _attempt in range(2):
        port = _find_free_port()
        process = await asyncio.to_thread(_spawn, port, workdir)
        await asyncio.sleep(0.3)
        if process.poll() is None:
            break
        logger.warning("preview server for session %s failed to bind port %s", session_id, port)
    else:
        materialize_service.cleanup_materialized(workdir)
        return {
            "url": None,
            "servable": False,
            "message": "Impossible de démarrer le serveur d'aperçu local.",
        }

    _previews[session_id] = PreviewProcess(
        session_id=session_id, port=port, process=process, workdir=workdir
    )
    logger.info("preview started: session=%s port=%s", session_id, port)
    return {"url": f"http://127.0.0.1:{port}/", "servable": True, "message": None}


def _terminate_sync(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


async def _terminate(preview: PreviewProcess) -> None:
    await asyncio.to_thread(_terminate_sync, preview.process)
    materialize_service.cleanup_materialized(preview.workdir)


async def stop_preview(session_id: str) -> bool:
    preview = _previews.pop(session_id, None)
    if preview is None:
        return False
    await _terminate(preview)
    logger.info("preview stopped: session=%s", session_id)
    return True


async def stop_all_previews() -> None:
    """Terminate every running preview. Call on app shutdown."""
    previews = list(_previews.values())
    _previews.clear()
    for preview in previews:
        await _terminate(preview)
