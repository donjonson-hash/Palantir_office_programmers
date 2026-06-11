"""
Планировщик и оркестратор проектов офиса.

«Поставил задачу — получил результат»: человек отдаёт ЦЕЛЬ ПРОЕКТА, Kristina
декомпозирует её в план подзадач (кому, что, критерии приёмки), и оркестратор
ведёт подзадачи последовательно — каждая подзадача исполняется рабочей петлёй
runner'а тем агентом, которому её раздала Kristina.

Контроля человека в петле нет (режим автономии): весь ход работы виден через
события (plan_created → subtask_started → ... → project_done) в «стекле».

Kristina при планировании обязана выстроить подзадачи в порядке исполнения;
depends_on хранится для отображения зависимостей в центре.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import closing
from typing import Any

import blackboard
import context
import events
import runner
from llm import LLM

PLANS_DB_PATH = os.getenv("PLANS_DB_PATH", "office_plans.db")
PLAN_RETRIES = int(os.getenv("PLAN_RETRIES", "3"))


# ─── Хранилище планов (переживает рестарт) ───────────────────────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(PLANS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(_db()) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS plans (
                   id         TEXT PRIMARY KEY,
                   goal       TEXT NOT NULL,
                   status     TEXT NOT NULL,
                   subtasks   TEXT NOT NULL,
                   summary    TEXT,
                   created_at REAL NOT NULL
               )"""
        )
        conn.commit()


init_db()


def _update(plan_id: str, **fields: Any) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = plan_id
    with closing(_db()) as conn:
        conn.execute(f"UPDATE plans SET {sets} WHERE id = :id", fields)
        conn.commit()


def get_plan(plan_id: str) -> dict:
    with closing(_db()) as conn:
        row = conn.execute("SELECT * FROM plans WHERE id = :id",
                           {"id": plan_id}).fetchone()
    if row is None:
        raise KeyError(plan_id)
    d = dict(row)
    d["subtasks"] = json.loads(d["subtasks"])
    return d


def list_plans(limit: int = 50) -> list[dict]:
    with closing(_db()) as conn:
        rows = conn.execute(
            "SELECT id, goal, status, summary, created_at FROM plans "
            "ORDER BY created_at DESC LIMIT :n", {"n": limit}).fetchall()
    return [dict(r) for r in rows]


def _save_subtasks(plan_id: str, subtasks: list[dict]) -> None:
    _update(plan_id, subtasks=json.dumps(subtasks, ensure_ascii=False))


# ─── Планирование: Kristina декомпозирует цель ───────────────────────────────

PLAN_PROMPT = """Ты — Kristina, лид команды разработки. Разбей цель проекта на подзадачи.
Команда (имя — роль):
{roster}
Правила:
- Подзадачи В ПОРЯДКЕ ИСПОЛНЕНИЯ: сначала каркас/бэкенд, затем фронтенд, в конце проверка.
- Каждой подзадаче — ОДИН исполнитель из команды (kristina не исполняет).
- acceptance — проверяемый критерий готовности, конкретный.
- 3–8 подзадач, без лишних.
Ответь строго одним JSON-объектом без пояснений:
{{"subtasks":[{{"agent":"<имя>","title":"<кратко>","description":"<что сделать>",
"acceptance":"<критерий готовности>","depends_on":[<номера подзадач с 1>]}}]}}"""


def make_plan(goal: str, llm: LLM, workers: dict[str, str]) -> str:
    """Сгенерировать план и сохранить. Возвращает plan_id.
    workers: имя → роль (исполнители, без kristina)."""
    roster = "\n".join(f"- {name} — {role}" for name, role in workers.items())
    system = PLAN_PROMPT.format(roster=roster)

    subtasks: list[dict] = []
    last_error = ""
    for _ in range(PLAN_RETRIES):
        raw = llm.complete(system, goal)
        subtasks, last_error = _parse_plan(raw, set(workers))
        if subtasks:
            break

    plan_id = uuid.uuid4().hex[:12]
    status = "planned" if subtasks else "plan_failed"
    for i, st in enumerate(subtasks, 1):
        st.update({"n": i, "status": "queued", "run_id": None})
    with closing(_db()) as conn:
        conn.execute(
            """INSERT INTO plans (id, goal, status, subtasks, summary, created_at)
               VALUES (:id,:goal,:status,:subtasks,:summary,:ts)""",
            {"id": plan_id, "goal": goal, "status": status,
             "subtasks": json.dumps(subtasks, ensure_ascii=False),
             "summary": None if subtasks else f"план не сгенерирован: {last_error}",
             "ts": time.time()},
        )
        conn.commit()

    if subtasks:
        events.publish("plan_created", agent="kristina", plan_id=plan_id,
                       goal=goal[:300],
                       subtasks=[{"n": s["n"], "agent": s["agent"],
                                  "title": s["title"]} for s in subtasks])
    else:
        events.publish("plan_failed", agent="kristina", plan_id=plan_id,
                       goal=goal[:300], detail=last_error)
    return plan_id


