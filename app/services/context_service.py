"""Shared context ("master context") — the single source of truth.

All coordination between agents happens here. Writes use MongoDB atomic
operators (``$push`` / ``$pull`` / ``$inc``) so concurrent agents never race
on read-modify-write cycles.
"""

from typing import Optional

from app.database import context_col
from app.utils.ids import new_task_id, to_object_id
from app.utils.time import utcnow


async def create_shared_context(session_id, master_context: dict) -> dict:
    now = utcnow()
    document = {
        "session_id": to_object_id(session_id),
        "master_context": master_context,
        "state_summary": "Session initialisée. Aucun travail réalisé pour le moment.",
        "active_tasks": [],
        "completed_tasks": [],
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    result = await context_col().insert_one(document)
    document["_id"] = result.inserted_id
    return document


async def get_shared_context(session_id) -> Optional[dict]:
    return await context_col().find_one({"session_id": to_object_id(session_id)})


async def register_intention(session_id, intention: dict) -> None:
    """Atomically append an in-progress task (signal to other agents)."""
    await context_col().update_one(
        {"session_id": to_object_id(session_id)},
        {
            "$push": {"active_tasks": intention},
            "$set": {"updated_at": utcnow()},
            "$inc": {"version": 1},
        },
    )


async def complete_task(
    session_id,
    task_id: str,
    summary: str,
    files_touched: list[str],
    description: Optional[str] = None,
    user_id=None,
) -> dict:
    """Atomically move a task from active -> completed with a concise summary."""
    now = utcnow()
    completed_entry = {
        "task_id": task_id,
        "user_id": to_object_id(user_id) if user_id else None,
        "description": description,
        "summary": summary,
        "files_touched": files_touched,
        "completed_at": now,
    }
    await context_col().update_one(
        {"session_id": to_object_id(session_id)},
        {
            "$pull": {"active_tasks": {"task_id": task_id}},
            "$push": {"completed_tasks": completed_entry},
            "$set": {"state_summary": summary, "updated_at": now},
            "$inc": {"version": 1},
        },
    )
    return completed_entry


async def abort_task(session_id, task_id: str) -> None:
    """Remove an in-progress task without recording a completion (on failure)."""
    await context_col().update_one(
        {"session_id": to_object_id(session_id)},
        {
            "$pull": {"active_tasks": {"task_id": task_id}},
            "$set": {"updated_at": utcnow()},
            "$inc": {"version": 1},
        },
    )


async def update_state_summary(session_id, summary: str) -> None:
    await context_col().update_one(
        {"session_id": to_object_id(session_id)},
        {"$set": {"state_summary": summary, "updated_at": utcnow()}, "$inc": {"version": 1}},
    )


async def record_manual_save(
    session_id, user_id, file_name: str, pseudo: str, note: Optional[str] = None
) -> dict:
    """Record a user's *manual* file edits into the shared context.

    Direct edits are already persisted to the file document; this makes the
    change visible to the other agents by logging it in ``completed_tasks``.
    """
    now = utcnow()
    summary = f"{pseudo} a enregistré des modifications manuelles sur « {file_name} »"
    if note:
        summary += f" — {note}"
    entry = {
        "task_id": new_task_id(),
        "user_id": to_object_id(user_id) if user_id else None,
        "kind": "manual_edit",
        "description": f"Édition manuelle de {file_name}",
        "summary": summary,
        "files_touched": [file_name],
        "completed_at": now,
    }
    await context_col().update_one(
        {"session_id": to_object_id(session_id)},
        {
            "$push": {"completed_tasks": entry},
            "$set": {"state_summary": summary, "updated_at": now},
            "$inc": {"version": 1},
        },
    )
    return entry


async def record_file_event(
    session_id, user_id, action: str, targets: list, pseudo: str
) -> dict:
    """Record a manual file/folder creation or deletion in the shared context.

    Direct structure changes (adding or removing files/folders outside the agent
    workflow) are logged here so every agent stays aware of the current project
    structure and its recent evolution.
    """
    now = utcnow()
    names = ", ".join(f"« {t} »" for t in targets) if targets else "(rien)"
    verb = {
        "create_file": "a créé le fichier",
        "create_folder": "a créé le dossier",
        "delete": "a supprimé",
    }.get(action, "a modifié la structure")
    summary = f"{pseudo} {verb} {names}"
    entry = {
        "task_id": new_task_id(),
        "user_id": to_object_id(user_id) if user_id else None,
        "kind": action,
        "description": summary,
        "summary": summary,
        "files_touched": list(targets),
        "completed_at": now,
    }
    await context_col().update_one(
        {"session_id": to_object_id(session_id)},
        {
            "$push": {"completed_tasks": entry},
            "$set": {"state_summary": summary, "updated_at": now},
            "$inc": {"version": 1},
        },
    )
    return entry
