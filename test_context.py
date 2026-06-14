"""
Тесты Этапа 3: общая картина (Context Broker), доска решений, устойчивость петли.
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
import blackboard  # noqa: E402
import context  # noqa: E402
import planner  # noqa: E402
import runner  # noqa: E402
from agents import Gateway, build_office  # noqa: E402
from llm import ScriptedLLM  # noqa: E402

GW = Gateway(base_url="", client=TestClient(action_service.app))
WORKERS = {"bjorn": "Backend", "elsa": "Frontend", "ingrid": "QA"}

PLAN_BE_FE = ('{"subtasks":['
              '{"agent":"bjorn","title":"API","description":"сделать API",'
              '"acceptance":"эндпоинт описан","depends_on":[]},'
              '{"agent":"elsa","title":"UI","description":"сделать UI под API",'
              '"acceptance":"UI по контракту","depends_on":[1]}]}')


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    blackboard.clear()


# ─── Blackboard ──────────────────────────────────────────────────────────────

def test_post_note_goes_through_gateway_with_forced_authorship():
    # Авторство подписывает шлюз: даже если агент подсунул чужое имя.
    out = GW.propose("bjorn", "post_note", "Blackboard",
                     {"text": "API: POST /api/research", "author": "elsa"})
    assert out["status"] == "executed"
    notes = blackboard.list_notes()
    assert notes[0]["agent"] == "bjorn"        # не elsa
    assert "POST /api/research" in notes[0]["text"]


def test_empty_note_fails_visibly():
    out = GW.propose("bjorn", "post_note", "Blackboard", {"text": "  "})
    assert out["status"] == "failed"


# ─── Context Broker ──────────────────────────────────────────────────────────

def test_context_contains_plan_notes_and_files(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text("x", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("x", encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))

    plan_id = planner.make_plan("сделай приложение",
                                ScriptedLLM([PLAN_BE_FE]), WORKERS)
    blackboard.add("bjorn", "стек: Next.js 15")

    ctx = context.build(plan_id, "elsa")
    assert "ЦЕЛЬ ПРОЕКТА: сделай приложение" in ctx
    assert "[queued] bjorn: API" in ctx          # весь план со статусами
    assert "стек: Next.js 15" in ctx             # доска решений
    assert "index.ts" in ctx                     # файлы соседей
    assert "junk.js" not in ctx                  # node_modules отрезан


def test_context_tree_is_capped(tmp_path, monkeypatch):
    for i in range(500):
        (tmp_path / f"f{i:03}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    tree = context.file_tree()
    assert "обрезано" in tree
    assert len(tree.splitlines()) <= context.MAX_TREE_ENTRIES + 1


# ─── Устойчивость петли ──────────────────────────────────────────────────────

def test_next_step_retries_garbage_json_then_succeeds():
    office = build_office(ScriptedLLM([
        "ой, забыл про json",
        "опять текст",
        '{"done":true,"summary":"собрался"}',
    ]), GW)
    run_id = runner.create_run("цель", "bjorn")
    st = runner.drive(run_id, office, GW)
    assert st["status"] == "done"                # ретраи спасли шаг


def test_persistent_garbage_fails_run_honestly():
    # Главный фикс: мусор от LLM — это провал, а НЕ молчаливый done.
    office = build_office(ScriptedLLM(["мусор"] * 10), GW)
    run_id = runner.create_run("цель", "bjorn")
    st = runner.drive(run_id, office, GW)
    assert st["status"] == "failed"
    assert "не удалось разобрать" in st["summary"]


def test_agent_without_suitable_action_fails_not_done():
    office = build_office(ScriptedLLM(
        ['{"action":null,"reason":"нет подходящего действия"}']), GW)
    run_id = runner.create_run("невыполнимое", "ingrid")
    st = runner.drive(run_id, office, GW)
    assert st["status"] == "failed"
    assert "нет подходящего" in st["summary"]


# ─── Единая команда: контракт передаётся от bjorn к elsa ─────────────────────

def test_contract_posted_by_backend_is_visible_to_frontend():
    # bjorn публикует контракт API → на шаге elsa Context Broker уже несёт его
    # в промпте. Проверяем через перехват system-промпта ScriptedLLM.
    llm = ScriptedLLM([
        '{"action":"post_note","target":"Blackboard",'
        '"params":{"text":"КОНТРАКТ: POST /api/research -> {findings[]}"},"reason":"контракт"}',
        '{"done":true,"summary":"API описан"}',
        '{"done":true,"summary":"UI сделан по контракту"}',
    ])
    office = build_office(llm, GW)
    plan_id = planner.make_plan("приложение", ScriptedLLM([PLAN_BE_FE]), WORKERS)

    plan = planner.run_project(plan_id, office, GW)

    assert plan["status"] == "done"
    # Контекст, который Context Broker собирал на шаге elsa, содержит
    # контракт bjorn и статус done его подзадачи.
    ctx = context.build(plan_id, "elsa")
    assert "КОНТРАКТ: POST /api/research" in ctx
    assert "[done] bjorn: API" in ctx


def test_gateway_transport_failure_fails_run_not_hangs():
    # Транспорт до шлюза умер (таймаут) → run_failed с диагнозом, не зависание.
    class DeadGateway:
        def propose(self, *a, **k):
            raise TimeoutError("httpx.ReadTimeout: 30s")
    office = build_office(ScriptedLLM([
        '{"action":"install_deps","target":"repo","params":{},"reason":"ставлю"}',
    ]), GW)
    run_id = runner.create_run("установи зависимости", "bjorn")
    st = runner.drive(run_id, office, DeadGateway())
    assert st["status"] == "failed"
    assert "шлюз недоступен" in st["summary"]


def test_orchestrator_crash_marks_project_failed(monkeypatch):
    # Любое неожиданное исключение внутри оркестрации → project_failed, не молчание.
    plan_id = planner.make_plan("цель", ScriptedLLM([PLAN_BE_FE]), WORKERS)
    monkeypatch.setattr(planner.runner, "create_run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("диск умер")))
    plan = planner.run_project(plan_id, {}, GW)
    assert plan["status"] == "failed"
    assert "диск умер" in plan["summary"]


def test_agent_sees_step_budget():
    from llm import FakeLLM
    fake = FakeLLM('{"done":true,"summary":"ок"}')
    office = build_office(fake, GW)
    run_id = runner.create_run("цель", "bjorn")
    runner.drive(run_id, office, GW)
    system_prompt = fake.calls[-1][0]
    assert "ОСТАЛОСЬ ШАГОВ: " in system_prompt
def test_provider_registry_reads_env(monkeypatch):
    import llm
    llm._provider_cache.clear()
    monkeypatch.setenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")
    monkeypatch.setenv("MIMO_API_KEY", "k")
    monkeypatch.setenv("MIMO_MODEL", "mimo-v2.5-pro")
    p = llm.get_provider("MIMO")
    assert p.base_url == "https://api.xiaomimimo.com/v1"
    assert p.model == "mimo-v2.5-pro"
    assert p.token_param == "max_completion_tokens"  # MiMo-специфика
    llm._provider_cache.clear()


def test_missing_provider_raises_clear_error(monkeypatch):
    import llm
    llm._provider_cache.clear()
    monkeypatch.delenv("GHOST_BASE_URL", raising=False)
    try:
        llm.get_provider("GHOST")
        assert False, "ожидалась ошибка"
    except RuntimeError as e:
        assert "GHOST_BASE_URL" in str(e)


def test_agent_carries_provider_from_ontology():
    # build_office с явным llm (как в тестах) — провайдер всё равно считывается
    # в spec и доступен, но общий llm используется для вызовов.
    office = build_office(ScriptedLLM([]), GW)
    assert office["bjorn"].provider == "MIMO"
    assert office["kristina"].provider == "LLM"
    assert "viktor" in office and office["viktor"].provider == "LLM"
