"""WebSocket endpoints — all real-time traffic flows through here.

Two endpoints, matching the two phases of a session's life:

* ``/ws/system/{session_id}/{user_id}``  — initialisation phase. The creator
  chats with the temporary system agent until the master context is produced.
* ``/ws/session/{session_id}/{user_id}`` — collaborative phase. Carries chat
  with the personal agent AND collaborative text edits, plus presence and
  live context/file broadcasts.
"""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agents import personal_agent, system_agent
from app.services import (
    context_service,
    file_service,
    lock_service,
    modification_service,
    proposal_service,
    session_service,
    user_service,
)
from app.utils.serialization import serialize
from app.websockets.connection_manager import manager

logger = logging.getLogger("vibecode.ws")
router = APIRouter()

# Strong references to in-flight agent turns so they are not garbage collected
# before completion (they run detached from the receive loop, see below).
_BACKGROUND_TASKS: set = set()


# --- Initialisation phase -------------------------------------------------

@router.websocket("/ws/system/{session_id}/{user_id}")
async def system_ws(websocket: WebSocket, session_id: str, user_id: str):
    session = await session_service.get_session(session_id)
    if session is None:
        await websocket.close(code=4404)
        return

    await manager.connect(session_id, user_id, websocket)
    await manager.send_personal(
        session_id,
        user_id,
        {
            "type": "system_message",
            "content": (
                "Bonjour ! Je vais t'aider à initialiser ta session VibeCode. "
                "Peux-tu me décrire en quelques mots l'objectif de ce projet ?"
            ),
        },
    )

    try:
        while True:
            try:
                data = await websocket.receive_json()
            except (ValueError, KeyError):
                continue

            if data.get("type") == "ping":
                await manager.send_personal(session_id, user_id, {"type": "pong"})
                continue
            if data.get("type") != "chat":
                continue

            content = (data.get("content") or "").strip()
            if not content:
                continue

            await manager.send_personal(
                session_id, user_id, {"type": "agent_status", "status": "thinking"}
            )
            try:
                result = await system_agent.respond(session_id, user_id, content)
            except Exception:
                logger.exception("system agent failure")
                await manager.send_personal(
                    session_id,
                    user_id,
                    {"type": "error", "message": "Erreur interne de l'agent système."},
                )
                continue

            if result["kind"] == "error":
                await manager.send_personal(
                    session_id, user_id, {"type": "error", "message": result["content"]}
                )
            elif result["kind"] == "message":
                await manager.send_personal(
                    session_id,
                    user_id,
                    {"type": "system_message", "content": result["content"]},
                )
            elif result["kind"] == "master_context":
                access_code = await session_service.finalize_session(
                    session_id, result["master_context"]
                )
                await manager.send_personal(
                    session_id,
                    user_id,
                    {"type": "system_message", "content": result["content"]},
                )
                await manager.send_personal(
                    session_id,
                    user_id,
                    {
                        "type": "master_context_ready",
                        "access_code": access_code,
                        "master_context": result["master_context"],
                        "session_ws_url": f"/ws/session/{session_id}/{user_id}",
                    },
                )
                break
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(session_id, user_id)


# --- Collaborative phase --------------------------------------------------

