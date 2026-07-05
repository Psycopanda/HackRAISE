"""Personal agent: converses with a user (streaming) and detects intent.

Conditional logic via Mistral tool calling:
- ``request_modification`` -> the user wants to create/modify a file.
- ``request_deletion``     -> the user wants to delete a file/folder.
- otherwise                -> a normal conversation, streamed token by token.
"""

import json

from app.agents.prompts import (
    PERSONAL_AGENT_PROMPT,
    REQUEST_DELETION_TOOL,
    REQUEST_MODIFICATION_TOOL,
)
from app.services import context_service, file_service, message_service
from app.services.mistral_service import MistralError, get_mistral_service

SCOPE = "personal_agent"


def _agent_id(user_id) -> str:
    return f"agent:{user_id}"


async def handle_message(session: dict, user: dict, user_message: str, on_delta=None) -> dict:
    """Process one chat turn, streaming assistant text through ``on_delta``.

    Returns one of:
      * {"kind": "message", "content": str}          (already streamed)
      * {"kind": "modification", "preface", "args"}
      * {"kind": "deletion", "preface", "args"}
      * {"kind": "error", "content": str}
    """
    session_id = session["_id"]
    user_id = user["_id"]
    agent_id = _agent_id(user_id)
    mistral = get_mistral_service()

    await message_service.save_message(
        session_id, user_id, "user", SCOPE, user_message, agent_id
    )

    # Mandatory: consult the shared context before answering.
    context = await context_service.get_shared_context(session_id)
    files = await file_service.list_files(session_id)
    files_overview = file_service.format_file_tree(files)
    system_prompt = PERSONAL_AGENT_PROMPT.format(
        shared_context=_format_context(context), files_overview=files_overview
    )

    history = await message_service.get_history(session_id, user_id, SCOPE, limit=20)
    messages = [{"role": "system", "content": system_prompt}]
    for item in history:
        messages.append({"role": item["role"], "content": item["content"]})

    tools = [REQUEST_MODIFICATION_TOOL, REQUEST_DELETION_TOOL]
    content_parts: list[str] = []
    tool_acc: dict[int, dict] = {}

    try:
        async for delta in mistral.stream_chat(
            messages, tools=tools, tool_choice="auto", temperature=0.3
        ):
            piece = delta.get("content")
            if piece:
                content_parts.append(piece)
                if on_delta is not None:
                    await on_delta(piece)
            for call in delta.get("tool_calls") or []:
                index = call.get("index", 0)
                slot = tool_acc.setdefault(index, {"name": None, "args": []})
                function = call.get("function") or {}
                if function.get("name"):
                    slot["name"] = function["name"]
                if function.get("arguments"):
                    slot["args"].append(function["arguments"])
    except MistralError as exc:
        return {"kind": "error", "content": str(exc)}

    content = "".join(content_parts).strip()

    if tool_acc:
        first = tool_acc[sorted(tool_acc)[0]]
        args = _parse_args("".join(first["args"]))
        name = first.get("name")
        preface = content or "Compris, je prépare une proposition."
        await message_service.save_message(
            session_id, user_id, "assistant", SCOPE, preface, agent_id
        )
        if name == "request_deletion":
            return {"kind": "deletion", "preface": preface, "args": args}
        return {"kind": "modification", "preface": preface, "args": args}

    await message_service.save_message(
        session_id, user_id, "assistant", SCOPE, content, agent_id
    )
    return {"kind": "message", "content": content}


def _format_context(context) -> str:
    if not context:
        return "(contexte indisponible)"
    master = context.get("master_context", {}) or {}
    active = context.get("active_tasks", []) or []
    completed = context.get("completed_tasks", []) or []

    lines = [
        f"Objectif: {master.get('objective', 'n/d')}",
        f"Cadre: {master.get('scope', 'n/d')}",
    ]
    if master.get("deliverables"):
        lines.append("Livrables: " + ", ".join(master["deliverables"]))
    lines.append(f"Résumé de l'état: {context.get('state_summary', 'n/d')}")

    if active:
        lines.append(
            "Tâches en cours: "
            + "; ".join(
                f"{t.get('description')} (agent {t.get('agent_id')})" for t in active
            )
        )
    else:
        lines.append("Tâches en cours: aucune")

    if completed:
        recent = completed[-5:]
        lines.append(
            "Travaux récents: " + "; ".join(t.get("summary", "") for t in recent)
        )
    return "\n".join(lines)


def _parse_args(raw: str) -> dict:
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
