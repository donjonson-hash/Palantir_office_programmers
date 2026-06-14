"""
Тесты фазы code review (viktor / приёмка на Claude).
Запуск:  cd office && python -m pytest -q
"""
import os
import pathlib
import subprocess
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
import review  # noqa: E402
from agents import Gateway, build_office  # noqa: E402
from llm import FakeLLM, ScriptedLLM  # noqa: E402

GW = Gateway(base_url="", client=TestClient(action_service.app))


@pytest.fixture
def git_workspace(tmp_path, monkeypatch):
    """Временный git-репозиторий с одним изменённым файлом."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "base.txt").write_text("base", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
    # неотслеживаемый новый файл — его ревью и увидит
    (tmp_path / "route.ts").write_text("export async function GET() {}", encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


def _cursor() -> int:
    evts = events.list_events(after=0, limit=100_000)
    return evts[-1]["id"] if evts else 0


PASS = '{"verdict":"pass","issues":[]}'
PASS_WITH_ADVICE = ('{"verdict":"pass","issues":['
                    '{"severity":"minor","file":"route.ts","problem":"назови понятнее"}]}')
FAIL = ('{"verdict":"fail","issues":['
        '{"severity":"critical","file":"route.ts","problem":"потерян POST-обработчик"}]}')


def test_review_pass_lets_project_finish(git_workspace):
    start = _cursor()
    r = review.review("p1", "цель", str(git_workspace), FakeLLM(PASS))
    assert r["verdict"] == "pass"
    kinds = [e["kind"] for e in events.list_events(after=start)]
    assert "review_started" in kinds and "review_passed" in kinds


def test_review_advisory_does_not_block(git_workspace):
    start = _cursor()
    r = review.review("p2", "цель", str(git_workspace), FakeLLM(PASS_WITH_ADVICE))
    assert r["verdict"] == "pass"          # minor не валит
    kinds = [e["kind"] for e in events.list_events(after=start)]
    assert "review_note" in kinds          # но совет виден в ленте
    assert "review_passed" in kinds


def test_review_critical_fails(git_workspace):
    start = _cursor()
    r = review.review("p3", "цель", str(git_workspace), FakeLLM(FAIL))
    assert r["verdict"] == "fail"
    assert len(r["criticals"]) == 1
    vf = next(e for e in events.list_events(after=start) if e["kind"] == "review_failed")
    assert "POST" in vf["payload"]["criticals"][0]["problem"]


def test_review_phase_skipped_without_workspace(monkeypatch):
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    office = build_office(ScriptedLLM([]), GW)
    office["viktor"].llm = FakeLLM(FAIL)   # даже если ревьюер сказал бы fail —
    # без workspace фаза не запускается и не блокирует проект
    ok = planner._review_phase("p4", "цель", [{"agent": "bjorn"}],
                               office, GW, lambda a: "")
    assert ok is True


def test_review_phase_critical_then_fixed(git_workspace, monkeypatch):
    # viktor: fail → [fixer чинит] → pass. Подзадачи уже выполнены.
    start = _cursor()
    # viktor отвечает по очереди: сначала fail, потом pass
    viktor_llm = ScriptedLLM([FAIL, PASS])
    office = build_office(ScriptedLLM(['{"done":true,"summary":"исправил"}'] * 5), GW)
    office["viktor"].llm = viktor_llm

    ok = planner._review_phase("p5", "цель",
                               [{"agent": "bjorn"}], office, GW,
                               lambda a: "")
    assert ok is True
    kinds = [e["kind"] for e in events.list_events(after=start) if e["plan_id"] == "p5"]
    assert "review_failed" in kinds
    assert kinds.count("fix_iteration") == 1
    assert kinds[-1] == "review_passed"


def test_review_phase_exhausts_and_fails(git_workspace, monkeypatch):
    monkeypatch.setattr(planner, "MAX_REVIEW_FIXES", 1)
    office = build_office(ScriptedLLM(['{"done":true,"summary":"типа починил"}'] * 5), GW)
    office["viktor"].llm = FakeLLM(FAIL)   # ревью всегда падает

    ok = planner._review_phase("p6", "цель",
                               [{"agent": "bjorn"}], office, GW, lambda a: "")
    assert ok is False
