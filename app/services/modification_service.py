"""Agent change workflow — proposals with human approval.

Instead of applying changes directly, an agent action (modify / create /
delete) produces a *proposal* shown only to the requesting user as a diff. The
task stays ``pending_review`` in the shared context and nothing is broadcast
until the user applies it. On apply, the change is persisted and broadcast to
everyone; on reject, it is discarded.
"""

import logging

from app.agents.prompts import MODIFICATION_SYSTEM_PROMPT
from app.services import context_service, file_service, lock_service, proposal_service
from app.services.mistral_service import MistralError, get_mistral_service
from app.utils.ids import new_task_id
from app.utils.time import utcnow

logger = logging.getLogger("vibecode.modification")


def _agent_id(user_id) -> str:
    return f"agent:{user_id}"


def _busy(target_name: str) -> dict:
    return {
        "status": "busy",
        "file_name": target_name,
        "message": (
            f"« {target_name} » fait déjà l'objet d'une proposition ou d'une "
            "action en cours. Réessaie une fois celle-ci résolue."
        ),
    }


def _recently_deleted(context: dict, target: str) -> bool:
    """Whether ``target`` appears as an already-deleted path in the shared context.

    Manual deletions are logged in ``completed_tasks`` (kind ``delete``); this
    lets the agent recognise a file a collaborator removed and answer gracefully.
    """
    if not context:
        return False
    for task in reversed(context.get("completed_tasks", []) or []):
        if task.get("kind") != "delete":
            continue
        for touched in task.get("files_touched") or []:
            name = str(touched)
            if name == target or name.startswith(target + "/") or target.startswith(name + "/"):
                return True
    return False


# --- Propose: modify / create --------------------------------------------

async def run_modification(session: dict, user: dict, args: dict) -> dict:
    session_id = session["_id"]
    user_id = user["_id"]
    agent_id = _agent_id(user_id)

    target_name = (args.get("target") or file_service.DEFAULT_DOCUMENT_NAME).strip()
    if not target_name:
        target_name = file_service.DEFAULT_DOCUMENT_NAME
    task_description = args.get("task_description") or "Modification du document"
    instructions = args.get("instructions") or task_description

    if await proposal_service.has_pending_for_name(session_id, target_name):
        return _busy(target_name)

    resource_id = f"path:{target_name}"
    lock = await lock_service.acquire_lock(
        session_id, resource_id, user_id, agent_id, task_description
    )
    if lock is None:
        return _busy(target_name)

    task_id = new_task_id()
    try:
        target_file = await file_service.get_file_by_name(session_id, target_name)
        kind = "modify" if target_file is not None else "create"
        previous = target_file.get("content", "") if target_file else ""
        base_version = target_file.get("version") if target_file else None
        file_id = str(target_file["_id"]) if target_file else None

        await context_service.register_intention(
            session_id,
            {
                "task_id": task_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "resource_id": resource_id,
                "target": target_name,
                "description": task_description,
                "status": "pending_review",
                "kind": kind,
                "started_at": utcnow(),
            },
        )

        context = await context_service.get_shared_context(session_id)
        file_hint = target_file or {
            "name": target_name,
            "type": file_service.infer_type(target_name)[0],
            "content": "",
        }
        new_content = await _generate_content(
            file_hint, instructions, task_description, context
        )

        await proposal_service.create_proposal(
            session_id, task_id, user_id, agent_id, kind, target_name,
            file_id, previous, new_content, base_version,
        )
        logger.info(
            "proposal created (%s): %s task=%s session=%s",
            kind, target_name, task_id, session_id,
        )
        proposal = await proposal_service.get_proposal(session_id, task_id)
        return {
            "status": "proposed",
            "kind": kind,
            "file_name": target_name,
            "task_id": task_id,
            "proposal": {
                "type": "change_proposal",
                **proposal_service.public_proposal(proposal),
            },
        }
    except MistralError as exc:
        logger.warning("proposal generation error: %s task=%s: %s", target_name, task_id, exc)
        await context_service.abort_task(session_id, task_id)
        return {"status": "error", "file_name": target_name, "message": str(exc)}
    finally:
        await lock_service.release_lock(session_id, resource_id, user_id)


# --- Propose: delete ------------------------------------------------------

