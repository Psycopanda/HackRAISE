"""MongoDB connection management and collection accessors (async, via Motor)."""

import logging

from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorCollection,
    AsyncIOMotorDatabase,
)
from pymongo import ASCENDING

from app.config import get_settings

logger = logging.getLogger("vibecode.database")


class _DatabaseState:
    client: AsyncIOMotorClient | None = None
    database: AsyncIOMotorDatabase | None = None


_state = _DatabaseState()


async def connect_to_mongo() -> None:
    """Open the MongoDB connection, verify it and ensure indexes exist."""
    settings = get_settings()
    _state.client = AsyncIOMotorClient(
        settings.mongo_uri, serverSelectionTimeoutMS=5000
    )
    _state.database = _state.client[settings.mongo_db_name]
    await _state.client.admin.command("ping")
    await ensure_indexes()
    logger.info("MongoDB connection established (database: %s)", settings.mongo_db_name)


async def close_mongo_connection() -> None:
    """Close the MongoDB connection."""
    if _state.client is not None:
        _state.client.close()
        _state.client = None
        _state.database = None
        logger.info("MongoDB connection closed")


def get_database() -> AsyncIOMotorDatabase:
    if _state.database is None:
        raise RuntimeError("Database is not initialised. Call connect_to_mongo() first.")
    return _state.database


# --- Collection accessors -------------------------------------------------

def sessions_col() -> AsyncIOMotorCollection:
    return get_database()["sessions"]


def users_col() -> AsyncIOMotorCollection:
    return get_database()["users"]


def files_col() -> AsyncIOMotorCollection:
    return get_database()["files"]


def context_col() -> AsyncIOMotorCollection:
    return get_database()["shared_context"]


def locks_col() -> AsyncIOMotorCollection:
    return get_database()["locks"]


def messages_col() -> AsyncIOMotorCollection:
    return get_database()["messages"]


def proposals_col() -> AsyncIOMotorCollection:
    return get_database()["proposals"]


# --- Indexes --------------------------------------------------------------

async def ensure_indexes() -> None:
    """Create all indexes required for correctness and performance.

    The access-code index is *partial* (only string values) so that many
    sessions can coexist in the "initializing" state without an access code.
    The locks collection has a unique compound key that guarantees a single
    owner per resource, plus a TTL index that auto-releases expired leases.
    """
    await sessions_col().create_index(
        "access_code",
        unique=True,
        partialFilterExpression={"access_code": {"$type": "string"}},
    )
    await users_col().create_index("session_id")
    await files_col().create_index("session_id")
    await context_col().create_index("session_id", unique=True)
    await messages_col().create_index(
        [("session_id", ASCENDING), ("created_at", ASCENDING)]
    )
    await locks_col().create_index(
        [("session_id", ASCENDING), ("resource_id", ASCENDING)], unique=True
    )
    await locks_col().create_index("expires_at", expireAfterSeconds=0)
    await proposals_col().create_index(
        [("session_id", ASCENDING), ("task_id", ASCENDING)], unique=True
    )
    await proposals_col().create_index(
        [("session_id", ASCENDING), ("file_name", ASCENDING), ("status", ASCENDING)]
    )
