"""
Глобальная изоляция тестов офиса: без WORKSPACE_ROOT/CMD_TIMEOUT из .env, чтобы
action-тесты не запускали реальные npm/git и не висели до таймаута. Тесты,
которым нужен workspace, задают его сами через monkeypatch.setenv (перекрывает).
Executor не трогаем — его проверяют test_executor и тесты post_note.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_office(monkeypatch):
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("CMD_TIMEOUT", raising=False)
