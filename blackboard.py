"""
Blackboard — доска решений команды (классический паттерн multi-agent систем).

Агенты публикуют сюда КОНТРАКТНЫЕ решения, обязательные для всей команды:
выбранный стек, формат API («POST /api/research принимает {topic} ...»),
структуру папок, соглашения об именовании. Сосед строит работу под контракт
с доски, а не под выдуманный.

Запись идёт через шлюз (действие post_note, LOW) — с провенансом и событием,
как всё в офисе. Чтение — через Context Broker: заметки попадают в промпт
каждого агента на каждом шаге.

Заметки глобальны для офиса (проекты исполняются последовательно). Если офис
когда-нибудь поведёт несколько проектов параллельно — добавить scope по plan_id.
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing

NOTES_DB_PATH = os.getenv("NOTES_DB_PATH", "office_notes.db")
MAX_NOTE_LEN = 2000


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(NOTES_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(_db()) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS notes (
                   id    INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts    REAL NOT NULL,
                   agent TEXT NOT NULL,
                   text  TEXT NOT NULL
               )"""
        )
        conn.commit()


init_db()


def add(agent: str, text: str) -> str:
    text = str(text)[:MAX_NOTE_LEN]
    if not text.strip():
        raise ValueError("пустая заметка")
    with closing(_db()) as conn:
        conn.execute("INSERT INTO notes (ts, agent, text) VALUES (:ts,:agent,:text)",
                     {"ts": time.time(), "agent": agent, "text": text})
        conn.commit()
    return f"заметка опубликована на доске ({agent})"


def list_notes(limit: int = 100) -> list[dict]:
    with closing(_db()) as conn:
        rows = conn.execute(
            "SELECT * FROM notes ORDER BY id LIMIT :n", {"n": limit}).fetchall()
    return [dict(r) for r in rows]


def clear() -> None:
    """Очистка доски (используется оркестратором при старте нового проекта)."""
    with closing(_db()) as conn:
        conn.execute("DELETE FROM notes")
        conn.commit()
