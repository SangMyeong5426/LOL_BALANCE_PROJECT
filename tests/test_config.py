"""설정 로딩 테스트.

환경이 제대로 섰는지 확인하는 최소 그물이다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lol_balance.config import (
    DEFAULT_AGENT_MODEL,
    DEFAULT_LLM_MODEL,
    DEFAULT_SEED,
    load_settings,
)

_ENV_KEYS = (
    "LOL_BALANCE_SEED",
    "LOL_BALANCE_LLM_MODEL",
    "LOL_BALANCE_AGENT_MODEL",
    "ANTHROPIC_API_KEY",
    "RIOT_API_KEY",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """실제 `.env` 와 셸 환경에서 테스트를 떼어 놓는다."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return tmp_path / "absent.env"


def test_defaults_when_nothing_set(clean_env: Path) -> None:
    settings = load_settings(env_file=clean_env)

    assert settings.seed == DEFAULT_SEED
    assert settings.llm_model == DEFAULT_LLM_MODEL
    assert settings.anthropic_api_key is None


def test_llm_is_not_required_to_run(clean_env: Path) -> None:
    """키가 없어도 설정은 만들어진다 — 수집·집계가 LLM 없이 돈다는 뜻이다."""
    assert load_settings(env_file=clean_env).llm_available is False


def test_env_overrides_defaults(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOL_BALANCE_SEED", "7")
    monkeypatch.setenv("LOL_BALANCE_LLM_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    settings = load_settings(env_file=clean_env)

    assert settings.seed == 7
    assert settings.llm_model == "claude-sonnet-5"
    assert settings.llm_available is True


def test_blank_values_fall_back(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.env.example` 을 그대로 복사해 빈 값이 남은 흔한 경우."""
    monkeypatch.setenv("LOL_BALANCE_SEED", "")
    monkeypatch.setenv("LOL_BALANCE_LLM_MODEL", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    settings = load_settings(env_file=clean_env)

    assert settings.seed == DEFAULT_SEED
    assert settings.llm_model == DEFAULT_LLM_MODEL
    assert settings.anthropic_api_key is None


def test_non_numeric_seed_raises(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """조용히 기본값으로 떨어지면 분할이 바뀐 것을 아무도 모른다."""
    monkeypatch.setenv("LOL_BALANCE_SEED", "abc")

    with pytest.raises(ValueError, match="LOL_BALANCE_SEED"):
        load_settings(env_file=clean_env)


def test_agent_runs_on_a_local_model_by_default(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**키가 없으면** 에이전트의 기본은 로컬 모델이다. 빈 값도 기본값으로 읽는다.

    키가 있으면 유료로 올라간다 — `test_agent_model_follows_the_key` 가 그쪽을 본다.
    여기서는 키를 지우고 「없어도 돈다」만 본다(ADR 0009).
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = load_settings(env_file=clean_env)
    assert settings.agent_model == DEFAULT_AGENT_MODEL
    assert settings.agent_model.startswith("ollama:")
    assert settings.llm_available is False

    monkeypatch.setenv("LOL_BALANCE_AGENT_MODEL", "")
    assert load_settings(env_file=clean_env).agent_model == DEFAULT_AGENT_MODEL

    monkeypatch.setenv("LOL_BALANCE_AGENT_MODEL", "ollama:qwen3.5:2b")
    assert load_settings(env_file=clean_env).agent_model == "ollama:qwen3.5:2b"


def test_riot_key_is_optional(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """직접 집계에만 쓰는 키다 — 없어도 설정은 서고, 빈 값은 없는 것으로 읽는다."""
    assert load_settings(env_file=clean_env).riot_api_key is None

    monkeypatch.setenv("RIOT_API_KEY", "  ")
    assert load_settings(env_file=clean_env).riot_api_key is None

    monkeypatch.setenv("RIOT_API_KEY", " RGAPI-test ")
    assert load_settings(env_file=clean_env).riot_api_key == "RGAPI-test"


def test_settings_are_frozen(clean_env: Path) -> None:
    settings = load_settings(env_file=clean_env)

    with pytest.raises(AttributeError):
        settings.seed = 1  # type: ignore[misc]


def test_agent_model_follows_the_key(monkeypatch, tmp_path):
    """**키가 있으면 유료, 없으면 로컬.** 키 없는 사람도 그대로 돌아가야 한다."""
    from lol_balance.config import (
        LOCAL_AGENT_MODEL,
        PAID_AGENT_MODEL,
        default_agent_model,
        load_settings,
    )

    assert default_agent_model(True) == PAID_AGENT_MODEL
    assert default_agent_model(False) == LOCAL_AGENT_MODEL

    empty = tmp_path / ".env"
    empty.write_text("")
    monkeypatch.delenv("LOL_BALANCE_AGENT_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert load_settings(empty).agent_model == LOCAL_AGENT_MODEL
    monkeypatch.setenv("OPENAI_API_KEY", "sk-테스트용-가짜")
    assert load_settings(empty).agent_model == PAID_AGENT_MODEL


def test_an_explicit_model_beats_the_key(monkeypatch, tmp_path):
    """손으로 적은 것이 항상 이긴다 — 유료 키가 있어도 로컬로 돌릴 수 있다."""
    from lol_balance.config import load_settings

    empty = tmp_path / ".env"
    empty.write_text("")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-테스트용-가짜")
    monkeypatch.setenv("LOL_BALANCE_AGENT_MODEL", "ollama:qwen3.5:2b")
    assert load_settings(empty).agent_model == "ollama:qwen3.5:2b"
