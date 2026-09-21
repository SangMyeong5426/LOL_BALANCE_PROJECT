"""유료 모델을 얼마나 썼나 — **세고, 상한에 닿으면 멈춘다.**

기본 에이전트 모델이 유료가 되면 두 가지가 위험해진다.

    한 건이 루프에 빠진다      → 호출 상한 미들웨어가 막는다(ADR 0009)
    **조금씩 계속 쓴다**       → 아무도 안 막는다. 여기가 그 자리다

크레딧은 반 전체가 나눠 쓴다. 개인 예산이 아니므로 **쓴 만큼을 파일에 적고**,
상한을 넘기면 호출 전에 막는다. 로컬 모델(`ollama:`)은 세지 않는다 — 공짜다.

장부는 `data/spend.jsonl` 이다. 원자료와 같은 곳이라 커밋되지 않는다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# USD / 1M 토큰. **제공자 문서에서 확인한 값만 적는다** (2026-09-21 확인).
# 없는 모델은 과금될 수 있어도 못 세므로, 쓰기 전에 여기 추가한다.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o-mini": (0.15, 0.60),
}

DEFAULT_CAP = 5.0
LEDGER = "spend.jsonl"


class SpendCapReached(RuntimeError):
    """상한에 닿았다. **더 부르지 않는다.**"""


def cap() -> float:
    raw = os.environ.get("LOL_BALANCE_SPEND_CAP", "").strip()
    if not raw:
        return DEFAULT_CAP
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"LOL_BALANCE_SPEND_CAP 은 숫자여야 한다: {raw!r}") from exc


def price(model: str) -> tuple[float, float] | None:
    """`제공자:모델` 에서 단가를 찾는다. 로컬이거나 모르는 모델이면 `None`."""
    name = model.split(":", 1)[-1]
    return None if model.startswith("ollama:") else PRICES.get(name)


def cost(model: str, tokens_in: int, tokens_out: int) -> float:
    found = price(model)
    if found is None:
        return 0.0
    per_in, per_out = found
    return (tokens_in * per_in + tokens_out * per_out) / 1e6


@dataclass(frozen=True)
class Ledger:
    """쓴 돈을 줄 단위로 적는다. **덧붙이기만 한다** — 지우지 않는다."""

    path: Path

    def spent(self) -> float:
        if not self.path.is_file():
            return 0.0
        total = 0.0
        for line in self.path.read_text().splitlines():
            if line.strip():
                total += float(json.loads(line).get("usd", 0.0))
        return total

    def add(self, model: str, tokens_in: int, tokens_out: int, why: str) -> float:
        usd = cost(model, tokens_in, tokens_out)
        if usd <= 0:
            return 0.0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "model": model,
                        "in": tokens_in,
                        "out": tokens_out,
                        "usd": round(usd, 6),
                        "why": why,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        return usd

    def check(self, model: str, limit: float | None = None) -> None:
        """부르기 **전에** 본다. 넘었으면 막는다.

        한 건의 값을 미리 모르므로 **이미 쓴 것**으로만 판단한다. 마지막 한 건이
        상한을 조금 넘길 수 있다 — 그 정도는 받아들이고, 다음 건에서 막힌다.
        """
        if price(model) is None:
            return
        ceiling = cap() if limit is None else limit
        used = self.spent()
        if used >= ceiling:
            raise SpendCapReached(
                f"유료 모델 누적 사용이 ${used:.4f} 로 상한 ${ceiling:.2f} 에 닿았다. "
                f"장부는 {self.path} 다. 더 쓰려면 LOL_BALANCE_SPEND_CAP 을 올린다."
            )


def ledger(root: Path) -> Ledger:
    return Ledger(root / "data" / LEDGER)


def usage_of(response: Any) -> tuple[int, int]:
    """LangChain 응답에서 제공자가 보고한 토큰. 없으면 (0, 0) — **추정하지 않는다.**"""
    meta = getattr(response, "usage_metadata", None) or {}
    return int(meta.get("input_tokens", 0)), int(meta.get("output_tokens", 0))
