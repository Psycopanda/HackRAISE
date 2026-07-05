"""Pending change proposals — the human-in-the-loop approval workflow.

When an agent wants to modify, create or delete files, it does NOT apply the
change. Instead it stores a *proposal* here (never broadcast, so the content
stays private to the requesting user) and the associated task stays
``pending_review`` in the shared context. The user then applies or rejects it.
"""

from typing import Optional

from app.database import proposals_col
from app.utils.ids import to_object_id
from app.utils.time import utcnow


async def create_proposal(
    session_id,
    task_id: str,
    user_id,
    agent_id: str,
    kind: str,  # "modify" | "create" | "delete"
    file_name: str,
    file_id: Optional[str],
    previous_content: Optional[str],
    proposed_content: Optional[str],
    base_version: Optional[int],
    files: Optional[list] = None,
) -> dict:
    document = {
        "session_id": to_object_id(session_id),
        "task_id": task_id,
        "user_id": to_object_id(user_id),
        "agent_id": agent_id,
        "kind": kind,
        "file_name": file_name,
        "file_id": file_id,
        "previous_content": previous_content,
        "proposed_content": proposed_content,
        "base_version": base_version,
        "files": files or [],
        "status": "pending",
        "created_at": utcnow(),
    }
    await proposals_col().insert_one(document)
    return document


async def get_proposal(session_id, task_id: str) -> Optional[dict]:
    return await proposals_col().find_one(
        {"session_id": to_object_id(session_id), "task_id": task_id}
    )


async def set_status(session_id, task_id: str, status: str) -> None:
    await proposals_col().update_one(
        {"session_id": to_object_id(session_id), "task_id": task_id},
        {"$set": {"status": status, "resolved_at": utcnow()}},
    )


async def has_pending_for_name(session_id, file_name: str) -> bool:
    document = await proposals_col().find_one(
        {
            "session_id": to_object_id(session_id),
            "file_name": file_name,
            "status": "pending",
        }
    )
    return document is not None


async def list_pending_for_user(session_id, user_id) -> list:
    """Pending proposals belonging to one user (used to restore on reconnect)."""
    cursor = proposals_col().find(
        {
            "session_id": to_object_id(session_id),
            "user_id": to_object_id(user_id),
            "status": "pending",
        }
    )
    return [doc async for doc in cursor]


def public_proposal(document: Optional[dict]) -> Optional[dict]:
    """Shape a proposal for the ``change_proposal`` WebSocket message."""
    if document is None:
        return None
    return {
        "task_id": document["task_id"],
        "kind": document["kind"],
        "file_id": document.get("file_id"),
        "file_name": document["file_name"],
        "previous_content": document.get("previous_content"),
        "proposed_content": document.get("proposed_content"),
        "files": document.get("files", []),
        "agent_id": document.get("agent_id"),
    }
