"""
Раннер задач — рабочий цикл агента (вторая половина связки).

Агент работает не одним действием, а ИТЕРАТИВНО: видит цель и историю шагов,
предлагает следующий шаг, видит его результат, решает дальше — пока не скажет
'done' или не упрётся в лимит шагов.

Гейт человека сохранён: AUTO/LOW исполняются автоматически и петля идёт дальше;
HIGH/CRITICAL ставят задачу на ПАУЗУ (waiting_approval). После одобрения человеком
задача ВОЗОБНОВЛЯЕТСЯ с реальным результатом действия. Так получается полный круг:
пользователь → агент → проект → агент → человек → агент → … → готово.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import closing
from typing import Any

import events

MAX_STEPS = int(os.getenv("MAX_RUN_STEPS", "40"))
RUN_DB_PATH = os.getenv("RUN_DB_PATH", "office_runs.db")


# ─── Хранилище прогонов (переживает рестарт) ─────────────────────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(RUN_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(_db()) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS runs (
                   id                TEXT PRIMARY KEY,
                   goal              TEXT NOT NULL,
                   agent_id          TEXT NOT NULL,
                   status            TEXT NOT NULL,
                   history           TEXT NOT NULL,
                   pending_action_id TEXT,
                   steps             INTEGER NOT NULL,
                   summary           TEXT,
                   created_at        REAL NOT NULL
               )"""
        )
        # Миграция: привязка прогона к плану проекта (Этап 3).
        try:
            conn.execute("ALTER TABLE runs ADD COLUMN plan_id TEXT")
        except sqlite3.OperationalError:
            pass  # колонка уже есть
        conn.commit()


init_db()


def _get(run_id: str) -> sqlite3.Row | None:
    with closing(_db()) as conn:
        return conn.execute("SELECT * FROM runs WHERE id = :id", {"id": run_id}).fetchone()


def _update(run_id: str, **fields: Any) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = run_id
    with closing(_db()) as conn:
        conn.execute(f"UPDATE runs SET {sets} WHERE id = :id", fields)
        conn.commit()


def _state(run_id: str) -> dict:
    row = _get(run_id)
    if row is None:
        raise KeyError(run_id)
    d = dict(row)
    d["history"] = json.loads(d["history"])
    return d


# ─── Жизненный цикл прогона ──────────────────────────────────────────────────

def create_run(goal: str, agent_id: str, plan_id: str | None = None) -> str:
    run_id = uuid.uuid4().hex[:12]
    with closing(_db()) as conn:
        conn.execute(
            """INSERT INTO runs (id, goal, agent_id, status, history,
                                 pending_action_id, steps, summary, created_at, plan_id)
               VALUES (:id,:goal,:agent_id,'running','[]',NULL,0,NULL,:ts,:plan_id)""",
            {"id": run_id, "goal": goal, "agent_id": agent_id,
             "ts": time.time(), "plan_id": plan_id},
        )
        conn.commit()
    events.publish("run_started", agent=agent_id, run_id=run_id, plan_id=plan_id,
                   goal=goal[:300])
    return run_id


def drive(run_id: str, office: dict, gateway: Any,
          context_provider: Any | None = None) -> dict:
    """Крутит петлю, пока не done / провал / лимит шагов.
    context_provider(agent_id) -> str — общая картина проекта (Context Broker);
    собирается заново на каждом шаге: агент видит свежие файлы/доску/план."""
    row = _get(run_id)
    if row is None:
        raise KeyError(run_id)
    agent = office.get(row["agent_id"])
    if agent is None:
        _update(run_id, status="failed", summary=f"нет агента {row['agent_id']}")
        events.publish("run_failed", agent=row["agent_id"], run_id=run_id,
                       detail=f"нет агента {row['agent_id']}")
        return _state(run_id)

    history: list[dict] = json.loads(row["history"])
    steps: int = row["steps"]

    while steps < MAX_STEPS:
        ctx = context_provider(agent.id) if context_provider else ""
        decision = agent.next_step(row["goal"], history, ctx)

        if decision.get("done"):
            _update(run_id, status="done", history=json.dumps(history, ensure_ascii=False),
                    steps=steps, summary=decision.get("summary", ""))
            events.publish("run_done", agent=agent.id, run_id=run_id,
                           steps=steps, summary=decision.get("summary", ""))
            return _state(run_id)

        if not decision.get("action"):
            # Нет действия и нет done — в автономном режиме это провал, а не
            # успех: «молча объявить готовым» хуже честного отказа.
            detail = decision.get("reason", "агент не вернул ни действия, ни done")
            _update(run_id, status="failed", history=json.dumps(history, ensure_ascii=False),
                    steps=steps, summary=detail)
            events.publish("run_failed", agent=agent.id, run_id=run_id,
                           steps=steps, detail=detail)
            return _state(run_id)

        gw = gateway.propose(agent.id, decision["action"],
                             decision.get("target", ""), decision.get("params", {}),
                             run_id=run_id, reason=decision.get("reason", ""))
        steps += 1
        status = gw.get("status")

        if status == "pending":
            # HIGH/CRITICAL — пауза до решения человека. Ничего не дописываем в
            # историю как факт: запомним id ожидающего действия.
            _update(run_id, status="waiting_approval",
                    history=json.dumps(history, ensure_ascii=False),
                    steps=steps, pending_action_id=gw["id"])
            return _state(run_id)

        # executed / failed / rejected_* — результат виден агенту на след. шаге.
        history.append({"action": decision["action"], "target": decision.get("target", ""),
                        "result": gw.get("result") or status, "status": status})
        _update(run_id, history=json.dumps(history, ensure_ascii=False), steps=steps)

    _update(run_id, status="stopped", history=json.dumps(history, ensure_ascii=False),
            steps=steps, summary=f"достигнут лимит шагов ({MAX_STEPS})")
    events.publish("run_stopped", agent=agent.id, run_id=run_id,
                   steps=steps, detail=f"достигнут лимит шагов ({MAX_STEPS})")
    return _state(run_id)


def continue_run(run_id: str, office: dict, gateway: Any,
                 context_provider: Any | None = None) -> dict:
    """Возобновляет приостановленную задачу после решения человека по действию."""
    row = _get(run_id)
    if row is None:
        raise KeyError(run_id)
    if row["status"] != "waiting_approval" or not row["pending_action_id"]:
        return _state(run_id)  # нечего возобновлять

    rec = gateway.get_action(row["pending_action_id"])
    if rec["status"] == "pending":
        return _state(run_id)  # человек ещё не решил

    history: list[dict] = json.loads(row["history"])
    history.append({"action": rec["action"], "target": rec["target"],
                    "result": rec.get("result") or rec["status"], "status": rec["status"]})
    _update(run_id, status="running", history=json.dumps(history, ensure_ascii=False),
            pending_action_id=None)
    return drive(run_id, office, gateway, context_provider)


def find_run_by_pending(action_id: str) -> str | None:
    """Найти задачу, которая стоит на паузе именно из-за этого действия."""
    with closing(_db()) as conn:
        row = conn.execute(
            "SELECT id FROM runs WHERE pending_action_id = :a "
            "AND status = 'waiting_approval'", {"a": action_id}
        ).fetchone()
    return row["id"] if row else None


def list_runs(limit: int = 50) -> list[dict]:
    with closing(_db()) as conn:
        rows = conn.execute(
            "SELECT id, goal, agent_id, status, steps, pending_action_id, summary, created_at "
            "FROM runs ORDER BY created_at DESC LIMIT :n", {"n": limit}
        ).fetchall()
    return [dict(r) for r in rows]


def get_state(run_id: str) -> dict:
    return _state(run_id)
