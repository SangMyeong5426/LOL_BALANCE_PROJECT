"""설정 로딩 테스트.

환경이 제대로 섰는지 확인하는 최소 그물이다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from patchlens.config import DEFAULT_LLM_MODEL, DEFAULT_SEED, load_settings

_ENV_KEYS = ("PATCHLENS_SEED", "PATCHLENS_LLM_MODEL", "ANTHROPIC_API_KEY")


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
    monkeypatch.setenv("PATCHLENS_SEED", "7")
    monkeypatch.setenv("PATCHLENS_LLM_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    settings = load_settings(env_file=clean_env)

    assert settings.seed == 7
    assert settings.llm_model == "claude-sonnet-5"
    assert settings.llm_available is True


def test_blank_values_fall_back(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.env.example` 을 그대로 복사해 빈 값이 남은 흔한 경우."""
    monkeypatch.setenv("PATCHLENS_SEED", "")
    monkeypatch.setenv("PATCHLENS_LLM_MODEL", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    settings = load_settings(env_file=clean_env)

    assert settings.seed == DEFAULT_SEED
    assert settings.llm_model == DEFAULT_LLM_MODEL
    assert settings.anthropic_api_key is None


def test_non_numeric_seed_raises(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """조용히 기본값으로 떨어지면 분할이 바뀐 것을 아무도 모른다."""
    monkeypatch.setenv("PATCHLENS_SEED", "abc")

    with pytest.raises(ValueError, match="PATCHLENS_SEED"):
        load_settings(env_file=clean_env)


def test_settings_are_frozen(clean_env: Path) -> None:
    settings = load_settings(env_file=clean_env)

    with pytest.raises(AttributeError):
        settings.seed = 1  # type: ignore[misc]
