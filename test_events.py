"""
Тесты «стекла»: события публикуются на каждом значимом шаге и читаются курсором.
Запуск:  cd office && python -m pytest -q
"""
import os
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).parent
os.environ["ONTOLOGY_PATH"] = str(ROOT / "ontology.yaml")
for var in ("AUDIT_DB_PATH", "RUN_DB_PATH", "EVENTS_DB_PATH", "OFFICE_DB_PATH"):
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    os.environ[var] = f.name

from fastapi.testclient import TestClient  # noqa: E402
import pytest  # noqa: E402
import action_service  # noqa: E402
import events  # noqa: E402
import office_server  # noqa: E402
import runner  # noqa: E402
from agents import Gateway, build_office  # noqa: E402
from llm import ScriptedLLM  # noqa: E402

GW = Gateway(base_url="", client=TestClient(action_service.app))
office = TestClient(office_server.app)


@pytest.fixture(autouse=True)
def _stub_executor(monkeypatch):
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)


def _cursor() -> int:
    evts = events.list_events(after=0, limit=10_000)
    return evts[-1]["id"] if evts else 0


def test_publish_and_cursor_pagination():
    start = _cursor()
    events.publish("run_started", agent="bjorn", run_id="r1", goal="тест")
    events.publish("action_executed", agent="bjorn", run_id="r1", action="read_file")
    evts = events.list_events(after=start)
    assert [e["kind"] for e in evts] == ["run_started", "action_executed"]
    # Курсор: после последнего id — пусто.
    assert events.list_events(after=evts[-1]["id"]) == []


def test_run_produces_full_event_chain():
    # Прогон: read_file → write_file → done. В ленте должна быть вся цепочка,
    # скоррелированная по run_id, с reason каждого шага.
    start = _cursor()
    office_agents = build_office(ScriptedLLM([
        '{"action":"read_file","target":"repo","params":{"path":"a"},"reason":"осмотреться"}',
        '{"action":"write_file","target":"repo","params":{"path":"b","content":"c"},"reason":"правка"}',
        '{"done":true,"summary":"готово"}',
    ]), GW)
    run_id = runner.create_run("сделай правку", "bjorn")
    st = runner.drive(run_id, office_agents, GW)
    assert st["status"] == "done"

    evts = [e for e in events.list_events(after=start) if e["run_id"] == run_id]
    kinds = [e["kind"] for e in evts]
    assert kinds == ["run_started", "action_executed", "action_executed", "run_done"]
    # Видно ЧТО и ПОЧЕМУ делал агент — это и есть «стекло».
    write_evt = evts[2]
    assert write_evt["agent"] == "bjorn"
    assert write_evt["payload"]["action"] == "write_file"
    assert write_evt["payload"]["reason"] == "правка"
    assert evts[3]["payload"]["summary"] == "готово"


def test_failed_action_is_visible_in_feed(monkeypatch):
    # Сбой исполнителя не теряется: в ленте появляется action_failed с ошибкой.
    start = _cursor()
    monkeypatch.setattr(action_service, "execute",
                        lambda a, t, p: (_ for _ in ()).throw(RuntimeError("диск полон")))
    GW.propose("bjorn", "write_file", "repo", {"path": "x", "content": "y"},
               run_id="rfail", reason="попытка")
    evts = [e for e in events.list_events(after=start) if e["run_id"] == "rfail"]
    assert evts[0]["kind"] == "action_failed"
    assert "диск полон" in evts[0]["payload"]["error"]


def test_office_events_endpoint_polls_with_cursor():
    start = _cursor()
    events.publish("task_routed", agent="kristina", routed_to="elsa", reason="ui")
    body = office.get(f"/office/events?after={start}").json()
    assert body["events"][-1]["kind"] == "task_routed"
    assert body["last_id"] > start
    # Повторный поллинг с новым курсором — без дублей.
    again = office.get(f"/office/events?after={body['last_id']}").json()
    assert again["events"] == []
    assert again["last_id"] == body["last_id"]


def test_notes_endpoint_exposes_blackboard():
    import blackboard
    blackboard.add("bjorn", "контракт: GET /api/x")
    notes = office.get("/office/notes").json()["notes"]
    assert any("GET /api/x" in n["text"] and n["agent"] == "bjorn" for n in notes)
