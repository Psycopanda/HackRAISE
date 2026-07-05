"""File CRUD with optimistic concurrency control (OCC).

Every file carries a monotonically increasing ``version``. Writes only
succeed when the caller's expected version matches, guaranteeing that
concurrent writers cannot silently overwrite each other.
"""

import re
from typing import Optional

from pymongo import ReturnDocument

from app.database import files_col
from app.utils.ids import to_object_id
from app.utils.time import utcnow

DEFAULT_DOCUMENT_NAME = "document.txt"


async def create_file(
    session_id,
    name: str,
    file_type: str = "text",
    content: str = "",
    language: Optional[str] = None,
) -> dict:
    now = utcnow()
    document = {
        "session_id": to_object_id(session_id),
        "name": name,
        "type": file_type,
        "language": language,
        "content": content,
        "version": 1,
        "last_modified_by": None,
        "created_at": now,
        "updated_at": now,
    }
    result = await files_col().insert_one(document)
    document["_id"] = result.inserted_id
    return document


async def create_default_document(session_id) -> dict:
    """Create the initial collaborative text document for a new session."""
    return await create_file(session_id, DEFAULT_DOCUMENT_NAME, "text", "", None)


async def get_file(session_id, file_id) -> Optional[dict]:
    return await files_col().find_one(
        {"_id": to_object_id(file_id), "session_id": to_object_id(session_id)}
    )


async def get_file_by_name(session_id, name: str) -> Optional[dict]:
    return await files_col().find_one(
        {"session_id": to_object_id(session_id), "name": name}
    )


async def list_files(session_id) -> list[dict]:
    cursor = files_col().find({"session_id": to_object_id(session_id)})
    return [doc async for doc in cursor]


async def update_file_content(
    session_id, file_id, new_content: str, expected_version: int, user_id
) -> Optional[dict]:
    """Optimistic update. Returns the new document, or ``None`` on version conflict."""
    now = utcnow()
    return await files_col().find_one_and_update(
        {
            "_id": to_object_id(file_id),
            "session_id": to_object_id(session_id),
            "version": expected_version,
        },
        {
            "$set": {
                "content": new_content,
                "last_modified_by": to_object_id(user_id),
                "updated_at": now,
            },
            "$inc": {"version": 1},
        },
        return_document=ReturnDocument.AFTER,
    )


def public_file(document: Optional[dict]) -> Optional[dict]:
    """Reduce a file document to the fields the frontend needs."""
    if document is None:
        return None
    return {
        "file_id": str(document["_id"]),
        "name": document["name"],
        "type": document.get("type"),
        "language": document.get("language"),
        "content": document.get("content", ""),
        "version": document.get("version"),
    }


_MARKDOWN_EXT = {".md", ".markdown"}
_CODE_EXT = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".ts": "typescript",
    ".jsx": "javascript", ".tsx": "typescript", ".html": "html", ".css": "css",
    ".json": "json", ".java": "java", ".c": "c", ".h": "c", ".cpp": "cpp",
    ".cs": "csharp", ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".sh": "bash", ".sql": "sql", ".yml": "yaml", ".yaml": "yaml", ".xml": "xml",
}


def infer_type(name: str) -> tuple[str, Optional[str]]:
    """Infer (type, language) from a file name's extension."""
    lower = name.lower()
    dot = lower.rfind(".")
    ext = lower[dot:] if dot != -1 else ""
    if ext in _MARKDOWN_EXT:
        return "markdown", "markdown"
    if ext in _CODE_EXT:
        return "code", _CODE_EXT[ext]
    return "text", None


async def delete_file(session_id, file_id) -> bool:
    result = await files_col().delete_one(
        {"_id": to_object_id(file_id), "session_id": to_object_id(session_id)}
    )
    return result.deleted_count > 0


async def delete_files_by_names(session_id, names: list) -> int:
    if not names:
        return 0
    result = await files_col().delete_many(
        {"session_id": to_object_id(session_id), "name": {"$in": names}}
    )
    return result.deleted_count


async def resolve_path_targets(session_id, path: str):
    """Return (is_folder, [file docs]) for a file or folder path.

    A folder is any path with files nested under ``path/``. Its nested files
    (and an optional ``.keep`` placeholder) are returned for cascade deletion.
    """
    path = path.strip().strip("/")
    exact = await get_file_by_name(session_id, path)
    prefix = path + "/"
    cursor = files_col().find(
        {
            "session_id": to_object_id(session_id),
            "name": {"$regex": "^" + re.escape(prefix)},
        }
    )
    nested = [doc async for doc in cursor]
    if nested:
        files = list(nested)
        if exact is not None:
            files.append(exact)
        return True, files
    if exact is not None:
        return False, [exact]
    return False, []


def split_path(name: str) -> list[str]:
    """Split a ``/``-delimited file name into non-empty path parts."""
    return [part for part in str(name).strip().split("/") if part]


def build_file_tree(files: list) -> dict:
    """Build a nested dict tree from a list of file documents.

    Names use ``/`` as a separator. Internal ``.keep`` folder placeholders are
    hidden, but the folders they materialise are kept so empty folders still
    appear. A file leaf is represented as ``None``; a folder as a nested dict.
    """
    root: dict = {}
    for document in files:
        parts = split_path(document.get("name", ""))
        if not parts:
            continue
        node = root
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        leaf = parts[-1]
        if leaf == ".keep":
            continue  # folder placeholder: its parent is already materialised
        node.setdefault(leaf, None)  # None marks a file leaf
    return root


def format_file_tree(files: list) -> str:
    """Render the session's files as an indented tree (arborescence).

    Returns a human/agent-readable outline of the current structure.
    """
    root = build_file_tree(files)

    if not root:
        return "(aucun fichier)"

    lines: list[str] = []

    def walk(node: dict, depth: int) -> None:
        # Folders (dict values) first, then files, each alphabetically.
        entries = sorted(node.items(), key=lambda kv: (kv[1] is None, kv[0].lower()))
        for key, child in entries:
            indent = "  " * depth
            if isinstance(child, dict):
                lines.append(f"{indent}- {key}/")
                walk(child, depth + 1)
            else:
                lines.append(f"{indent}- {key}")

    walk(root, 0)
    return "\n".join(lines)
