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
