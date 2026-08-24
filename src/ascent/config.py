"""실행 환경 설정.

`.env` 에서 읽고 없으면 기본값을 쓴다. 여기서 하는 일은 값을 읽어 오는 것뿐이고
전투 규칙이나 밸런스 수치는 다루지 않는다 — 그쪽은 `data/` 의 JSON 이 기준이다.
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

        Phase 0-A·0-B·1 은 이 값이 False 여도 전부 돌아간다. 시뮬레이션 결과가
        LLM 에 의존하지 않는다는 설계 원칙이 여기서 확인된다.
        """
        return bool(self.anthropic_api_key)


def _read_seed(raw: str | None) -> int:
    if raw is None or raw.strip() == "":
        return DEFAULT_SEED
    try:
        return int(raw)
    except ValueError as exc:
        # 시드가 조용히 기본값으로 떨어지면 재현성이 깨진 것을 아무도 모른다.
        raise ValueError(f"ASCENT_SEED 는 정수여야 한다: {raw!r}") from exc


def load_settings(env_file: Path | None = None) -> Settings:
    """`.env` 를 읽어 설정을 만든다.

    이미 셸에 있는 환경변수를 덮어쓰지 않는다(`override=False`). CI 나 일회성
    실행에서 `ASCENT_SEED=1 python ...` 이 파일보다 우선하게 하기 위해서다.
    """
    load_dotenv(
        env_file if env_file is not None else PROJECT_ROOT / ".env", override=False
    )

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return Settings(
        seed=_read_seed(os.environ.get("ASCENT_SEED")),
        llm_model=os.environ.get("ASCENT_LLM_MODEL", "").strip() or DEFAULT_LLM_MODEL,
        anthropic_api_key=key or None,
    )
