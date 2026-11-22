"""Minimal OpenAI-compatible chat client.

One function, no retries, no logging: a failure is an exception the route maps
to 502/503. The API key is read from the process environment and never appears
in an exception message.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app import config


class LLMError(Exception):
    """Any failure talking to the configured chat endpoint."""


class LLMNotConfigured(LLMError):
    """One of LLM_BASE_URL / LLM_API_KEY / LLM_MODEL is empty."""


def is_configured() -> bool:
    return bool(config.LLM_BASE_URL and config.LLM_API_KEY and config.LLM_MODEL)


async def chat(messages: list[dict], temperature: float = 0.0) -> str:
    """Return the assistant message content for ``messages``."""
    if not is_configured():
        raise LLMNotConfigured(
            "LLM not configured: set LLM_BASE_URL/LLM_API_KEY/LLM_MODEL"
        )

    url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    payload = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 512,
    }
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        # Only the exception type: transport errors can carry the URL but must
        # never carry the Authorization header into a response body.
        raise LLMError(f"LLM request failed: {type(exc).__name__}") from exc

    if response.status_code >= 400:
        raise LLMError(f"LLM returned HTTP {response.status_code}")

    try:
        body = response.json()
    except ValueError as exc:
        raise LLMError("LLM returned a non-JSON body") from exc

    choices = body.get("choices") if isinstance(body, dict) else None
    if not choices:
        raise LLMError("LLM response has no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str):
        raise LLMError("LLM response content is not a string")
    return content


async def stream_chat(
    messages: list[dict], temperature: float = 0.0
) -> AsyncIterator[str]:
    """Yield content deltas from an OpenAI-compatible streaming completion.

    Only ``data:`` lines are interpreted; ``[DONE]`` ends the stream. A frame
    with no ``delta.content`` (role frames, usage frames) is skipped, and an
    unparseable frame is ignored rather than aborting the whole answer.
    """
    if not is_configured():
        raise LLMNotConfigured(
            "LLM not configured: set LLM_BASE_URL/LLM_API_KEY/LLM_MODEL"
        )

    url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    payload = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 512,
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST", url, json=payload, headers=headers
            ) as response:
                if response.status_code >= 400:
                    raise LLMError(f"LLM returned HTTP {response.status_code}")

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        return
                    try:
                        frame = json.loads(data)
                    except ValueError:
                        continue
                    choices = frame.get("choices") if isinstance(frame, dict) else None
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        yield content
    except httpx.HTTPError as exc:
        raise LLMError(f"LLM stream failed: {type(exc).__name__}") from exc
