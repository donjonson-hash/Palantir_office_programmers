"""
Тесты Этапа 4 — самопроверка: контроль качества вместо человека.
Запуск:  cd office && python -m pytest -q
"""
import os
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).parent
os.environ["ONTOLOGY_PATH"] = str(ROOT / "ontology.yaml")
for var in ("AUDIT_DB_PATH", "RUN_DB_PATH", "EVENTS_DB_PATH",
            "OFFICE_DB_PATH", "PLANS_DB_PATH", "NOTES_DB_PATH"):
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    os.environ[var] = f.name

from fastapi.testclient import TestClient  # noqa: E402
import pytest  # noqa: E402
import action_service  # noqa: E402
import events  # noqa: E402
import planner  # noqa: E402
from agents import Gateway, build_office  # noqa: E402
from llm import ScriptedLLM  # noqa: E402

GW = Gateway(base_url="", client=TestClient(action_service.app))
WORKERS = {"bjorn": "Backend", "elsa": "Frontend", "ingrid": "QA"}

PLAN_1 = ('{"subtasks":[{"agent":"bjorn","title":"Код","description":"написать код",'
          '"acceptance":"код есть","depends_on":[]}]}')


@pytest.fixture(autouse=True)
def _stub_executor(monkeypatch):
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)


def _cursor() -> int:
    evts = events.list_events(after=0, limit=100_000)
    return evts[-1]["id"] if evts else 0


class FlakyChecks:
    """Имитация исполнителя: проверки падают first_failures раз, потом зелёные."""

    def __init__(self, first_failures: int):
        self.failures_left = first_failures

    def __call__(self, action, target, params):
        if action in planner.VERIFY_CHECKS:
            if self.failures_left > 0:
                self.failures_left -= 1
                return "exit=1\nFAIL tests/app.test.ts: ожидалось 200, получено 500"
            return "exit=0\nвсё зелёное"
        return f"[stub] {action}"


def test_verification_failure_triggers_fix_then_passes(monkeypatch):
    # Проверки падают один раунд (2 проверки) → bjorn чинит → раунд 2 зелёный.
    start = _cursor()
    monkeypatch.setattr(action_service, "execute", FlakyChecks(first_failures=2))
    office = build_office(ScriptedLLM([
        '{"done":true,"summary":"код написан"}',                      # подзадача
        '{"action":"write_file","target":"repo",'
        '"params":{"path":"fix.ts","content":"исправлено"},"reason":"чиню тест"}',
        '{"done":true,"summary":"починил"}',                          # fix-прогон
    ]), GW)
    plan_id = planner.make_plan("цель", ScriptedLLM([PLAN_1]), WORKERS)

    plan = planner.run_project(plan_id, office, GW)

    assert plan["status"] == "done"
    kinds = [e["kind"] for e in events.list_events(after=start)
             if e["plan_id"] == plan_id]
    assert "verify_failed" in kinds
    assert "fix_iteration" in kinds
    assert kinds[-2:] == ["verify_passed", "project_done"]
    # Вывод проваленной проверки виден в ленте — диагноз, а не просто «упало».
    vf = next(e for e in events.list_events(after=start)
              if e["kind"] == "verify_failed")
    assert "ожидалось 200" in vf["payload"]["report"]


def test_exhausted_fix_iterations_fail_project_honestly(monkeypatch):
    # Проверки падают всегда → после MAX_FIX_ITERATIONS — project_failed.
    start = _cursor()
    monkeypatch.setattr(planner, "MAX_FIX_ITERATIONS", 2)
    monkeypatch.setattr(action_service, "execute", FlakyChecks(first_failures=999))
    office = build_office(ScriptedLLM(
        ['{"done":true,"summary":"якобы починил"}'] * 10), GW)
    plan_id = planner.make_plan("цель", ScriptedLLM([PLAN_1]), WORKERS)

    plan = planner.run_project(plan_id, office, GW)

    assert plan["status"] == "failed"
    assert "самопроверка не прошла" in plan["summary"]
    kinds = [e["kind"] for e in events.list_events(after=start)
             if e["plan_id"] == plan_id]
    assert kinds.count("fix_iteration") == 2
    assert kinds[-1] == "project_failed"


def test_failed_fix_run_stops_verification(monkeypatch):
    # Если сам fix-прогон провалился (LLM шлёт мусор) — не зацикливаемся.
    monkeypatch.setattr(action_service, "execute", FlakyChecks(first_failures=999))
    office = build_office(ScriptedLLM(
        ['{"done":true,"summary":"код"}'] + ["мусор"] * 30), GW)
    plan_id = planner.make_plan("цель", ScriptedLLM([PLAN_1]), WORKERS)
    plan = planner.run_project(plan_id, office, GW)
    assert plan["status"] == "failed"


def test_fixer_is_last_write_capable_agent():
    office = build_office(ScriptedLLM([]), GW)
    subtasks = [{"agent": "bjorn"}, {"agent": "elsa"}, {"agent": "ingrid"}]
    # ingrid не умеет write_file → чинит elsa (последний пишущий).
    assert planner._fixer(subtasks, office) == "elsa"
    assert planner._fixer([{"agent": "ingrid"}], office) == "bjorn"  # fallback