@router.websocket("/ws/session/{session_id}/{user_id}")
async def session_ws(websocket: WebSocket, session_id: str, user_id: str):
    session = await session_service.get_session(session_id)
    user = await user_service.get_user(session_id, user_id)
    if session is None or user is None or session.get("status") != "active":
        await websocket.close(code=4404)
        return

    await manager.connect(session_id, user_id, websocket)
    await user_service.set_connected(session_id, user_id, True)
    logger.info(
        "session WS connected: session=%s user=%s (%s)",
        session_id,
        user_id,
        user.get("display_name"),
    )

    # Send the full initial snapshot to the newcomer.
    context = await context_service.get_shared_context(session_id)
    files = await file_service.list_files(session_id)
    locks = await lock_service.list_active_locks(session_id)
    users = await user_service.list_users(session_id)
    pending = await proposal_service.list_pending_for_user(session_id, user_id)
    await manager.send_personal(
        session_id,
        user_id,
        {
            "type": "snapshot",
            "access_code": session.get("access_code"),
            "title": session.get("title"),
            "context": serialize(context),
            "files": [file_service.public_file(f) for f in files],
            "active_locks": serialize(locks),
            "users": serialize(users),
            "proposals": [proposal_service.public_proposal(p) for p in pending],
        },
    )
    await manager.broadcast(
        session_id,
        {
            "type": "user_joined",
            "user": {"user_id": user_id, "display_name": user.get("display_name")},
        },
        exclude_user=user_id,
    )

    try:
        while True:
            try:
                data = await websocket.receive_json()
            except (ValueError, KeyError):
                continue

            msg_type = data.get("type")
            if msg_type == "ping":
                await manager.send_personal(session_id, user_id, {"type": "pong"})
            elif msg_type == "chat":
                # Run the (potentially slow) agent turn concurrently so the
                # user's own text edits are never queued behind it: the editor
                # is never blocked while the agent thinks or modifies.
                task = asyncio.create_task(
                    _safe_handle(_handle_chat, session, user, data)
                )
                _BACKGROUND_TASKS.add(task)
                task.add_done_callback(_BACKGROUND_TASKS.discard)
            elif msg_type == "text_edit":
                await _safe_handle(_handle_text_edit, session, user, data)
            elif msg_type == "create_file":
                await _safe_handle(_handle_create_file, session, user, data)
            elif msg_type == "create_folder":
                await _safe_handle(_handle_create_folder, session, user, data)
            elif msg_type == "delete_path":
                await _safe_handle(_handle_delete_path, session, user, data)
            elif msg_type == "resolve_proposal":
                await _safe_handle(_handle_resolve_proposal, session, user, data)
            elif msg_type == "save_to_context":
                await _safe_handle(_handle_save_to_context, session, user, data)
            elif msg_type == "cursor":
                await manager.broadcast(
                    session_id,
                    {
                        "type": "cursor",
                        "user_id": user_id,
                        "file_id": data.get("file_id"),
                        "position": data.get("position"),
                    },
                    exclude_user=user_id,
                )
    except WebSocketDisconnect:
        pass
    finally:
        await user_service.set_connected(session_id, user_id, False)
        await manager.disconnect(session_id, user_id)
        await manager.broadcast(session_id, {"type": "user_left", "user_id": user_id})
        logger.info("session WS disconnected: session=%s user=%s", session_id, user_id)


# --- Message handlers -----------------------------------------------------

async def _safe_handle(handler, session, user, data) -> None:
    session_id = str(session["_id"])
    user_id = str(user["_id"])
    try:
        await handler(session, user, data)
    except Exception:
        logger.exception("session handler failure")
        await manager.send_personal(
            session_id, user_id, {"type": "error", "message": "Erreur interne du serveur."}
        )


async def _handle_chat(session: dict, user: dict, data: dict) -> None:
    session_id = str(session["_id"])
    user_id = str(user["_id"])
    content = (data.get("content") or "").strip()
    if not content:
        return

    await manager.send_personal(
        session_id, user_id, {"type": "agent_status", "status": "thinking"}
    )

    state = {"started": False}

    async def on_delta(piece: str) -> None:
        if not state["started"]:
            await manager.send_personal(session_id, user_id, {"type": "agent_message_start"})
            state["started"] = True
        await manager.send_personal(
            session_id, user_id, {"type": "agent_message_delta", "content": piece}
        )

    result = await personal_agent.handle_message(session, user, content, on_delta)

    if state["started"]:
        await manager.send_personal(session_id, user_id, {"type": "agent_message_end"})

    kind = result["kind"]
    if kind == "error":
        await manager.send_personal(
            session_id, user_id, {"type": "error", "message": result["content"]}
        )
        await manager.send_personal(
            session_id, user_id, {"type": "agent_status", "status": "idle"}
        )
        return

    if kind == "message":
        # If the stream produced nothing, deliver the whole message at once.
        if not state["started"] and result.get("content"):
            await manager.send_personal(
                session_id, user_id, {"type": "agent_message", "content": result["content"]}
            )
        await manager.send_personal(
            session_id, user_id, {"type": "agent_status", "status": "idle"}
        )
        return

    # kind == "modification" or "deletion" -> build a proposal (no code streamed).
    await manager.send_personal(
        session_id, user_id, {"type": "agent_status", "status": "modifying"}
    )
    if kind == "deletion":
        outcome = await modification_service.run_deletion(session, user, result["args"])
    else:
        outcome = await modification_service.run_modification(session, user, result["args"])

    # The task (pending_review) now appears in the shared context for everyone.
    refreshed = await context_service.get_shared_context(session["_id"])
    await manager.broadcast(
        session_id, {"type": "context_update", "context": serialize(refreshed)}
    )

    status = outcome.get("status")
    if status == "proposed":
        await manager.send_personal(session_id, user_id, outcome["proposal"])
        await manager.send_personal(
            session_id, user_id, {"type": "agent_message", "content": _proposal_message(outcome)}
        )
    elif status in ("busy", "noop"):
        await manager.send_personal(
            session_id, user_id, {"type": "agent_message", "content": outcome["message"]}
        )
    else:
        await manager.send_personal(
            session_id, user_id, {"type": "error", "message": outcome.get("message", "Erreur.")}
        )

    await manager.send_personal(
        session_id, user_id, {"type": "agent_status", "status": "idle"}
    )


