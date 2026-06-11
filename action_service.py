"""
Action Service — единственный шлюз мутаций офиса (Palantir-модель).

Ключевой инвариант: агент НЕ решает свой уровень риска. Легальность
действия и его тир берутся ИЗ онтологии. Шлюз — тонкий неподкупный
исполнитель политики: сверяет предложение с каталогом Action Types,
определяет тир, и либо исполняет сразу (обратимое), либо ставит в очередь
на одобрение человека (рискованное). Всё фиксируется в провенансе (SQLite)
и переживает рестарт. Исполнители (OpenClaw/git/shell) стоят строго ЗА
шлюзом — обойти его нельзя.
"""
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv(override=True)

import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from executor import get_executor
import events

ONTOLOGY_PATH = os.getenv("ONTOLOGY_PATH", "ontology.yaml")
AUDIT_DB_PATH = os.getenv("AUDIT_DB_PATH", "office_audit.db")


# ─── Онтология: источник легальных действий и их тиров ───────────────────────

def load_ontology(path: str) -> tuple[dict[str, dict], dict[str, bool], dict[str, set]]:
    with open(path, "r", encoding="utf-8") as f:
        onto = (yaml.safe_load(f) or {}).get("ontology", {})
    action_types = {a["name"]: a for a in onto.get("action_types", [])}
    tier_policy = {
        tier: bool(cfg.get("requires_approval", False))
        for tier, cfg in onto.get("risk_tiers", {}).items()
    }
    agent_permissions = {
        a["name"]: set(a.get("allowed_actions", [])) for a in onto.get("agents", [])
    }
    return action_types, tier_policy, agent_permissions


ACTION_TYPES, TIER_POLICY, AGENT_PERMISSIONS = load_ontology(ONTOLOGY_PATH)


