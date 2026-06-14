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
    """OpenAI-style /chat/completions. Подходит для AITunnel, DeepSeek, OpenAI, MiMo."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, *, token_param: str = "max_tokens",
                 timeout: float = 120) -> None:
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL", "")
        # MiMo ждёт max_completion_tokens вместо max_tokens — провайдер-специфично.
        self.token_param = token_param
        self.timeout = timeout
        if not (self.base_url and self.api_key and self.model):
            raise RuntimeError("LLM не настроен: задай base_url, api_key, model")

    def complete(self, system: str, user: str) -> str:
        r = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "temperature": 0,
                  self.token_param: 4096,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


# ─── Мультипровайдерный реестр ───────────────────────────────────────────────
# Каждый провайдер настраивается тройкой env-переменных <PREFIX>_BASE_URL/_API_KEY/_MODEL.
# Агент в онтологии указывает поле provider; без него берётся DEFAULT.
# Так bjorn/elsa/sven могут работать на MiMo, а reviewer и kristina — на Claude.

# Какой token-параметр шлёт провайдер (MiMo требует max_completion_tokens).
_TOKEN_PARAM = {"MIMO": "max_completion_tokens"}

# Имя провайдера по умолчанию (legacy LLM_* = провайдер "LLM").
DEFAULT_PROVIDER = os.getenv("DEFAULT_PROVIDER", "LLM")

_provider_cache: dict[str, "OpenAICompatibleLLM"] = {}


def get_provider(name: str | None = None) -> "OpenAICompatibleLLM":
    """LLM-клиент по имени провайдера. Конфиг из env <NAME>_BASE_URL/_API_KEY/_MODEL.
    Экземпляры кешируются. Имя провайдера регистронезависимо."""
    prefix = (name or DEFAULT_PROVIDER).upper()
    if prefix in _provider_cache:
        return _provider_cache[prefix]
    base = os.getenv(f"{prefix}_BASE_URL", "")
    key = os.getenv(f"{prefix}_API_KEY", "")
    model = os.getenv(f"{prefix}_MODEL", "")
    if not (base and key and model):
        raise RuntimeError(
            f"провайдер '{prefix}' не настроен: задай "
            f"{prefix}_BASE_URL, {prefix}_API_KEY, {prefix}_MODEL")
    llm = OpenAICompatibleLLM(base, key, model,
                              token_param=_TOKEN_PARAM.get(prefix, "max_tokens"))
    _provider_cache[prefix] = llm
    return llm


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
