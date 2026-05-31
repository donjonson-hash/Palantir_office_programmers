"""
Диспетчер офиса (Kristina) — переосмысленный orchestrator.

Принимает входящую задачу и маршрутизирует её ОДНОМУ исполнителю по ролям и
полномочиям из онтологии: LLM-роутер + детерминированный fallback по ключевым
словам. Kristina не предлагает и не исполняет действия — только выбирает, кто
будет работать. Сам исполнитель действует строго через шлюз.
"""
from __future__ import annotations

import json
from typing import Any

from agents import Agent
from llm import LLM

ROUTER_SYSTEM = """Ты — Kristina, лид команды разработки. Распредели задачу ОДНОМУ исполнителю.
Команда (имя — роль — что умеет):
{roster}
Ответь строго JSON без пояснений: {{"agent": "<имя>", "reason": "<кратко почему>"}}"""

# Грубое соответствие ключевых слов ролям — детерминированный fallback.
_KEYWORDS = {
    "elsa":   ["frontend", "react", "ui", "вёрст", "компонент", "интерфейс"],
    "sven":   ["deploy", "деплой", "прод", "ci", "infra", "инфра", "секрет", "merge", "мёрж"],
    "ingrid": ["test", "тест", "qa", "lint", "линт", "покрыт"],
    "bjorn":  ["backend", "бэк", "api", "сервер", "база", "endpoint"],
}


class Dispatcher:
    def __init__(self, office: dict[str, Agent], llm: LLM,
                 default_agent: str = "bjorn") -> None:
        self.office = office
        self.llm = llm
        self.default_agent = default_agent
        # Кандидаты на исполнение — все, кроме самой Kristina.
        self.workers = [name for name in office if name != "kristina"]

    def route(self, task: str) -> tuple[str, str]:
        roster = "\n".join(
            f"- {self.office[name].id} — {self.office[name].role} — "
            f"{', '.join(self.office[name].allowed)}"
            for name in self.workers
        )
        choice = _parse_json(self.llm.complete(ROUTER_SYSTEM.format(roster=roster), task))
        agent = choice.get("agent")
        if agent in self.workers:
            return agent, choice.get("reason", "")
        return self._fallback(task), "fallback по ключевым словам"

    def _fallback(self, task: str) -> str:
        low = task.lower()
        for agent, words in _KEYWORDS.items():
            if agent in self.workers and any(w in low for w in words):
                return agent
        return self.default_agent

    def handle(self, task: str) -> dict[str, Any]:
        agent_id, reason = self.route(task)
        result = self.office[agent_id].act(task)
        return {"task": task, "routed_to": agent_id,
                "routing_reason": reason, "result": result}


def _parse_json(text: str) -> dict:
    s, e = text.find("{"), text.rfind("}") + 1
    if s != -1 and e > s:
        try:
            return json.loads(text[s:e])
        except json.JSONDecodeError:
            pass
    return {}
