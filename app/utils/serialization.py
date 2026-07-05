"""Recursively convert MongoDB documents into JSON-serialisable structures."""

from datetime import datetime

from bson import ObjectId


def serialize(document):
    """Convert ObjectId -> str and datetime -> ISO string, recursively."""
    if document is None:
        return None
    if isinstance(document, list):
        return [serialize(item) for item in document]
    if isinstance(document, dict):
        return {key: serialize(value) for key, value in document.items()}
    if isinstance(document, ObjectId):
        return str(document)
    if isinstance(document, datetime):
        return document.isoformat()
    return document
