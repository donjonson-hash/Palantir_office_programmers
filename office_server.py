"""
Office Server — HTTP-фасад офиса для командного центра.

Выставляет наружу диспетчера (Kristina): приём задачи, журнал задач и штат.
Журнал задач (маршрутизация) — зона офиса; провенанс действий и очередь
одобрений живут в Action Service (шлюзе), центр ходит туда напрямую.
"""
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv(override=True)

import hashlib
import os
import sqlite3
import time
from contextlib import closing
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agents import Gateway, build_office, load_roster
from dispatcher import Dispatcher
from llm import OpenAICompatibleLLM
import runner

ONTOLOGY_PATH = os.getenv("ONTOLOGY_PATH", "ontology.yaml")
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8000")
OFFICE_DB_PATH = os.getenv("OFFICE_DB_PATH", "office_tasks.db")

app = FastAPI(title="Office Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"], allow_headers=["*"],
)

_dispatcher: Optional[Dispatcher] = None  # можно подменить в тестах


def get_dispatcher() -> Dispatcher:
    global _dispatcher
    if _dispatcher is None:
        try:
            llm = OpenAICompatibleLLM()  # ключи из env
        except RuntimeError as e:
            raise HTTPException(503, str(e))
        _dispatcher = Dispatcher(build_office(llm, Gateway(GATEWAY_URL)), llm)
    return _dispatcher


# ─── Журнал задач: маршрутизация офиса, переживает рестарт ───────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(OFFICE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(_db()) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
                   id         TEXT PRIMARY KEY,
                   text       TEXT NOT NULL,
                   routed_to  TEXT,
                   action     TEXT,
                   action_id  TEXT,
                   tier       TEXT,
                   status     TEXT,
                   reason     TEXT,
                   created_at REAL NOT NULL
               )"""
        )
        conn.commit()


init_db()


def _flatten(handle_result: dict[str, Any]) -> dict[str, Any]:
    """Свести исход диспетчера к плоской записи журнала."""
    res = handle_result.get("result", {})
    gw = res.get("gateway")
    if gw:
        return {"action": res.get("action", ""), "action_id": gw.get("id", ""),
                "tier": gw.get("tier", ""), "status": gw.get("status", "")}
    return {"action": res.get("action", ""), "action_id": "",
            "tier": "", "status": res.get("status", "unknown")}


def _record_task(text: str, handle_result: dict[str, Any]) -> str:
    ts = time.time()
    tid = hashlib.sha256(f"{text}{ts}".encode()).hexdigest()[:12]
    flat = _flatten(handle_result)
    with closing(_db()) as conn:
        conn.execute(
            """INSERT INTO tasks (id, text, routed_to, action, action_id, tier,
                                  status, reason, created_at)
               VALUES (:id,:text,:routed_to,:action,:action_id,:tier,
                       :status,:reason,:created_at)""",
            {"id": tid, "text": text, "routed_to": handle_result.get("routed_to"),
             "reason": handle_result.get("routing_reason"), "created_at": ts, **flat},
        )
        conn.commit()
    return tid


# ─── API ─────────────────────────────────────────────────────────────────────

class TaskRequest(BaseModel):
    task: str


@app.post("/office/task")
def submit_task(req: TaskRequest) -> dict:
    out = get_dispatcher().handle(req.task)
    return {"task_id": _record_task(req.task, out), **out}


@app.get("/office/tasks")
def list_tasks(limit: int = 50) -> dict:
    with closing(_db()) as conn:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT :n", {"n": limit}
        ).fetchall()
    return {"tasks": [dict(r) for r in rows]}


@app.get("/office/agents")
def list_agents() -> dict:
    roster = load_roster(ONTOLOGY_PATH)
    return {"agents": [{"name": s["name"], "role": s.get("role", ""),
                        "allowed_actions": s.get("allowed_actions", [])}
                       for s in roster.values()]}


@app.get("/office/ontology")
def get_ontology() -> dict:
    """Живое зеркало ontology.yaml для центра: объекты, тиры, каталог действий, штат."""
    import yaml
    with open(ONTOLOGY_PATH, "r", encoding="utf-8") as f:
        onto = (yaml.safe_load(f) or {}).get("ontology", {})
    return {
        "object_types": onto.get("object_types", []),
        "risk_tiers": onto.get("risk_tiers", {}),
        "action_types": onto.get("action_types", []),
        "agents": [{"name": a["name"], "role": a.get("role", ""),
                    "allowed_actions": a.get("allowed_actions", [])}
                   for a in onto.get("agents", [])],
    }


# ─── Рабочий цикл агента (многошаговые задачи) ───────────────────────────────

class RunRequest(BaseModel):
    goal: str


def _office_and_gateway():
    disp = get_dispatcher()
    gateway = next(iter(disp.office.values())).gateway
    return disp, gateway


@app.post("/office/run")
def start_run(req: RunRequest) -> dict:
    disp, gateway = _office_and_gateway()
    agent_id, _ = disp.route(req.goal)
    run_id = runner.create_run(req.goal, agent_id)
    return runner.drive(run_id, disp.office, gateway)


@app.post("/office/run/{run_id}/continue")
def continue_run(run_id: str) -> dict:
    disp, gateway = _office_and_gateway()
    try:
        return runner.continue_run(run_id, disp.office, gateway)
    except KeyError:
        raise HTTPException(404, "Задача не найдена")


@app.get("/office/run/{run_id}")
def run_state(run_id: str) -> dict:
    try:
        return runner.get_state(run_id)
    except KeyError:
        raise HTTPException(404, "Задача не найдена")


@app.get("/office/runs")
def runs() -> dict:
    return {"runs": runner.list_runs()}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "agents": len(load_roster(ONTOLOGY_PATH))}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)
