"""
Тесты HTTP-фасада офиса.
Запуск:  cd office && python -m pytest -q
"""
import os
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).parent
os.environ["ONTOLOGY_PATH"] = str(ROOT / "ontology.yaml")
for var in ("AUDIT_DB_PATH", "OFFICE_DB_PATH"):
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    os.environ[var] = f.name

from fastapi.testclient import TestClient  # noqa: E402
import action_service  # noqa: E402
import office_server  # noqa: E402
from agents import Gateway, build_office  # noqa: E402
from dispatcher import Dispatcher  # noqa: E402
from llm import FakeLLM  # noqa: E402

GW = Gateway(base_url="", client=TestClient(action_service.app))
office = TestClient(office_server.app)


def _inject(action_json: str, routing_json: str) -> None:
    office_server._dispatcher = Dispatcher(
        build_office(FakeLLM(action_json), GW), FakeLLM(routing_json))


def test_list_agents_from_ontology():
    names = [a["name"] for a in office.get("/office/agents").json()["agents"]]
    assert "kristina" in names and "sven" in names


def test_submit_task_routes_and_proposes():
    _inject('{"action":"run_tests","target":"syndi-vercel","params":{},"reason":"проверка"}',
            '{"agent":"ingrid","reason":"это QA"}')
    body = office.post("/office/task", json={"task": "прогони тесты"}).json()
    assert body["routed_to"] == "ingrid"
    assert body["result"]["gateway"]["status"] == "executed"
    assert "task_id" in body


def test_task_is_logged_and_listable():
    _inject('{"action":"deploy","target":"syndi-vercel","params":{},"reason":"релиз"}',
            '{"agent":"sven","reason":"деплой"}')
    out = office.post("/office/task", json={"task": "выкати на прод"}).json()
    tasks = office.get("/office/tasks").json()["tasks"]
    rec = next(t for t in tasks if t["id"] == out["task_id"])
    assert rec["routed_to"] == "sven"
    assert rec["action"] == "deploy"
    assert rec["status"] == "pending"   # CRITICAL → ждёт одобрения


def test_ontology_mirror_exposes_policy():
    o = office.get("/office/ontology").json()
    # тиры, каталог действий и штат видны центру
    assert "HIGH" in o["risk_tiers"] and o["risk_tiers"]["HIGH"]["requires_approval"] is True
    names = {a["name"] for a in o["action_types"]}
    assert {"write_file", "deploy", "read_file"} <= names
    wf = next(a for a in o["action_types"] if a["name"] == "write_file")
    assert wf["tier"] == "HIGH"