async def run_deletion(session: dict, user: dict, args: dict) -> dict:
    session_id = session["_id"]
    user_id = user["_id"]
    agent_id = _agent_id(user_id)

    target = (args.get("target") or "").strip().strip("/")
    if not target:
        return {"status": "error", "file_name": "", "message": "Cible de suppression manquante."}

    if await proposal_service.has_pending_for_name(session_id, target):
        return _busy(target)

    resource_id = f"path:{target}"
    lock = await lock_service.acquire_lock(
        session_id, resource_id, user_id, agent_id, f"Suppression de {target}"
    )
    if lock is None:
        return _busy(target)

    task_id = new_task_id()
    try:
        is_folder, files = await file_service.resolve_path_targets(session_id, target)
        if not files:
            # The target no longer exists. Rather than raising an error, consult
            # the shared context: if it was already deleted (e.g. manually by a
            # collaborator), acknowledge it gracefully instead of failing.
            context = await context_service.get_shared_context(session_id)
            if _recently_deleted(context, target):
                message = (
                    f"« {target} » a déjà été supprimé précédemment ; "
                    "il n'y a plus rien à supprimer."
                )
            else:
                message = (
                    f"Aucun élément nommé « {target} » n'existe dans le projet ; "
                    "il n'y a rien à supprimer."
                )
            return {"status": "noop", "file_name": target, "message": message}

        entries = [
            {"name": f["name"], "file_id": str(f["_id"]), "previous_content": f.get("content", "")}
            for f in files
        ]
        description = f"Suppression de {'dossier ' if is_folder else ''}{target}"
        await context_service.register_intention(
            session_id,
            {
                "task_id": task_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "resource_id": resource_id,
                "target": target,
                "description": description,
                "status": "pending_review",
                "kind": "delete",
                "started_at": utcnow(),
            },
        )
        await proposal_service.create_proposal(
            session_id, task_id, user_id, agent_id, "delete", target,
            None, None, None, None, files=entries,
        )
        logger.info(
            "delete proposal created: %s (%d files) task=%s session=%s",
            target, len(entries), task_id, session_id,
        )
        proposal = await proposal_service.get_proposal(session_id, task_id)
        return {
            "status": "proposed",
            "kind": "delete",
            "file_name": target,
            "task_id": task_id,
            "proposal": {
                "type": "change_proposal",
                **proposal_service.public_proposal(proposal),
            },
        }
    finally:
        await lock_service.release_lock(session_id, resource_id, user_id)


# --- Resolve: apply / reject ---------------------------------------------

async def resolve_proposal(session: dict, user: dict, task_id: str, decision: str) -> dict:
    session_id = session["_id"]
    user_id = user["_id"]

    proposal = await proposal_service.get_proposal(session_id, task_id)
    if proposal is None or proposal.get("status") != "pending":
        return {"status": "gone", "task_id": task_id}

    file_name = proposal["file_name"]

    if decision == "reject":
        await proposal_service.set_status(session_id, task_id, "rejected")
        await context_service.abort_task(session_id, task_id)
        logger.info("proposal rejected: %s task=%s session=%s", file_name, task_id, session_id)
        return {"status": "rejected", "task_id": task_id, "file_name": file_name, "events": []}

    kind = proposal["kind"]
    events: list[dict] = []
    if kind == "delete":
        names = [entry["name"] for entry in proposal.get("files", [])]
        await file_service.delete_files_by_names(session_id, names)
        for entry in proposal.get("files", []):
            events.append(
                {"type": "file_deleted", "file_id": entry.get("file_id"), "name": entry["name"]}
            )
        summary = f"Suppression de {file_name}"
    elif kind == "create":
        file_type, language = file_service.infer_type(file_name)
        created = await file_service.create_file(
            session_id, file_name, file_type, proposal.get("proposed_content") or "", language
        )
        events.append(
            {"type": "file_created", "file": file_service.public_file(created), "by": str(user_id)}
        )
        summary = f"Création de {file_name}"
    else:  # modify
        updated = await _apply_proposed(
            session_id, proposal["file_id"], proposal.get("proposed_content") or "", user_id
        )
        events.append({"type": "file_update", "file": file_service.public_file(updated)})
        summary = f"Modification de {file_name}"

    await proposal_service.set_status(session_id, task_id, "applied")
    await context_service.complete_task(
        session_id, task_id, summary, [file_name], proposal.get("description") or summary, user_id
    )
    logger.info("proposal applied (%s): %s task=%s session=%s", kind, file_name, task_id, session_id)
    return {
        "status": "applied",
        "task_id": task_id,
        "file_name": file_name,
        "summary": summary,
        "events": events,
    }


# --- Helpers --------------------------------------------------------------

async def _generate_content(file_hint, instructions, task_description, context) -> str:
    mistral = get_mistral_service()
    master = (context or {}).get("master_context", {})
    objective = master.get("objective", "n/d")
    current = file_hint.get("content", "")
    user_prompt = (
        f"Objectif global du projet: {objective}\n\n"
        f"Fichier ciblé: {file_hint.get('name')} (type: {file_hint.get('type')})\n"
        f"Tâche: {task_description}\n"
        f"Instructions détaillées: {instructions}\n\n"
        f"--- CONTENU ACTUEL ---\n{current}\n--- FIN DU CONTENU ---\n\n"
        "Renvoie UNIQUEMENT le nouveau contenu complet du fichier, "
        "sans explication ni balise de code."
    )
    messages = [
        {"role": "system", "content": MODIFICATION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    return await mistral.generate_text(messages, temperature=0.4)


async def _apply_proposed(session_id, file_id, new_content, user_id, attempts: int = 5):
    """Apply approved content to an existing file, adopting the current version."""
    for _ in range(attempts):
        fresh = await file_service.get_file(session_id, file_id)
        if fresh is None:
            raise RuntimeError("Le fichier ciblé n'existe plus.")
        updated = await file_service.update_file_content(
            session_id, file_id, new_content, fresh["version"], user_id
        )
        if updated is not None:
            return updated
    raise RuntimeError("Conflit de version persistant lors de l'application.")
