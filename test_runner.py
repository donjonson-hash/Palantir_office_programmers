"""
Тесты рабочего цикла агента (runner).
Запуск:  cd office && python -m pytest -q
"""
import os
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).parent
os.environ["ONTOLOGY_PATH"] = str(ROOT / "ontology.yaml")
for var in ("AUDIT_DB_PATH", "RUN_DB_PATH", "EVENTS_DB_PATH"):
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    os.environ[var] = f.name

from fastapi.testclient import TestClient  # noqa: E402
import pytest  # noqa: E402
import action_service  # noqa: E402
import runner  # noqa: E402
from agents import Gateway, build_office  # noqa: E402
from llm import ScriptedLLM  # noqa: E402

GW = Gateway(base_url="", client=TestClient(action_service.app))


@pytest.fixture(autouse=True)
def _stub_executor(monkeypatch):
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)


def office_running(script):
    return build_office(ScriptedLLM(script), GW)


def test_loop_runs_high_autonomously():
    # Автономная политика: write_file (HIGH) исполняется без паузы, петля идёт до done.
    office = office_running([
        '{"action":"write_file","target":"repo","params":{"path":"b","content":"c"},"reason":"правка"}',
        '{"done":true,"summary":"готово"}',
    ])
    run_id = runner.create_run("автономная правка", "bjorn")
    st = runner.drive(run_id, office, GW)
    assert st["status"] == "done"
    assert st["history"][0]["action"] == "write_file"
    assert st["history"][0]["status"] == "executed"


def test_loop_pauses_on_gated_tier_then_resumes_to_done(monkeypatch):
    # Механизм паузы сохранён как страховка: если тиру вернуть requires_approval,
    # петля встанет и возобновится после решения человека.
    monkeypatch.setitem(action_service.TIER_POLICY, "HIGH", True)
    # read_file (AUTO, авто) → write_file (HIGH, пауза) → [человек одобрил] → done
    office = office_running([
        '{"action":"read_file","target":"repo","params":{"path":"a"},"reason":"посмотреть"}',
        '{"action":"write_file","target":"repo","params":{"path":"b","content":"c"},"reason":"правка"}',
        '{"done":true,"summary":"готово"}',
    ])
    run_id = runner.create_run("сделай правку", "bjorn")

    st = runner.drive(run_id, office, GW)
    assert st["status"] == "waiting_approval"          # остановился на HIGH
    assert st["history"][0]["action"] == "read_file"   # AUTO уже исполнился
    assert st["history"][0]["status"] == "executed"

    # человек одобряет ожидающее действие
    GW._client.post(f"/actions/{st['pending_action_id']}/approve", params={"approver": "don"})

    st = runner.continue_run(run_id, office, GW)
    assert st["status"] == "done"                       # петля сама дошла до конца
    actions = [h["action"] for h in st["history"]]
    assert actions == ["read_file", "write_file"]


def test_reject_lets_agent_finish(monkeypatch):
    # write_file (HIGH, гейт включён страховкой) → [отклонил] → агент завершает
    monkeypatch.setitem(action_service.TIER_POLICY, "HIGH", True)
    office = office_running([
        '{"action":"write_file","target":"repo","params":{"path":"b","content":"c"},"reason":"правка"}',
        '{"done":true,"summary":"остановлено по отказу"}',
    ])
    run_id = runner.create_run("опасная правка", "bjorn")
    st = runner.drive(run_id, office, GW)
    assert st["status"] == "waiting_approval"
    GW._client.post(f"/actions/{st['pending_action_id']}/reject", params={"approver": "don"})
    st = runner.continue_run(run_id, office, GW)
    assert st["status"] == "done"
    assert st["history"][-1]["status"] == "rejected"


def test_done_immediately():
    office = office_running(['{"done":true,"summary":"нечего делать"}'])
    run_id = runner.create_run("ничего", "ingrid")
    st = runner.drive(run_id, office, GW)
    assert st["status"] == "done"
    assert st["history"] == []


def test_step_limit(monkeypatch):
    # Агент бесконечно читает (AUTO) — петля упрётся в лимит, а не зациклится.
    monkeypatch.setattr(runner, "MAX_STEPS", 3)
    office = office_running([
        '{"action":"read_file","target":"repo","params":{"path":"a"},"reason":"x"}',
    ] * 10)
    run_id = runner.create_run("читай вечно", "bjorn")
    st = runner.drive(run_id, office, GW)
    assert st["status"] == "stopped"
    assert st["steps"] == 3
