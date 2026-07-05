"""Session lifecycle: creation, initialisation finalisation and joining."""

import logging
from typing import Optional, Tuple

from pymongo.errors import DuplicateKeyError

from app.database import sessions_col, users_col
from app.services import context_service, file_service
from app.utils.ids import generate_access_code, to_object_id
from app.utils.time import utcnow

logger = logging.getLogger("vibecode.session")


async def create_session(
    title: Optional[str] = None, creator_name: Optional[str] = None
) -> Tuple[dict, str]:
    """Create a session in the ``initializing`` state and its creator user.

    No access code is issued yet: it is generated only once the system agent
    has produced the master context (see ``finalize_session``).
    """
    now = utcnow()
    session_doc = {
        "title": title or "Nouvelle session VibeCode",
        "status": "initializing",
        "mode": "text",
        "creator_id": None,
        "created_at": now,
        "updated_at": now,
    }
    result = await sessions_col().insert_one(session_doc)
    session_id = result.inserted_id

    user_doc = {
        "session_id": session_id,
        "display_name": creator_name or "Créateur",
        "role": "creator",
        "connected": False,
        "joined_at": now,
        "last_seen": now,
    }
    user_result = await users_col().insert_one(user_doc)
    creator_id = user_result.inserted_id

    await sessions_col().update_one(
        {"_id": session_id}, {"$set": {"creator_id": creator_id}}
    )
    session_doc["_id"] = session_id
    session_doc["creator_id"] = creator_id
    logger.info("session created: %s (creator=%s)", session_id, creator_id)
    return session_doc, str(creator_id)


async def get_session(session_id) -> Optional[dict]:
    return await sessions_col().find_one({"_id": to_object_id(session_id)})


async def set_github_repo(session_id, repo_full_name: str, html_url: str) -> None:
    """Remember which GitHub repo a session was exported to (for idempotent re-export)."""
    await sessions_col().update_one(
        {"_id": to_object_id(session_id)},
        {"$set": {"github_repo": {"full_name": repo_full_name, "html_url": html_url}}},
    )


async def get_session_by_code(access_code: str) -> Optional[dict]:
    return await sessions_col().find_one({"access_code": access_code.strip().upper()})


async def finalize_session(session_id, master_context: dict) -> str:
    """Persist the master context, create the initial document and issue a code.

    Idempotent: if the session is already active it returns the existing code.
    """
    session = await get_session(session_id)
    if session is None:
        raise ValueError("Session introuvable")
    if session.get("status") == "active" and session.get("access_code"):
        return session["access_code"]

    # The shared context becomes the single source of truth for the session.
    await context_service.create_shared_context(session_id, master_context)
    await file_service.create_default_document(session_id)

    title = _derive_title(master_context)

    # Generate a unique access code; the partial unique index is the real guard.
    for _ in range(20):
        candidate = generate_access_code()
        try:
            await sessions_col().update_one(
                {"_id": to_object_id(session_id)},
                {
                    "$set": {
                        "access_code": candidate,
                        "status": "active",
                        "title": title,
                        "updated_at": utcnow(),
                    }
                },
            )
            logger.info("session finalized: %s access_code=%s", session_id, candidate)
            return candidate
        except DuplicateKeyError:
            continue
    raise RuntimeError("Impossible de générer un code d'accès unique")


async def join_session(
    access_code: str, display_name: Optional[str] = None
) -> Tuple[Optional[dict], Optional[dict]]:
    """Add a collaborator to an active session.

    Returns ``(session, user)``. ``user`` is ``None`` when the code is valid
    but the session is not yet active; both are ``None`` for an unknown code.
    """
    session = await get_session_by_code(access_code)
    if session is None:
        return None, None
    if session.get("status") != "active":
        return session, None

    now = utcnow()
    user_doc = {
        "session_id": session["_id"],
        "display_name": display_name or "Collaborateur",
        "role": "collaborator",
        "connected": False,
        "joined_at": now,
        "last_seen": now,
    }
    result = await users_col().insert_one(user_doc)
    user_doc["_id"] = result.inserted_id
    logger.info(
        "user joined: session=%s user=%s (%s)",
        session["_id"], user_doc["_id"], user_doc["display_name"],
    )
    return session, user_doc


def _derive_title(master_context: dict) -> str:
    """Produce a short, human-friendly project title (like an LLM app's
    conversation title) from the master context.

    Prefers the agent-provided ``title``; otherwise falls back to a trimmed
    version of the objective.
    """
    raw = (master_context.get("title") or "").strip()
    if not raw:
        raw = (master_context.get("objective") or "").strip()
    if not raw:
        return "Projet VibeCode"

    first_line = raw.splitlines()[0].strip().strip("\"'«»").strip()
    words = first_line.split()
    short = " ".join(words[:6]).rstrip(".,;:—- ")
    if len(short) > 60:
        short = short[:57].rstrip() + "…"
    elif len(words) > 6:
        short += "…"
    return short or "Projet VibeCode"
