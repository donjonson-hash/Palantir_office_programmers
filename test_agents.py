"""
Тесты привязки агентов к шлюзу.
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
from agents import Gateway, build_office  # noqa: E402
from llm import FakeLLM  # noqa: E402

GW = Gateway(base_url="", client=TestClient(action_service.app))


def office_with(canned: str):
    return build_office(FakeLLM(canned), GW)


def test_agent_proposes_auto_action_through_gateway():
    office = office_with(
        '{"action":"read_file","target":"AI-avatar_command",'
        '"params":{"path":"README.md"},"reason":"посмотреть"}')
    out = office["bjorn"].act("прочитай README")
    assert out["gateway"]["tier"] == "AUTO"
    assert out["gateway"]["status"] == "executed"


def test_risky_action_goes_to_approval_queue():
    office = office_with(
        '{"action":"deploy","target":"syndi-vercel","params":{},"reason":"релиз"}')
    out = office["sven"].act("выкати на прод")
    assert out["gateway"]["tier"] == "CRITICAL"
    assert out["gateway"]["status"] == "pending"   # человек одобрит из центра


def test_agent_cannot_propose_outside_its_role():
    # QA не имеет права на deploy — действие не доходит до шлюза.
    office = office_with(
        '{"action":"deploy","target":"syndi-vercel","params":{},"reason":"хочу"}')
    out = office["ingrid"].act("задеплой")
    assert out["status"] == "forbidden"
    assert "gateway" not in out


def test_backend_dev_cannot_force_push():
    office = office_with(
        '{"action":"force_push","target":"syndi-vercel","params":{},"reason":"быстрее"}')
    out = office["bjorn"].act("перезапиши историю")
    assert out["status"] == "forbidden"


def test_agent_has_no_direct_execution_path():
    office = office_with('{"action":null,"reason":"нет действия"}')
    assert not hasattr(office["bjorn"], "execute")
