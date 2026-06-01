"""
LLM-слой офиса — переосмысленный apiClient.ts: перенесён на бэк, ключи из env.

Любой OpenAI-совместимый провайдер: AITunnel (Claude), DeepSeek, OpenAI.
Настройка через env: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL.
Для тестов есть FakeLLM — без сети и ключей.
"""
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv(override=True)

import os
from typing import Protocol

import httpx


class LLM(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class OpenAICompatibleLLM:
    """OpenAI-style /chat/completions. Подходит для AITunnel, DeepSeek, OpenAI."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL", "")
        if not (self.base_url and self.api_key and self.model):
            raise RuntimeError("LLM не настроен: задай LLM_BASE_URL, LLM_API_KEY, LLM_MODEL")

    def complete(self, system: str, user: str) -> str:
        r = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "temperature": 0,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


class FakeLLM:
    """Детерминированный LLM для тестов: возвращает заранее заданный ответ."""

    def __init__(self, canned: str) -> None:
        self.canned = canned
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.canned


class ScriptedLLM:
    """Возвращает ответы по списку, по одному на вызов (для многошаговых тестов).
    После исчерпания списка отдаёт 'done'."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.i = 0

    def complete(self, system: str, user: str) -> str:
        if self.i < len(self.responses):
            r = self.responses[self.i]
            self.i += 1
            return r
        return '{"done": true, "summary": "конец сценария"}'