async def _handle_text_edit(session: dict, user: dict, data: dict) -> None:
    """Human-driven collaborative edit, guarded by optimistic concurrency."""
    session_id = str(session["_id"])
    user_id = str(user["_id"])
    file_id = data.get("file_id")
    new_content = data.get("content")
    base_version = data.get("base_version")

    if file_id is None or new_content is None or base_version is None:
        await manager.send_personal(
            session_id,
            user_id,
            {"type": "error", "message": "Requête d'édition invalide (champs manquants)."},
        )
        return

    updated = await file_service.update_file_content(
        session_id, file_id, new_content, base_version, user["_id"]
    )
    if updated is None:
        # Version conflict: return the authoritative content for the client to rebase.
        current = await file_service.get_file(session_id, file_id)
        await manager.send_personal(
            session_id,
            user_id,
            {"type": "edit_conflict", "file": file_service.public_file(current)},
        )
        return

    # Acknowledge the author's own write so its editor advances its base version
    # without ever triggering a conflict while the user keeps typing.
    await manager.send_personal(
        session_id,
        user_id,
        {"type": "edit_ack", "file_id": str(updated["_id"]), "version": updated["version"]},
    )
    await manager.broadcast(
        session_id,
        {
            "type": "file_update",
            "file": file_service.public_file(updated),
            "sender": user.get("display_name"),
        },
        exclude_user=user_id,
    )


async def _handle_create_file(session: dict, user: dict, data: dict) -> None:
    """Create a new empty file in the session and announce it to everyone."""
    session_id = str(session["_id"])
    user_id = str(user["_id"])
    name = (data.get("name") or "").strip().lstrip("/")
    if not name or len(name) > 120:
        await manager.send_personal(
            session_id, user_id, {"type": "error", "message": "Nom de fichier invalide."}
        )
        return
    existing = await file_service.get_file_by_name(session_id, name)
    if existing is not None:
        await manager.send_personal(
            session_id, user_id, {"type": "error", "message": f"« {name} » existe déjà."}
        )
        return
    file_type, language = file_service.infer_type(name)
    created = await file_service.create_file(session_id, name, file_type, "", language)
    logger.info("file created: %s session=%s by=%s", name, session_id, user_id)
    await manager.broadcast(
        session_id,
        {"type": "file_created", "file": file_service.public_file(created), "by": user_id},
    )
    await _record_structure_change(session, user, "create_file", [name])


async def _handle_save_to_context(session: dict, user: dict, data: dict) -> None:
    """Record a user's manual file edits into the shared context."""
    session_id = str(session["_id"])
    user_id = str(user["_id"])
    file_id = data.get("file_id")
    note = (data.get("note") or "").strip()
    file = await file_service.get_file(session_id, file_id) if file_id else None
    file_name = file["name"] if file else (data.get("name") or "document")
    pseudo = user.get("display_name") or "Un collaborateur"
    entry = await context_service.record_manual_save(
        session_id, user["_id"], file_name, pseudo, note
    )
    logger.info("manual save to context: %s by=%s session=%s", file_name, pseudo, session_id)
    refreshed = await context_service.get_shared_context(session_id)
    await manager.broadcast(
        session_id, {"type": "context_update", "context": serialize(refreshed)}
    )
    await manager.send_personal(
        session_id,
        user_id,
        {"type": "save_ack", "file_id": file_id, "summary": entry.get("summary")},
    )