# ─── Провенанс: SQLite, переживает рестарт ───────────────────────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(AUDIT_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(_db()) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS actions (
                   id              TEXT PRIMARY KEY,
                   agent_id        TEXT NOT NULL,
                   action          TEXT NOT NULL,
                   target          TEXT NOT NULL,
                   params          TEXT NOT NULL,
                   tier            TEXT NOT NULL,
                   status          TEXT NOT NULL,
                   result          TEXT,
                   provenance_hash TEXT NOT NULL,
                   created_at      REAL NOT NULL,
                   decided_at      REAL,
                   decided_by      TEXT
               )"""
        )
        conn.commit()


init_db()


def _record(row: dict[str, Any]) -> None:
    with closing(_db()) as conn:
        conn.execute(
            """INSERT INTO actions
               (id, agent_id, action, target, params, tier, status,
                result, provenance_hash, created_at, decided_at, decided_by)
               VALUES (:id,:agent_id,:action,:target,:params,:tier,:status,
                       :result,:provenance_hash,:created_at,:decided_at,:decided_by)""",
            row,
        )
        conn.commit()


def _update(action_id: str, **fields: Any) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = action_id
    with closing(_db()) as conn:
        conn.execute(f"UPDATE actions SET {sets} WHERE id = :id", fields)
        conn.commit()


def _get(action_id: str) -> sqlite3.Row | None:
    with closing(_db()) as conn:
        return conn.execute(
            "SELECT * FROM actions WHERE id = :id", {"id": action_id}
        ).fetchone()


# ─── Исполнение: единственная точка, за которой стоят реальные исполнители ────

def execute(action: str, target: str, params: dict) -> str:
    """Единственный чокпоинт исполнения. За ним стоит локальный исполнитель
    (файлы/терминал, заперт в корне проекта) либо заглушка без WORKSPACE_ROOT.
    Вызывается только после проверки легальности, полномочий и тира."""
    return get_executor().run(action, target, params)


# ─── API ─────────────────────────────────────────────────────────────────────

app = FastAPI(title="Office Action Service")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ProposeRequest(BaseModel):
    # Заметь: НЕТ поля tier / requires_approval. Агент не решает свой риск.
    agent_id: str
    action: str
    target: str
    params: dict[str, Any] = {}
    # Наблюдательные поля для «стекла»: не влияют на политику и исполнение.
    run_id: str | None = None
    reason: str = ""


class ActionView(BaseModel):
    id: str
    agent_id: str
    action: str
    target: str
    tier: str
    status: str
    result: str | None = None


def _new_id(action: str, target: str, params: dict, ts: float) -> tuple[str, str]:
    full = hashlib.sha256(
        json.dumps({"a": action, "t": target, "p": params, "ts": ts},
                   sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return full[:12], full


@app.post("/actions/propose", response_model=ActionView)
def propose(req: ProposeRequest) -> ActionView:
    ts = time.time()
    action_id, full = _new_id(req.action, req.target, req.params, ts)
    params_json = json.dumps(req.params, ensure_ascii=False)

    spec = ACTION_TYPES.get(req.action)
    if spec is None:
        # Незаявленное в онтологии действие предложить нельзя.
        _record({"id": action_id, "agent_id": req.agent_id, "action": req.action,
                 "target": req.target, "params": params_json, "tier": "UNKNOWN",
                 "status": "rejected_unknown", "result": None, "provenance_hash": full,
                 "created_at": ts, "decided_at": ts, "decided_by": "ontology"})
        events.publish("action_rejected", agent=req.agent_id, run_id=req.run_id,
                       action=req.action, target=req.target,
                       detail="действие отсутствует в онтологии")
        raise HTTPException(422, f"Действие '{req.action}' отсутствует в онтологии")

    # Полномочия: неподкупная проверка на шлюзе — агент не может её обойти.
    # Неизвестный агент или действие вне его набора → отказ + запись в провенанс.
    allowed = AGENT_PERMISSIONS.get(req.agent_id)
    if allowed is None or req.action not in allowed:
        _record({"id": action_id, "agent_id": req.agent_id, "action": req.action,
                 "target": req.target, "params": params_json, "tier": spec["tier"],
                 "status": "rejected_forbidden", "result": None, "provenance_hash": full,
                 "created_at": ts, "decided_at": ts, "decided_by": "ontology"})
        events.publish("action_rejected", agent=req.agent_id, run_id=req.run_id,
                       action=req.action, target=req.target,
                       detail="нет полномочий на действие")
        raise HTTPException(403, f"Агент '{req.agent_id}' не имеет полномочий на '{req.action}'")

    tier = spec["tier"]
    requires_approval = TIER_POLICY.get(tier, True)  # неизвестный тир → безопасно требуем одобрения
    base = {"id": action_id, "agent_id": req.agent_id, "action": req.action,
            "target": req.target, "params": params_json, "tier": tier,
            "provenance_hash": full, "created_at": ts,
            "decided_at": None, "decided_by": None, "result": None}

    if requires_approval:
        _record({**base, "status": "pending"})
        events.publish("action_pending", agent=req.agent_id, run_id=req.run_id,
                       action=req.action, target=req.target, tier=tier,
                       reason=req.reason)
        return ActionView(id=action_id, agent_id=req.agent_id, action=req.action,
                          target=req.target, tier=tier, status="pending")

    try:
        result = execute(req.action, req.target, req.params)
    except Exception as e:  # сбой реального исполнителя → failed в провенанс, не 500
        _record({**base, "status": "failed", "result": str(e),
                 "decided_at": ts, "decided_by": "auto"})
        events.publish("action_failed", agent=req.agent_id, run_id=req.run_id,
                       action=req.action, target=req.target, tier=tier,
                       reason=req.reason, error=str(e)[:500])
        return ActionView(id=action_id, agent_id=req.agent_id, action=req.action,
                          target=req.target, tier=tier, status="failed", result=str(e))
    _record({**base, "status": "executed", "result": result,
             "decided_at": ts, "decided_by": "auto"})
    events.publish("action_executed", agent=req.agent_id, run_id=req.run_id,
                   action=req.action, target=req.target, tier=tier,
                   reason=req.reason, result=str(result)[:500])
    return ActionView(id=action_id, agent_id=req.agent_id, action=req.action,
                      target=req.target, tier=tier, status="executed", result=result)


@app.post("/actions/{action_id}/approve", response_model=ActionView)
def approve(action_id: str, approver: str = "human") -> ActionView:
    row = _get(action_id)
    if row is None:
        raise HTTPException(404, "Действие не найдено")
    if row["status"] != "pending":
        raise HTTPException(409, f"Нельзя одобрить действие в статусе '{row['status']}'")
    try:
        result = execute(row["action"], row["target"], json.loads(row["params"]))
    except Exception as e:
        _update(action_id, status="failed", result=str(e),
                decided_at=time.time(), decided_by=approver)
        return ActionView(id=action_id, agent_id=row["agent_id"], action=row["action"],
                          target=row["target"], tier=row["tier"], status="failed", result=str(e))
    _update(action_id, status="executed", result=result,
            decided_at=time.time(), decided_by=approver)
    return ActionView(id=action_id, agent_id=row["agent_id"], action=row["action"],
                      target=row["target"], tier=row["tier"], status="executed", result=result)


@app.post("/actions/{action_id}/reject", response_model=ActionView)
def reject(action_id: str, approver: str = "human") -> ActionView:
    row = _get(action_id)
    if row is None:
        raise HTTPException(404, "Действие не найдено")
    if row["status"] != "pending":
        raise HTTPException(409, f"Нельзя отклонить действие в статусе '{row['status']}'")
    _update(action_id, status="rejected", decided_at=time.time(), decided_by=approver)
    return ActionView(id=action_id, agent_id=row["agent_id"], action=row["action"],
                      target=row["target"], tier=row["tier"], status="rejected")


@app.get("/actions/pending")
def pending() -> dict:
    with closing(_db()) as conn:
        rows = conn.execute(
            "SELECT * FROM actions WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()
    return {"pending": [dict(r) for r in rows]}


@app.get("/actions/audit")
def audit(limit: int = 50) -> dict:
    with closing(_db()) as conn:
        rows = conn.execute(
            "SELECT * FROM actions ORDER BY created_at DESC LIMIT :n", {"n": limit}
        ).fetchall()
    return {"audit": [dict(r) for r in rows]}


@app.get("/actions/{action_id}")
def get_action(action_id: str) -> dict:
    row = _get(action_id)
    if row is None:
        raise HTTPException(404, "Действие не найдено")
    return dict(row)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "action_types": len(ACTION_TYPES),
            "agents": len(AGENT_PERMISSIONS), "tiers": list(TIER_POLICY)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
