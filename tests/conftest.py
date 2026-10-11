import pytest
from app.core.config import settings


@pytest.fixture(autouse=True)
def offline_models_for_unit_tests(monkeypatch):
    """Unit tests must not silently incur provider costs from the user's .env."""
    monkeypatch.setattr(settings, "rag_provider", "local")
    monkeypatch.setattr(settings, "rag_embedding_url", None)
    monkeypatch.setattr(settings, "rag_generation_enabled", False)
    monkeypatch.setattr(settings, "rag_min_similarity", 0.12)
    monkeypatch.setattr(settings, "rag_rewrite_enabled", False)
    monkeypatch.setattr(settings, "rag_rerank_enabled", False)
    monkeypatch.setattr(settings, "rag_self_rag_enabled", False)
    monkeypatch.setattr(settings, "agent_tool_transport", "local")


@pytest.fixture(autouse=True)
def legacy_write_stage_for_existing_tests(request,monkeypatch):
    # Existing stage5 tests explicitly exercise writes, independently of production's readonly default.
    if request.module.__name__.endswith(("test_stage5","test_api")):
        from app.services.rollout import RolloutConfig
        monkeypatch.setattr("app.services.rollout.configuration",lambda db:RolloutConfig(stage="controlled"))
