"""Synchronous JSON generation for offline data building (personas, scam scripts)."""

from __future__ import annotations

import httpx

from coach.llm import parse_json_object
from config import settings


def chat_json(system: str, user: str, temperature: float = 0.8, max_tokens: int = 1200, timeout_s: float = 120) -> dict:
    body = {
        "model": settings.llm_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    resp = httpx.post(f"{settings.llm_base_url}/chat/completions", json=body, timeout=timeout_s,
                      headers={"Authorization": f"Bearer {settings.llm_api_key}"})
    resp.raise_for_status()
    return parse_json_object(resp.json()["choices"][0]["message"]["content"])


def reachable() -> bool:
    try:
        return httpx.get(f"{settings.llm_base_url}/models", timeout=5).status_code == 200
    except httpx.HTTPError:
        return False
