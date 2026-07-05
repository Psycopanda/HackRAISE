"""System agent: interviews the session creator and produces the master context.

This agent is temporary. Once it emits the master context (via the
``finalize_master_context`` tool), the caller finalises the session and the
agent is never used again.
"""

import json

from app.agents.prompts import FINALIZE_TOOL, SYSTEM_AGENT_PROMPT
from app.services import message_service
from app.services.mistral_service import MistralError, get_mistral_service

SCOPE = "system_agent"
AGENT_ID = "system-agent"


async def respond(session_id, user_id, user_message: str) -> dict:
    """Process one turn with the system agent.

    Returns one of:
      * {"kind": "message", "content": str}
      * {"kind": "master_context", "content": str, "master_context": dict}
      * {"kind": "error", "content": str}
    """
    mistral = get_mistral_service()
    await message_service.save_message(
        session_id, user_id, "user", SCOPE, user_message, AGENT_ID
    )

    history = await message_service.get_history(session_id, user_id, SCOPE, limit=30)
    messages = [{"role": "system", "content": SYSTEM_AGENT_PROMPT}]
    for item in history:
        messages.append({"role": item["role"], "content": item["content"]})

    try:
        response = await mistral.chat(
            messages, tools=[FINALIZE_TOOL], tool_choice="auto", temperature=0.4
        )
    except MistralError as exc:
        return {"kind": "error", "content": str(exc)}

    message = mistral.first_message(response)
    tool_calls = message.get("tool_calls") or []

    if tool_calls:
        args = _parse_tool_args(tool_calls[0])
        assistant_text = (
            message.get("content")
            or "Parfait, je génère le contexte initial du projet."
        ).strip()
        await message_service.save_message(
            session_id, user_id, "assistant", SCOPE, assistant_text, AGENT_ID
        )
        return {
            "kind": "master_context",
            "content": assistant_text,
            "master_context": args,
        }

    content = (message.get("content") or "").strip()
    await message_service.save_message(
        session_id, user_id, "assistant", SCOPE, content, AGENT_ID
    )
    return {"kind": "message", "content": content}


def _parse_tool_args(tool_call: dict) -> dict:
    try:
        return json.loads(tool_call["function"]["arguments"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return {}
