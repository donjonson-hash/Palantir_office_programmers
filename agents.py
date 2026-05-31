"""
Слой агентов офиса — переосмысленные BaseAgent + orchestrator.

Принцип: агент НЕ исполняет действия. Единственный способ повлиять на мир —
предложить Действие из каталога онтологии через шлюз (/actions/propose).
Шлюз сам определяет тир и нужно ли одобрение человека. У агента нет execute.
Инструменты агента = ровно те Action Types, что разрешены его роли в онтологии.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
import yaml

from llm import LLM

ONTOLOGY_PATH = os.getenv("ONTOLOGY_PATH", "ontology.yaml")
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8000")


# ─── Шлюз: единственный канал воздействия на мир ─────────────────────────────

class Gateway:
    """Клиент Action Service. По умолчанию ходит по HTTP; в тестах можно
    передать client (например TestClient) для вызовов в процессе."""

    def __init__(self, base_url: str = GATEWAY_URL, client: Any | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client

    def propose(self, agent_id: str, action: str, target: str,
                params: dict[str, Any]) -> dict:
        url = f"{self.base_url}/actions/propose"
        payload = {"agent_id": agent_id, "action": action,
                   "target": target, "params": params}
        r = (self._client.post(url, json=payload) if self._client
             else httpx.post(url, json=payload, timeout=30))
        if r.status_code == 422:
            return {"status": "rejected_unknown", "detail": r.json().get("detail")}
        if r.status_code == 403:
            return {"status": "rejected_forbidden", "detail": r.json().get("detail")}
        r.raise_for_status()
        return r.json()


# ─── Онтология: штат и полномочия ────────────────────────────────────────────

def load_roster(path: str = ONTOLOGY_PATH) -> dict[str, dict]:
    with open(path, "r", encoding="utf-8") as f:
        onto = (yaml.safe_load(f) or {}).get("ontology", {})
    return {a["name"]: a for a in onto.get("agents", [])}


# ─── Агент ────────────────────────────────────────────────────────────────────

PROMPT = """Ты — {name}, {role} в команде разработки.
Тебе доступны ТОЛЬКО эти действия (Action Types): {actions}.
Получив задачу, выбери ОДНО действие и ответь строго JSON без пояснений:
{{"action": "<имя>", "target": "<репозиторий/объект>", "params": {{...}}, "reason": "<кратко>"}}
Если ни одно действие не подходит — верни {{"action": null, "reason": "<почему>"}}."""


class Agent:
    def __init__(self, spec: dict, llm: LLM, gateway: Gateway) -> None:
        self.id: str = spec["name"]
        self.role: str = spec.get("role", "")
        self.allowed: list[str] = spec.get("allowed_actions", [])
        self.llm = llm
        self.gateway = gateway

    def act(self, task: str) -> dict:
        system = PROMPT.format(name=self.id, role=self.role,
                               actions=", ".join(self.allowed) or "нет")
        choice = _parse_json(self.llm.complete(system, task))
        action = choice.get("action")

        if not action:
            return {"agent": self.id, "status": "no_action",
                    "reason": choice.get("reason", "")}

        # Полномочия роли: агент не предлагает действия вне своего набора.
        # (Это удобный предохранитель; неподкупная проверка — на шлюзе.)
        if action not in self.allowed:
            return {"agent": self.id, "status": "forbidden",
                    "reason": f"'{action}' вне полномочий роли «{self.role}»"}

        result = self.gateway.propose(self.id, action,
                                      choice.get("target", ""), choice.get("params", {}))
        return {"agent": self.id, "action": action,
                "reason": choice.get("reason", ""), "gateway": result}


def _parse_json(text: str) -> dict:
    s, e = text.find("{"), text.rfind("}") + 1
    if s != -1 and e > s:
        try:
            return json.loads(text[s:e])
        except json.JSONDecodeError:
            pass
    return {"action": None, "reason": "не удалось разобрать ответ LLM"}


def build_office(llm: LLM, gateway: Gateway,
                 roster_path: str = ONTOLOGY_PATH) -> dict[str, Agent]:
    return {name: Agent(spec, llm, gateway)
            for name, spec in load_roster(roster_path).items()}
