"""Chat message persistence (used to give agents conversation memory)."""

from typing import Optional

from app.database import messages_col
from app.utils.ids import to_object_id
from app.utils.time import utcnow


async def save_message(
    session_id,
    user_id,
    role: str,
    scope: str,
    content: str,
    agent_id: Optional[str] = None,
) -> dict:
    document = {
        "session_id": to_object_id(session_id),
        "user_id": to_object_id(user_id),
        "agent_id": agent_id,
        "role": role,
        "scope": scope,
        "content": content,
        "created_at": utcnow(),
    }
    result = await messages_col().insert_one(document)
    document["_id"] = result.inserted_id
    return document


async def get_history(session_id, user_id, scope: str, limit: int = 20) -> list[dict]:
    """Return the most recent messages for a (user, scope), oldest first."""
    cursor = (
        messages_col()
        .find(
            {
                "session_id": to_object_id(session_id),
                "user_id": to_object_id(user_id),
                "scope": scope,
            }
        )
        .sort("created_at", 1)
    )
    documents = [doc async for doc in cursor]
    return documents[-limit:]
