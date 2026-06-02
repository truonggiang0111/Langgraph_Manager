import pytest


@pytest.fixture(autouse=True)
def run_actions_inline(monkeypatch):
    monkeypatch.setenv("LANGGRAPH_INLINE_ACTIONS", "1")
