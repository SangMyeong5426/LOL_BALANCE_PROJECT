"""실행 환경 설정.

`.env` 에서 읽고 없으면 기본값을 쓴다. 여기서 하는 일은 값을 읽어 오는 것뿐이다.
데이터 출처와 수집 규칙은 `docs/spec/data-sources.md` 가 기준이다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_LLM_MODEL = "claude-opus-5"
DEFAULT_SEED = 20260824


@dataclass(frozen=True)
class Settings:
    """한 번 읽어 고정하는 실행 설정."""

    seed: int
    llm_model: str
    anthropic_api_key: str | None

    @property
    def llm_available(self) -> bool:
        """LLM 호출이 가능한지.

        수집·집계·통계 베이스라인은 이 값이 False 여도 전부 돌아간다.
        LLM 이 없으면 못 하는 것과 있어도 그만인 것을 가르는 경계다.
        """
        return bool(self.anthropic_api_key)


def _read_seed(raw: str | None) -> int:
    if raw is None or raw.strip() == "":
        return DEFAULT_SEED
    try:
        return int(raw)
    except ValueError as exc:
        # 시드가 조용히 기본값으로 떨어지면 학습·평가 분할이 바뀐 것을 아무도 모른다.
        raise ValueError(f"LOL_BALANCE_SEED 는 정수여야 한다: {raw!r}") from exc


def load_settings(env_file: Path | None = None) -> Settings:
    """`.env` 를 읽어 설정을 만든다.

    이미 셸에 있는 환경변수를 덮어쓰지 않는다(`override=False`). CI 나 일회성
    실행에서 `LOL_BALANCE_SEED=1 python ...` 이 파일보다 우선하게 하기 위해서다.
    """
    load_dotenv(
        env_file if env_file is not None else PROJECT_ROOT / ".env", override=False
    )

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return Settings(
        seed=_read_seed(os.environ.get("LOL_BALANCE_SEED")),
        llm_model=os.environ.get("LOL_BALANCE_LLM_MODEL", "").strip()
        or DEFAULT_LLM_MODEL,
        anthropic_api_key=key or None,
    )
