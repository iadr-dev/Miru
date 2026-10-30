"""The local model endpoint setting: default points at Docker Model Runner, env overrides it."""

from __future__ import annotations

import pytest

from config.settings import Settings


def test_local_llm_base_url_defaults_to_model_runner_on_the_host() -> None:
    assert Settings(_env_file=None).local_llm_base_url == "http://localhost:12434/engines"


def test_local_llm_base_url_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://model-runner.docker.internal/engines")
    settings = Settings(_env_file=None)
    assert settings.local_llm_base_url == "http://model-runner.docker.internal/engines"
