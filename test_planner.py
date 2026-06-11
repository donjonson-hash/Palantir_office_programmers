"""
Тесты планировщика и оркестратора проектов.
Запуск:  cd office && python -m pytest -q
"""
import os
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).parent
os.environ["ONTOLOGY_PATH"] = str(ROOT / "ontology.yaml")
for var in ("AUDIT_DB_PATH", "RUN_DB_PATH", "EVENTS_DB_PATH",
            "OFFICE_DB_PATH", "PLANS_DB_PATH"):
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    os.environ[var] = f.name

from fastapi.testclient import TestClient  # noqa: E402
import pytest  # noqa: E402
import action_service  # noqa: E402
import events  # noqa: E402
import office_server  # noqa: E402
import planner  # noqa: E402
import runner  # noqa: E402
from agents import Gateway, build_office  # noqa: E402
from dispatcher import Dispatcher  # noqa: E402
from llm import ScriptedLLM  # noqa: E402

GW = Gateway(base_url="", client=TestClient(action_service.app))
WORKERS = {"bjorn": "Backend", "elsa": "Frontend", "ingrid": "QA"}

PLAN_2 = ('{"subtasks":['
          '{"agent":"bjorn","title":"Каркас","description":"создать каркас",'
          '"acceptance":"файлы созданы","depends_on":[]},'
          '{"agent":"ingrid","title":"Проверка","description":"прогнать тесты",'
          '"acceptance":"тесты зелёные","depends_on":[1]}]}')


@pytest.fixture(autouse=True)
def _stub_executor(monkeypatch):
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)


def _cursor() -> int:
    evts = events.list_events(after=0, limit=100_000)
    return evts[-1]["id"] if evts else 0


def test_plan_is_generated_and_stored():
    plan_id = planner.make_plan("сделай приложение", ScriptedLLM([PLAN_2]), WORKERS)
    plan = planner.get_plan(plan_id)
    assert plan["status"] == "planned"
    assert [s["agent"] for s in plan["subtasks"]] == ["bjorn", "ingrid"]
    assert plan["subtasks"][0]["n"] == 1
    assert plan["subtasks"][0]["status"] == "queued"


def test_plan_retries_on_garbage_then_succeeds():
    llm = ScriptedLLM(["это не json", '{"subtasks":[]}', PLAN_2])
    plan_id = planner.make_plan("цель", llm, WORKERS)
    assert planner.get_plan(plan_id)["status"] == "planned"


def test_plan_fails_visibly_after_retries():
    start = _cursor()
    llm = ScriptedLLM(["мусор"] * planner.PLAN_RETRIES)
    plan_id = planner.make_plan("цель", llm, WORKERS)
    plan = planner.get_plan(plan_id)
    assert plan["status"] == "plan_failed"
    kinds = [e["kind"] for e in events.list_events(after=start)]
    assert "plan_failed" in kinds


def test_plan_rejects_unknown_agent():
    llm = ScriptedLLM(
        ['{"subtasks":[{"agent":"ghost","description":"x"}]}'] * planner.PLAN_RETRIES)
    plan_id = planner.make_plan("цель", llm, WORKERS)
    assert planner.get_plan(plan_id)["status"] == "plan_failed"


def test_project_runs_subtasks_sequentially_to_done():
    # Один ScriptedLLM на офис: подзадача 1 (bjorn) — действие + done,
    # подзадача 2 (ingrid) — done сразу. Полная цепочка событий с plan_id.
    start = _cursor()
    office = build_office(ScriptedLLM([
        '{"action":"write_file","target":"repo","params":{"path":"a","content":"b"},"reason":"каркас"}',
        '{"done":true,"summary":"каркас готов"}',
        '{"action":"run_tests","target":"repo","params":{},"reason":"проверка"}',
        '{"done":true,"summary":"тесты зелёные"}',
    ]), GW)
    plan_id = planner.make_plan("сделай приложение", ScriptedLLM([PLAN_2]), WORKERS)

    plan = planner.run_project(plan_id, office, GW)

    assert plan["status"] == "done"
    assert all(s["status"] == "done" for s in plan["subtasks"])
    assert all(s["run_id"] for s in plan["subtasks"])

    evts = [e for e in events.list_events(after=start) if e["plan_id"] == plan_id]
    kinds = [e["kind"] for e in evts]
    assert kinds == ["plan_created",
                     "subtask_started", "run_started", "subtask_done",
                     "subtask_started", "run_started", "subtask_done",
                     "verify_started", "verify_passed",
                     "project_done"]
    # Раздача видна: кто взял подзадачу 1 и почему проект завершён.
    assert evts[1]["agent"] == "bjorn"
    assert "2 подзадач" in evts[-1]["payload"]["summary"]
    assert "самопроверка зелёная" in evts[-1]["payload"]["summary"]


def test_failed_subtask_stops_project(monkeypatch):
    # Подзадача 1 упирается в лимит шагов → subtask_failed + project_failed,
    # подзадача 2 не стартует.
    start = _cursor()
    monkeypatch.setattr(runner, "MAX_STEPS", 2)
    office = build_office(ScriptedLLM(
        ['{"action":"read_file","target":"repo","params":{"path":"a"},"reason":"x"}'] * 10
    ), GW)
    plan_id = planner.make_plan("цель", ScriptedLLM([PLAN_2]), WORKERS)

    plan = planner.run_project(plan_id, office, GW)

    assert plan["status"] == "failed"
    assert plan["subtasks"][0]["status"] == "failed"
    assert plan["subtasks"][1]["status"] == "queued"   # до второй не дошли
    kinds = [e["kind"] for e in events.list_events(after=start) if e["plan_id"] == plan_id]
    assert kinds[-2:] == ["subtask_failed", "project_failed"]


def test_project_endpoint_plans_and_executes_in_background():
    # POST /office/project: план в ответе сразу, исполнение — фоном
    # (TestClient прогоняет background-задачи до возврата ответа).
    llm = ScriptedLLM([
        PLAN_2,
        '{"done":true,"summary":"каркас готов"}',
        '{"done":true,"summary":"проверено"}',
    ])
    office_server._dispatcher = Dispatcher(build_office(llm, GW), llm)
    client = TestClient(office_server.app)

    body = client.post("/office/project", json={"goal": "сделай приложение"}).json()
    assert body["status"] == "planned"
    assert len(body["subtasks"]) == 2

    state = client.get(f"/office/project/{body['id']}").json()
    assert state["status"] == "done"
    listed = client.get("/office/projects").json()["projects"]
    assert any(p["id"] == body["id"] for p in listed)


def test_plan_prompt_includes_staff_abilities():
    # Kristina видит, ЧТО умеет штат, — план не содержит невыполнимых подзадач.
    from llm import FakeLLM
    fake = FakeLLM(PLAN_2)
    planner.make_plan("цель", fake, WORKERS,
                      abilities={"bjorn": ["write_file", "run_tests"]})
    system_prompt = fake.calls[-1][0]
    assert "bjorn — Backend — [write_file, run_tests]" in system_prompt
    assert "НЕТ способа запускать серверы" in system_prompt
