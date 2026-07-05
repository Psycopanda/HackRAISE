"""Identifier helpers: access codes, task ids and ObjectId coercion."""

import secrets
import uuid

from bson import ObjectId

# Ambiguous characters (0/O, 1/I/L) are excluded for human-friendly codes.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_access_code(length: int = 6) -> str:
    """Return a random, human-readable session access code."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))


def new_task_id() -> str:
    """Return a unique task identifier used inside the shared context."""
    return uuid.uuid4().hex


def to_object_id(value) -> ObjectId:
    """Coerce a value to an ObjectId, accepting either str or ObjectId."""
    if isinstance(value, ObjectId):
        return value
    return ObjectId(str(value))
