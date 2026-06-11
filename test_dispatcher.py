"""
Тесты диспетчера (Kristina) и полного пути задача → агент → шлюз.
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
_tmp2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp2.close()
os.environ["EVENTS_DB_PATH"] = _tmp2.name

from fastapi.testclient import TestClient  # noqa: E402
import action_service  # noqa: E402
from agents import Gateway, build_office  # noqa: E402
from dispatcher import Dispatcher  # noqa: E402
from llm import FakeLLM  # noqa: E402

GW = Gateway(base_url="", client=TestClient(action_service.app))


def make(action_json: str, routing_json: str, default: str = "bjorn") -> Dispatcher:
    # Раздельные «мозги»: агенты выбирают действие, Kristina — исполнителя.
    office = build_office(FakeLLM(action_json), GW)
    return Dispatcher(office, FakeLLM(routing_json), default_agent=default)


def test_routes_to_chosen_agent_and_proposes_end_to_end():
    d = make(
        action_json='{"action":"run_tests","target":"syndi-vercel","params":{},"reason":"проверка"}',
        routing_json='{"agent":"ingrid","reason":"это QA"}')
    out = d.handle("прогони тесты")
    assert out["routed_to"] == "ingrid"
    assert out["result"]["gateway"]["tier"] == "LOW"
    assert out["result"]["gateway"]["status"] == "executed"


def test_irreversible_action_stopped_even_through_dispatcher():
    # Деплой не выдан никому: даже корректно смаршрутизированная задача
    # упирается в предохранитель роли — необратимое не исполняется.
    d = make(
        action_json='{"action":"deploy","target":"syndi-vercel","params":{},"reason":"релиз"}',
        routing_json='{"agent":"sven","reason":"это деплой"}')
    out = d.handle("выкати свежую версию на прод")
    assert out["routed_to"] == "sven"
    assert out["result"]["status"] == "forbidden"


def test_fallback_when_llm_returns_garbage():
    d = make(
        action_json='{"action":"run_tests","target":"syndi-vercel","params":{},"reason":"проверка"}',
        routing_json='это вообще не json')
    out = d.handle("прогони тесты и проверь покрытие")   # ключевые слова → ingrid
    assert out["routed_to"] == "ingrid"
    assert out["result"]["gateway"]["status"] == "executed"


def test_unknown_routed_agent_falls_back():
    d = make(
        action_json='{"action":"read_file","target":"x","params":{},"reason":"."}',
        routing_json='{"agent":"superman","reason":"героически"}')
    out = d.handle("посмотри бэкенд")                    # superman невалиден → fallback → bjorn
    assert out["routed_to"] == "bjorn"


def test_kristina_is_never_a_worker():
    d = make(action_json='{"action":null,"reason":"."}',
             routing_json='{"agent":"kristina","reason":"сам сделаю"}')
    out = d.handle("что-то совсем непонятное")           # kristina не worker → fallback → default
    assert out["routed_to"] != "kristina"
