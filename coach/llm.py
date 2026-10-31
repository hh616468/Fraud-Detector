"""Minimal async client for an OpenAI-compatible chat endpoint (vLLM serving Gemma).

The client can only generate text. It has no tools and no access to the wallet.
"""

from __future__ import annotations

import json
import re
import time

import httpx


class LLMError(Exception):
    pass


_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_object(content: str) -> dict:
    """Parse the model's JSON object, tolerating code fences or stray text around it."""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        match = _OBJECT.search(content or "")
        if not match:
            raise LLMError("invalid_json") from None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError("invalid_json") from exc
    if not isinstance(data, dict):
        raise LLMError("invalid_json")
    return data


class LLMClient:
    def __init__(self, base_url: str, model: str, api_key: str = "EMPTY"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

    async def chat_json(
        self,
        messages: list[dict],
        timeout_s: float,
        max_tokens: int = 400,
        temperature: float = 0.4,
    ) -> tuple[dict, float]:
        # json_object rather than a strict json_schema: with Gemma on vLLM, strict schemas
        # degenerated into whitespace until max_tokens. The validator enforces the shape instead.
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False},
        }
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        except httpx.TimeoutException as exc:
            raise LLMError("timeout") from exc
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"http:{type(exc).__name__}") from exc
        latency_ms = (time.perf_counter() - started) * 1000
        return parse_json_object(content), latency_ms

    async def ping(self, timeout_s: float = 2.0) -> bool:
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"})
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
