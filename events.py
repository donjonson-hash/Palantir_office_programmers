"""
События офиса — фундамент «стекла» (наблюдение без вмешательства).

Каждое значимое событие (маршрутизация, шаг агента, исполнение действия,
завершение прогона) публикуется в единый журнал SQLite. Командный центр
забирает его поллингом GET /office/events?after=<id>.

Это слой НАБЛЮДЕНИЯ, не контроля: запись событий не влияет на исполнение.
Сбой публикации не должен ронять рабочую петлю — publish() глотает ошибки
записи, честно печатая их в stderr.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from typing import Any

EVENTS_DB_PATH = os.getenv("EVENTS_DB_PATH", "office_events.db")

# Словарь видов событий (не enforced — справочно для читателя):
#   task_routed        — Kristina раздала задачу агенту
#   run_started        — агент взял цель в работу
#   action_executed    — действие прошло шлюз и исполнено
#   action_failed      — действие прошло шлюз, но исполнитель упал
#   action_pending     — действие встало в очередь одобрения (рудимент-страховка)
#   action_rejected    — шлюз отказал (нет в онтологии / нет полномочий)
#   run_done           — агент завершил цель
#   run_stopped        — петля упёрлась в лимит шагов
#   run_failed         — прогон не удалось вести (нет агента и т.п.)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(EVENTS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(_db()) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS events (
                   id      INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts      REAL NOT NULL,
                   kind    TEXT NOT NULL,
                   agent   TEXT,
                   run_id  TEXT,
                   plan_id TEXT,
                   payload TEXT NOT NULL
               )"""
        )
        conn.commit()


init_db()


def publish(kind: str, *, agent: str | None = None, run_id: str | None = None,
            plan_id: str | None = None, **payload: Any) -> None:
    """Опубликовать событие. Никогда не роняет вызывающий код."""
    try:
        with closing(_db()) as conn:
            conn.execute(
                "INSERT INTO events (ts, kind, agent, run_id, plan_id, payload) "
                "VALUES (:ts,:kind,:agent,:run_id,:plan_id,:payload)",
                {"ts": time.time(), "kind": kind, "agent": agent,
                 "run_id": run_id, "plan_id": plan_id,
                 "payload": json.dumps(payload, ensure_ascii=False)},
            )
            conn.commit()
    except sqlite3.Error as e:
        print(f"[events] не удалось записать '{kind}': {e}", file=sys.stderr)


def list_events(after: int = 0, limit: int = 200) -> list[dict]:
    """События с id > after, по возрастанию — курсор для поллинга центра."""
    with closing(_db()) as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE id > :after ORDER BY id LIMIT :n",
            {"after": after, "n": limit},
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out
