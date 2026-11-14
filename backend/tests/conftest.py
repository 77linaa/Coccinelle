import pytest


@pytest.fixture(autouse=True)
def _no_real_llm_calls(monkeypatch):
    """Tests never reach the Anthropic API: no cost, no network, no flakiness."""
    monkeypatch.setenv("SENTINEL_AI", "off")
