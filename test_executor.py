"""
Тесты локального исполнителя и его связки со шлюзом.
Запуск:  cd office && python -m pytest -q
"""
import os
import pathlib
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).parent
os.environ["ONTOLOGY_PATH"] = str(ROOT / "ontology.yaml")
_audit = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_audit.close()
os.environ["AUDIT_DB_PATH"] = _audit.name

from executor import LocalExecutor, StubExecutor, get_executor  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "hello.txt").write_text("привет мир", encoding="utf-8")
    return tmp_path


def test_read_and_write_roundtrip(repo):
    ex = LocalExecutor(repo)
    assert "привет" in ex.run("read_file", "x", {"path": "hello.txt"})
    ex.run("write_file", "x", {"path": "sub/new.txt", "content": "данные"})
    assert (repo / "sub" / "new.txt").read_text(encoding="utf-8") == "данные"


def test_path_traversal_is_blocked(repo):
    ex = LocalExecutor(repo)
    with pytest.raises(PermissionError):
        ex.run("read_file", "x", {"path": "../../etc/passwd"})
    with pytest.raises(PermissionError):
        ex.run("write_file", "x", {"path": "/tmp/evil", "content": "x"})


def test_search_code(repo):
    ex = LocalExecutor(repo)
    assert "hello.txt" in ex.run("search_code", "x", {"pattern": "привет"})


def test_constrained_command_runs(repo):
    ex = LocalExecutor(repo, test_cmd="python3 -c \"print('ok-tests')\"")
    out = ex.run("run_tests", "x", {})
    assert "ok-tests" in out and "exit=0" in out


def test_git_branch_and_commit(repo):
    ex = LocalExecutor(repo)
    assert "exit=0" in ex.run("create_branch", "x", {"branch": "feature/x"})
    (repo / "a.txt").write_text("a", encoding="utf-8")
    assert "exit=0" in ex.run("commit", "x", {"message": "add a"})


def test_critical_action_not_silently_executed(repo):
    ex = LocalExecutor(repo)
    with pytest.raises(NotImplementedError):
        ex.run("deploy", "syndi-vercel", {})


def test_factory_defaults_to_stub_without_workspace(monkeypatch):
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    assert isinstance(get_executor(), StubExecutor)


def test_gateway_executes_through_local_executor(repo, monkeypatch):
    # write_file теперь HIGH: предложение → pending → одобрение человека → запись.
    monkeypatch.setenv("WORKSPACE_ROOT", str(repo))
    from fastapi.testclient import TestClient
    import action_service
    client = TestClient(action_service.app)
    r = client.post("/actions/propose", json={
        "agent_id": "bjorn", "action": "write_file", "target": repo.name,
        "params": {"path": "from_gateway.txt", "content": "через шлюз"}})
    body = r.json()
    assert body["status"] == "pending"                       # не записалось само
    assert not (repo / "from_gateway.txt").exists()
    a = client.post(f"/actions/{body['id']}/approve", params={"approver": "don"})
    assert a.json()["status"] == "executed"                  # записалось после человека
    assert (repo / "from_gateway.txt").read_text(encoding="utf-8") == "через шлюз"


def test_gateway_records_failed_on_executor_error(repo, monkeypatch):
    # Сбой реального исполнителя (нет файла) → шлюз пишет 'failed', не падает.
    monkeypatch.setenv("WORKSPACE_ROOT", str(repo))
    from fastapi.testclient import TestClient
    import action_service
    client = TestClient(action_service.app)
    r = client.post("/actions/propose", json={
        "agent_id": "bjorn", "action": "read_file", "target": repo.name,
        "params": {"path": "no_such.txt"}})
    assert r.json()["status"] == "failed"
