"""Thin async client for the Mistral chat-completions API (OpenAI-compatible).

Kept dependency-free (httpx only) so it is robust against SDK churn. Supports
plain chat, tool/function calling and JSON response formats.
"""

import json
from typing import Any, Optional

import httpx

from app.config import get_settings


class MistralError(Exception):
    """Raised for any error while talking to the Mistral API."""


class MistralConfigError(MistralError):
    """Raised when the Mistral API key is missing."""


class MistralService:
    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.mistral_api_key
        self._model = settings.mistral_model
        self._base_url = settings.mistral_base_url.rstrip("/")

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.3,
        response_format: Optional[dict] = None,
        model: Optional[str] = None,
    ) -> dict:
        if not self.is_configured:
            raise MistralConfigError(
                "La clé MISTRAL_API_KEY n'est pas configurée. "
                "Ajoute-la dans le fichier .env pour activer les agents IA."
            )

        payload: dict[str, Any] = {
            "model": model or self._model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if response_format is not None:
            payload["response_format"] = response_format

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise MistralError(
                f"Erreur API Mistral ({exc.response.status_code}): {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise MistralError(f"Erreur réseau vers Mistral: {exc}") from exc

    @staticmethod
    def first_message(response: dict) -> dict:
        choices = response.get("choices") or []
        if not choices:
            return {}
        return choices[0].get("message", {}) or {}

    async def generate_text(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        model: Optional[str] = None,
    ) -> str:
        response = await self.chat(messages, temperature=temperature, model=model)
        return (self.first_message(response).get("content") or "").strip()

    async def stream_chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.3,
        model: Optional[str] = None,
    ):
        """Yield streaming ``delta`` dicts from the chat-completions SSE stream.

        Each yielded value is the ``choices[0].delta`` object, which may carry a
        ``content`` piece and/or partial ``tool_calls``.
        """
        if not self.is_configured:
            raise MistralConfigError(
                "La clé MISTRAL_API_KEY n'est pas configurée. "
                "Ajoute-la dans le fichier .env pour activer les agents IA."
            )
        payload: dict[str, Any] = {
            "model": model or self._model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                ) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise MistralError(
                            f"Erreur API Mistral ({response.status_code}): "
                            f"{body.decode(errors='ignore')}"
                        )
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        if delta:
                            yield delta
        except httpx.HTTPError as exc:
            raise MistralError(f"Erreur réseau vers Mistral: {exc}") from exc


_service: Optional[MistralService] = None


def get_mistral_service() -> MistralService:
    global _service
    if _service is None:
        _service = MistralService()
    return _service