def _parse_plan(raw: str, valid_agents: set[str]) -> tuple[list[dict], str]:
    """Разобрать и провалидировать ответ Kristina. → (подзадачи, ошибка)."""
    s, e = raw.find("{"), raw.rfind("}") + 1
    if s == -1 or e <= s:
        return [], "ответ не содержит JSON"
    try:
        data = json.loads(raw[s:e])
    except json.JSONDecodeError as err:
        return [], f"невалидный JSON: {err}"
    subtasks = data.get("subtasks")
    if not isinstance(subtasks, list) or not subtasks:
        return [], "пустой или отсутствующий список subtasks"
    out = []
    for st in subtasks:
        agent = st.get("agent")
        if agent not in valid_agents:
            return [], f"неизвестный исполнитель '{agent}'"
        if not st.get("description"):
            return [], "подзадача без description"
        out.append({"agent": agent, "title": st.get("title", "")[:200],
                    "description": str(st["description"]),
                    "acceptance": str(st.get("acceptance", "")),
                    "depends_on": st.get("depends_on", []) or []})
    return out, ""


# ─── Оркестрация: последовательное исполнение подзадач ───────────────────────

def _subtask_goal(goal: str, st: dict, total: int) -> str:
    return (f"ЦЕЛЬ ПРОЕКТА: {goal}\n"
            f"ТВОЯ ПОДЗАДАЧА ({st['n']} из {total}): {st['description']}\n"
            f"КРИТЕРИЙ ГОТОВНОСТИ: {st['acceptance'] or 'подзадача выполнена по описанию'}")


def run_project(plan_id: str, office: dict, gateway: Any) -> dict:
    """Ведёт подзадачи плана последовательно до конца / первого провала.
    Синхронная функция: на сервере запускается в фоне (BackgroundTasks)."""
    plan = get_plan(plan_id)
    if plan["status"] not in ("planned", "running"):
        return plan
    if plan["status"] == "planned":
        blackboard.clear()   # новый проект — чистая доска решений
    _update(plan_id, status="running")
    subtasks = plan["subtasks"]
    total = len(subtasks)
    ctx_provider = lambda agent_id: context.build(plan_id, agent_id)  # noqa: E731

    for st in subtasks:
        if st["status"] == "done":      # возобновление после рестарта
            continue
        events.publish("subtask_started", agent=st["agent"], plan_id=plan_id,
                       n=st["n"], title=st["title"])
        st["status"] = "in_progress"
        run_id = runner.create_run(_subtask_goal(plan["goal"], st, total),
                                   st["agent"], plan_id=plan_id)
        st["run_id"] = run_id
        _save_subtasks(plan_id, subtasks)

        state = runner.drive(run_id, office, gateway, ctx_provider)

        if state["status"] == "done":
            st["status"] = "done"
            _save_subtasks(plan_id, subtasks)
            events.publish("subtask_done", agent=st["agent"], plan_id=plan_id,
                           run_id=run_id, n=st["n"], title=st["title"],
                           summary=state.get("summary", ""))
            continue

        # stopped (лимит шагов) / failed / waiting_approval (страховка) — провал
        # подзадачи останавливает проект: видимый честный исход вместо тихого мусора.
        st["status"] = "failed"
        _save_subtasks(plan_id, subtasks)
        detail = f"подзадача {st['n']} «{st['title']}»: прогон {state['status']}"
        _update(plan_id, status="failed", summary=detail)
        events.publish("subtask_failed", agent=st["agent"], plan_id=plan_id,
                       run_id=run_id, n=st["n"], title=st["title"],
                       detail=state.get("summary", state["status"]))
        events.publish("project_failed", agent="kristina", plan_id=plan_id,
                       detail=detail)
        return get_plan(plan_id)

    summary = f"все {total} подзадач выполнены"
    _update(plan_id, status="done", summary=summary)
    events.publish("project_done", agent="kristina", plan_id=plan_id,
                   summary=summary)
    return get_plan(plan_id)