async def _record_structure_change(session: dict, user: dict, action: str, targets: list) -> None:
    """Log a manual file/folder add or delete into the shared context (so all
    agents stay aware of the current structure), then broadcast the update."""
    session_id = str(session["_id"])
    pseudo = user.get("display_name") or "Un collaborateur"
    await context_service.record_file_event(
        session_id, user["_id"], action, targets, pseudo
    )
    refreshed = await context_service.get_shared_context(session_id)
    await manager.broadcast(
        session_id, {"type": "context_update", "context": serialize(refreshed)}
    )


def _proposal_message(outcome: dict) -> str:
    kind = outcome.get("kind")
    name = outcome.get("file_name")
    verb = {
        "delete": "une suppression",
        "create": "la création",
    }.get(kind, "une modification")
    return (
        f"J'ai préparé {verb} de « {name} ». Examine le diff à gauche puis "
        "clique sur Appliquer ou Rejeter."
    )


async def _handle_resolve_proposal(session: dict, user: dict, data: dict) -> None:
    session_id = str(session["_id"])
    user_id = str(user["_id"])
    task_id = data.get("task_id")
    decision = data.get("decision")
    if not task_id or decision not in ("apply", "reject"):
        await manager.send_personal(
            session_id, user_id, {"type": "error", "message": "Requête de résolution invalide."}
        )
        return

    outcome = await modification_service.resolve_proposal(session, user, task_id, decision)
    status = outcome.get("status")

    if status == "gone":
        await manager.send_personal(
            session_id, user_id,
            {"type": "proposal_resolved", "task_id": task_id, "decision": "gone"},
        )
        return

    for event in outcome.get("events", []):
        await manager.broadcast(session_id, event)

    refreshed = await context_service.get_shared_context(session["_id"])
    await manager.broadcast(
        session_id, {"type": "context_update", "context": serialize(refreshed)}
    )

    await manager.send_personal(
        session_id, user_id,
        {
            "type": "proposal_resolved",
            "task_id": task_id,
            "decision": status,
            "file_name": outcome.get("file_name"),
        },
    )
    if status == "applied":
        await manager.send_personal(
            session_id, user_id,
            {"type": "agent_message", "content": f"Proposition appliquée : {outcome.get('summary')}"},
        )
    elif status == "rejected":
        await manager.send_personal(
            session_id, user_id,
            {"type": "agent_message", "content": f"Proposition rejetée ({outcome.get('file_name')})."},
        )


async def _handle_create_folder(session: dict, user: dict, data: dict) -> None:
    """Create a folder by adding a hidden ``.keep`` placeholder file."""
    session_id = str(session["_id"])
    user_id = str(user["_id"])
    name = (data.get("name") or "").strip().strip("/")
    if not name or len(name) > 120:
        await manager.send_personal(
            session_id, user_id, {"type": "error", "message": "Nom de dossier invalide."}
        )
        return
    placeholder = f"{name}/.keep"
    if await file_service.get_file_by_name(session_id, placeholder) is not None:
        return
    created = await file_service.create_file(session_id, placeholder, "text", "", None)
    logger.info("folder created: %s session=%s by=%s", name, session_id, user_id)
    await manager.broadcast(
        session_id,
        {"type": "file_created", "file": file_service.public_file(created), "by": user_id},
    )
    await _record_structure_change(session, user, "create_folder", [name])


async def _handle_delete_path(session: dict, user: dict, data: dict) -> None:
    """User-initiated immediate deletion of a file or folder (cascade)."""
    session_id = str(session["_id"])
    user_id = str(user["_id"])
    path = (data.get("path") or "").strip().strip("/")
    if not path:
        await manager.send_personal(
            session_id, user_id, {"type": "error", "message": "Chemin de suppression manquant."}
        )
        return
    _, files = await file_service.resolve_path_targets(session_id, path)
    if not files:
        await manager.send_personal(
            session_id, user_id, {"type": "error", "message": f"« {path} » introuvable."}
        )
        return
    names = [f["name"] for f in files]
    await file_service.delete_files_by_names(session_id, names)
    logger.info("user deleted path: %s (%d files) session=%s", path, len(names), session_id)
    for f in files:
        await manager.broadcast(
            session_id, {"type": "file_deleted", "file_id": str(f["_id"]), "name": f["name"]}
        )
    await _record_structure_change(session, user, "delete", [path])
