"""HTTP endpoints for session bootstrap and read-only inspection.

Real-time collaboration itself happens over WebSockets (see ws.py); these
endpoints handle creation/joining and let the frontend fetch snapshots.
"""

from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    CreateSessionRequest,
    CreateSessionResponse,
    JoinSessionRequest,
    JoinSessionResponse,
)
from app.services import (
    context_service,
    file_service,
    lock_service,
    session_service,
)
from app.utils.serialization import serialize

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
