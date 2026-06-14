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
# Действия бывают долгими (npm install, сборка): клиент шлюза обязан ждать
# дольше, чем CMD_TIMEOUT исполнителя, иначе петля умрёт раньше действия.
GATEWAY_HTTP_TIMEOUT = float(os.getenv("GATEWAY_HTTP_TIMEOUT", "900"))


# ─── Шлюз: единственный канал воздействия на мир ─────────────────────────────

class Gateway:
    """Клиент Action Service. По умолчанию ходит по HTTP; в тестах можно
    передать client (например TestClient) для вызовов в процессе."""

    def __init__(self, base_url: str = GATEWAY_URL, client: Any | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client

    def propose(self, agent_id: str, action: str, target: str,
                params: dict[str, Any], *, run_id: str | None = None,
                reason: str = "") -> dict:
        url = f"{self.base_url}/actions/propose"
        payload = {"agent_id": agent_id, "action": action,
                   "target": target, "params": params,
                   "run_id": run_id, "reason": reason}
        r = (self._client.post(url, json=payload) if self._client
             else httpx.post(url, json=payload, timeout=GATEWAY_HTTP_TIMEOUT))
        if r.status_code == 422:
            return {"status": "rejected_unknown", "detail": r.json().get("detail")}
        if r.status_code == 403:
            return {"status": "rejected_forbidden", "detail": r.json().get("detail")}
        r.raise_for_status()
        return r.json()

    def get_action(self, action_id: str) -> dict:
        url = f"{self.base_url}/actions/{action_id}"
        r = (self._client.get(url) if self._client else httpx.get(url, timeout=GATEWAY_HTTP_TIMEOUT))
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
        self.provider: str | None = spec.get("provider")  # имя провайдера из онтологии
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
                                      choice.get("target", ""), choice.get("params", {}),
                                      reason=choice.get("reason", ""))
        return {"agent": self.id, "action": action,
                "reason": choice.get("reason", ""), "gateway": result}

    # Многошаговый режим: агент видит историю и решает СЛЕДУЮЩИЙ шаг или 'done'.
    # context — общая картина проекта от Context Broker (план, доска, файлы).
    def next_step(self, goal: str, history: list[dict],
                  context: str = "", steps_left: int | None = None) -> dict:
        hist = "\n".join(
            f"{i}. {h.get('action')}({h.get('target', '')}) → "
            f"{str(h.get('result', ''))[:300]}"
            for i, h in enumerate(history, 1)
        ) or "(пусто)"
        budget = (f"ОСТАЛОСЬ ШАГОВ: {steps_left}. Расходуй их на создание "
                  f"артефактов, а не на перепроверки; при близком нуле — "
                  f"завершай с done." if steps_left is not None else "")
        system = LOOP_PROMPT.format(
            name=self.id, role=self.role,
            actions=", ".join(self.allowed) or "нет", goal=goal, history=hist,
            context=context or "(контекст проекта не подключён)", budget=budget)
        # Мусорный JSON — не приговор: даём LLM до 3 попыток, прежде чем
        # честно вернуть провал (а не молча завершить задачу).
        decision: dict = {}
        for _ in range(3):
            decision = _parse_json(self.llm.complete(system, "Следующий шаг?"))
            if not decision.get("_parse_error"):
                return decision
        return decision


LOOP_PROMPT = """Ты — {name}, {role} в команде разработки. Рабочая цель: {goal}

ОБЩАЯ КАРТИНА ПРОЕКТА:
{context}

Доступные тебе действия (Action Types): {actions}.
История уже выполненных ТОБОЙ шагов (действие → итог):
{history}

Правила команды:
- Соблюдай контракты с ДОСКИ РЕШЕНИЙ. Принял решение, важное для коллег
  (формат API, стек, структура папок), — опубликуй его: post_note, params {{"text":"..."}}.
- Не пересоздавай то, что уже есть в ФАЙЛАХ ПРОЕКТА, — встраивайся.
- ПЕРЕД изменением существующего файла ОБЯЗАТЕЛЬНО сначала прочитай его (read_file)
  и сохрани ВСЁ, что в нём уже есть: другие функции, экспорты, обработчики,
  импорты. Добавляя новое — не удаляй и не затирай существующее. Перезапись файла
  без чтения, теряющая прежний код, — грубая ошибка.
- write_file: params {{"path":"<относительный путь>","content":"<полное содержимое файла>"}}.
- Если пишешь проверочный/тестовый код: НЕ используй глобалы тест-фреймворков
  (describe/it/expect/test) без установленных типов — иначе линтер/tsc упадёт на
  «Cannot find name». Либо пиши проверку как обычный исполнимый скрипт с явными
  assert/console, либо установи типы (@types/jest) и добавь фреймворк, либо исключи
  тест-файлы из tsconfig. Проверочный код не должен ломать сборку и линт.

{budget}
Реши СЛЕДУЮЩИЙ шаг и ответь строго одним JSON-объектом без пояснений:
- действие: {{"action":"<имя>","target":"<репозиторий/объект>","params":{{...}},"reason":"<кратко>"}}
- завершить: {{"done":true,"summary":"<что сделано>"}}
Учитывай итоги прошлых шагов: если шаг провалился — исправься;
не повторяй уже выполненное. Когда КРИТЕРИЙ ГОТОВНОСТИ достигнут — верни done."""


def _parse_json(text: str) -> dict:
    s, e = text.find("{"), text.rfind("}") + 1
    if s != -1 and e > s:
        try:
            return json.loads(text[s:e])
        except json.JSONDecodeError:
            pass
    return {"action": None, "_parse_error": True,
            "reason": "не удалось разобрать ответ LLM"}


def build_office(llm: LLM | None = None, gateway: Gateway | None = None,
                 roster_path: str = ONTOLOGY_PATH) -> dict[str, Agent]:
    """Собирает штат. Если llm передан (тесты) — он общий для всех агентов.
    Если llm=None (прод) — каждый агент получает LLM своего провайдера из
    онтологии (поле provider), что и позволяет bjorn работать на MiMo,
    а reviewer/kristina — на Claude."""
    from llm import get_provider
    office: dict[str, Agent] = {}
    for name, spec in load_roster(roster_path).items():
        agent_llm = llm if llm is not None else get_provider(spec.get("provider"))
        office[name] = Agent(spec, agent_llm, gateway)
    return office
