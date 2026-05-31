"""
Тесты инварианта: тир определяет онтология, а не агент.
Запуск:  cd office && python -m pytest -q
"""
import os
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).parent
os.environ["ONTOLOGY_PATH"] = str(ROOT / "ontology.yaml")
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["AUDIT_DB_PATH"] = _tmp.name

from fastapi.testclient import TestClient  # noqa: E402
import action_service  # noqa: E402

client = TestClient(action_service.app)


def test_auto_action_executes_immediately():
    r = client.post("/actions/propose", json={
        "agent_id": "bjorn", "action": "read_file",
        "target": "AI-avatar_command", "params": {"path": "README.md"}})
    assert r.status_code == 200
    assert r.json()["tier"] == "AUTO"
    assert r.json()["status"] == "executed"


def test_critical_action_requires_human_approval():
    r = client.post("/actions/propose", json={
        "agent_id": "sven", "action": "deploy", "target": "syndi-vercel"})
    body = r.json()
    assert body["tier"] == "CRITICAL"
    assert body["status"] == "pending"            # не исполнилось само
    a = client.post(f"/actions/{body['id']}/approve", params={"approver": "don"})
    assert a.json()["status"] == "executed"       # исполнилось только после человека


def test_unknown_action_is_rejected():
    r = client.post("/actions/propose", json={
        "agent_id": "elsa", "action": "rm_rf_root", "target": "syndi-vercel"})
    assert r.status_code == 422                    # нельзя предложить незаявленное


def test_agent_cannot_set_its_own_tier():
    # Агент подсовывает requires_approval=false в params — поле игнорируется.
    # merge_pr — тир HIGH в онтологии → всё равно очередь на одобрение.
    # (sven вправе делать merge_pr; полномочия проверяются отдельно.)
    r = client.post("/actions/propose", json={
        "agent_id": "sven", "action": "merge_pr", "target": "PR-42",
        "params": {"requires_approval": False}})
    assert r.json()["tier"] == "HIGH"
    assert r.json()["status"] == "pending"


def test_gateway_enforces_permissions_even_if_agent_bypasses_guard():
    # Прямой вызов шлюза в обход агент-side проверки: QA шлёт deploy.
    r = client.post("/actions/propose", json={
        "agent_id": "ingrid", "action": "deploy", "target": "syndi-vercel"})
    assert r.status_code == 403
    # Попытка зафиксирована в провенансе как событие безопасности.
    audit = client.get("/actions/audit").json()["audit"]
    assert any(a["status"] == "rejected_forbidden" and a["agent_id"] == "ingrid"
               for a in audit)


def test_unknown_agent_is_denied():
    r = client.post("/actions/propose", json={
        "agent_id": "ghost", "action": "read_file", "target": "x"})
    assert r.status_code == 403


def test_write_file_requires_approval():
    # Замок: запись файлов — тир HIGH, исполняется только после человека.
    r = client.post("/actions/propose", json={
        "agent_id": "bjorn", "action": "write_file", "target": "syndi-vercel",
        "params": {"path": "x.txt", "content": "y"}})
    assert r.json()["tier"] == "HIGH"
    assert r.json()["status"] == "pending"
