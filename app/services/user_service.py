"""User (session participant) management."""

from typing import Optional

from app.database import users_col
from app.utils.ids import to_object_id
from app.utils.time import utcnow


async def get_user(session_id, user_id) -> Optional[dict]:
    return await users_col().find_one(
        {"_id": to_object_id(user_id), "session_id": to_object_id(session_id)}
    )


async def set_connected(session_id, user_id, connected: bool) -> None:
    await users_col().update_one(
        {"_id": to_object_id(user_id), "session_id": to_object_id(session_id)},
        {"$set": {"connected": connected, "last_seen": utcnow()}},
    )


async def list_users(session_id) -> list[dict]:
    cursor = users_col().find({"session_id": to_object_id(session_id)})
    return [doc async for doc in cursor]
