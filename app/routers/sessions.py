"""HTTP endpoints for session bootstrap and read-only inspection.

Real-time collaboration itself happens over WebSockets (see ws.py); these
endpoints handle creation/joining and let the frontend fetch snapshots.
"""

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.models.schemas import (
    CreateSessionRequest,
    CreateSessionResponse,
    GitHubExportRequest,
    JoinSessionRequest,
    JoinSessionResponse,
)
from app.services import (
    context_service,
    file_service,
    github_service,
    lock_service,
    preview_service,
    session_service,
    upload_service,
)
from app.services.mistral_service import MistralError
from app.utils.serialization import serialize
from app.websockets.connection_manager import manager

router = APIRouter(prefix="/api", tags=["sessions"])


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(payload: CreateSessionRequest):
    """Create a new session and return the WebSocket URL for the system agent."""
    session, creator_id = await session_service.create_session(
        payload.title, payload.creator_name
    )
    session_id = str(session["_id"])
    return CreateSessionResponse(
        session_id=session_id,
        user_id=creator_id,
        status=session["status"],
        system_ws_url=f"/ws/system/{session_id}/{creator_id}",
    )


@router.post("/sessions/join", response_model=JoinSessionResponse)
async def join_session(payload: JoinSessionRequest):
    """Join an active session using its access code."""
    session, user = await session_service.join_session(
        payload.access_code, payload.display_name
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Code d'accès invalide.")
    if user is None:
        raise HTTPException(
            status_code=409,
            detail="La session n'est pas encore prête à accueillir des collaborateurs.",
        )
    session_id = str(session["_id"])
    user_id = str(user["_id"])
    return JoinSessionResponse(
        session_id=session_id,
        user_id=user_id,
        title=session.get("title", ""),
        status=session["status"],
        session_ws_url=f"/ws/session/{session_id}/{user_id}",
    )


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    session = await session_service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session introuvable.")
    return serialize(
        {
            "session_id": session["_id"],
            "title": session.get("title"),
            "status": session.get("status"),
            "mode": session.get("mode"),
            "access_code": session.get("access_code"),
        }
    )


@router.get("/sessions/{session_id}/context")
async def get_context(session_id: str):
    context = await context_service.get_shared_context(session_id)
    if context is None:
        raise HTTPException(status_code=404, detail="Contexte partagé introuvable.")
    return serialize(context)


@router.get("/sessions/{session_id}/files")
async def get_files(session_id: str):
    files = await file_service.list_files(session_id)
    return serialize(files)


@router.get("/sessions/{session_id}/files/{file_id}")
async def get_file(session_id: str, file_id: str):
    file = await file_service.get_file(session_id, file_id)
    if file is None:
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    return serialize(file)


@router.get("/sessions/{session_id}/locks")
async def get_locks(session_id: str):
    locks = await lock_service.list_active_locks(session_id)
    return serialize(locks)


@router.post("/sessions/{session_id}/export/github")
async def export_to_github(session_id: str, payload: GitHubExportRequest):
    session = await session_service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session introuvable.")
    try:
        result = await github_service.push_session_to_github(
            session_id,
            visibility=payload.visibility,
            add_pages_workflow=payload.add_pages_workflow,
        )
    except github_service.GitHubCliMissingError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except github_service.GitHubError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await manager.broadcast(
        session_id,
        {"type": "agent_message", "content": _github_export_message(result)},
    )
    return result


def _github_export_message(result: dict) -> str:
    lines = [f"📦 Projet exporté sur GitHub : [{result['repo_full_name']}]({result['repo_url']})"]
    if result.get("pages_url"):
        lines.append(
            f"🚀 Déploiement en cours sur GitHub Pages : {result['pages_url']} "
            "(la page peut prendre quelques minutes à apparaître)."
        )
    elif result.get("pages_status") == "failed":
        lines.append("⚠️ Impossible d'activer GitHub Pages pour ce dépôt.")
    return "\n\n".join(lines)


@router.post("/sessions/{session_id}/preview")
async def start_session_preview(session_id: str):
    session = await session_service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session introuvable.")
    return await preview_service.start_preview(session_id)


@router.delete("/sessions/{session_id}/preview")
async def stop_session_preview(session_id: str):
    stopped = await preview_service.stop_preview(session_id)
    if not stopped:
        raise HTTPException(status_code=404, detail="Aucun aperçu actif pour cette session.")
    return {"stopped": True}


@router.post("/uploads/extract")
async def extract_upload(file: UploadFile = File(...)):
    """Extract text from an uploaded .txt/.pdf/audio file (no persistence)."""
    data = await file.read()
    try:
        return await upload_service.extract_text(file.filename or "", file.content_type, data)
    except upload_service.UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MistralError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
