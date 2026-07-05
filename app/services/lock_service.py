"""Resource-level distributed locking (the conflict-resolution core).

Strategy: fine-grained, lease-based, optimistic claiming.

* A lock is one document keyed by a unique compound index
  ``(session_id, resource_id)`` — so MongoDB guarantees a single owner.
* ``acquire_lock`` is a *single atomic* ``find_one_and_update`` upsert:
    - if no lock exists  -> it is inserted (we win instantly);
    - if an expired lock exists -> it is taken over in place;
    - if an active lock exists -> the upsert insert violates the unique index
      and raises ``DuplicateKeyError`` -> we return ``None`` (busy) instantly.
  It never blocks or waits.
* Each lock carries an ``expires_at`` lease. A TTL index physically removes
  expired locks, so a crashed/disconnected agent never blocks a resource.
"""

from datetime import timedelta
from typing import Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.config import get_settings
from app.database import locks_col
from app.utils.ids import to_object_id
from app.utils.time import utcnow


async def acquire_lock(
    session_id,
    resource_id: str,
    user_id,
    agent_id: str,
    description: str,
    lease_seconds: Optional[int] = None,
) -> Optional[dict]:
    """Atomically try to claim ``resource_id``.

    Returns the lock document on success, or ``None`` if the resource is
    currently locked by another active agent (non-blocking).
    """
    lease = lease_seconds or get_settings().lock_lease_seconds
    now = utcnow()
    expires_at = now + timedelta(seconds=lease)
    session_oid = to_object_id(session_id)
    user_oid = to_object_id(user_id)

    filter_query = {
        "session_id": session_oid,
        "resource_id": resource_id,
        # Only match an EXISTING lock if it has already expired.
        "expires_at": {"$lte": now},
    }
    update = {
        "$set": {
            "owner_user_id": user_oid,
            "owner_agent_id": agent_id,
            "description": description,
            "acquired_at": now,
            "expires_at": expires_at,
        },
        "$setOnInsert": {
            "session_id": session_oid,
            "resource_id": resource_id,
        },
    }

    try:
        return await locks_col().find_one_and_update(
            filter_query,
            update,
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        # An active (non-expired) lock already exists -> resource is busy.
        return None


async def release_lock(session_id, resource_id: str, user_id) -> bool:
    """Release a lock owned by ``user_id``. Returns True if a lock was removed."""
    result = await locks_col().delete_one(
        {
            "session_id": to_object_id(session_id),
            "resource_id": resource_id,
            "owner_user_id": to_object_id(user_id),
        }
    )
    return result.deleted_count > 0


async def refresh_lock(
    session_id, resource_id: str, user_id, lease_seconds: Optional[int] = None
) -> bool:
    """Extend the lease of a held lock (heartbeat for long tasks)."""
    lease = lease_seconds or get_settings().lock_lease_seconds
    new_expiry = utcnow() + timedelta(seconds=lease)
    result = await locks_col().update_one(
        {
            "session_id": to_object_id(session_id),
            "resource_id": resource_id,
            "owner_user_id": to_object_id(user_id),
        },
        {"$set": {"expires_at": new_expiry}},
    )
    return result.modified_count > 0


async def get_lock(session_id, resource_id: str) -> Optional[dict]:
    """Return the active (non-expired) lock for a resource, if any."""
    now = utcnow()
    return await locks_col().find_one(
        {
            "session_id": to_object_id(session_id),
            "resource_id": resource_id,
            "expires_at": {"$gt": now},
        }
    )


async def list_active_locks(session_id) -> list[dict]:
    """Return every active lock in a session (used for snapshots/awareness)."""
    now = utcnow()
    cursor = locks_col().find(
        {"session_id": to_object_id(session_id), "expires_at": {"$gt": now}}
    )
    return [doc async for doc in cursor]
