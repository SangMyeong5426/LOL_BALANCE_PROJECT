"""테스트 공용 도구.

`tests/` 는 패키지가 아니라 테스트끼리 import 하면 깨진다. 여러 파일이 쓰는
것은 여기에 둔다.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from lol_balance.agent.data import Corpus
from lol_balance.panel import PanelRow, patch_index
from lol_balance.patchnotes import ChangeBlock

PanelRowFactory = Callable[..., PanelRow]


@pytest.fixture
def make_row() -> PanelRowFactory:
    """기본값이 채워진 `PanelRow` 를 만든다. 보려는 필드만 덮어쓴다."""

    def build(patch: str, champion_id: int, **overrides: object) -> PanelRow:
        values: dict[str, object] = dict(
            patch=patch,
            patch_index=patch_index(patch),
            champion_id=champion_id,
            champion=f"C{champion_id}",
            main_role="mid",
            role_count=1,
            win_rate=0.51,
            pick_rate=0.05,
            ban_rate=0.02,
            matches=5000,
            kills=5.0,
            deaths=5.0,
            assists=8.0,
            cs=150.0,
            gold=900.0,
            damage=1000.0,
            pro_pick_rate=0.0,
            pro_ban_rate=0.0,
            d_win_rate=None,
            d_pick_rate=None,
            d_ban_rate=None,
            history_len=0,
            recent_adjustments=None,
            high_wr_streak=0,
            adjusted_next=False,
            direction_next=None,
            direction_source=None,
        )
        values.update(overrides)
        return PanelRow(**values)  # type: ignore[arg-type]

    return build


# 작은 패널의 패치. 개발 분할점(14_13)과 평가 분할점(15_13)을 둘 다 넘고,
# 마지막 패치는 답이 없는 예측 지점이다 — 실제 패널과 같은 모양이다.
TINY_PATCHES = tuple(f"14_{i}" for i in range(6, 16)) + tuple(
    f"15_{i}" for i in range(10, 17)
)
TINY_CHAMPIONS = 20


@pytest.fixture
def tiny_corpus(make_row: PanelRowFactory) -> Corpus:
    """에이전트 시험용 작은 패널 — **원자료 없이** 도구·루프·평가를 돌린다.

    20종 × 17패치. 승률이 5할에서 멀수록 조정되고, 위면 너프·아래면 버프다.
    몇 칸은 일부러 뒤집어 다수결이 완벽하지 않게 둔다. 수치가 실제와 같을
    필요는 없다 — 도구·경계·표본·채점의 **배선**을 보는 자리다.
    """
    roles = ("top", "jungle", "mid", "bottom", "support")
    rows: list[PanelRow] = []
    last = TINY_PATCHES[-1]
    for j, patch in enumerate(TINY_PATCHES):
        for i in range(1, TINY_CHAMPIONS + 1):
            win = 0.47 + 0.004 * ((i * 7 + j * 5) % 16)
            adjusted = patch != last and (abs(win - 0.5) >= 0.012 or (i + j) % 6 == 0)
            direction = ("nerf" if win > 0.5 else "buff") if adjusted else None
            if adjusted and (i * j) % 7 == 3:
                direction = "buff" if direction == "nerf" else "nerf"
            rows.append(
                make_row(
                    patch,
                    i,
                    main_role=roles[i % 5],
                    win_rate=round(win, 4),
                    pick_rate=0.02 + 0.006 * (i % 6),
                    ban_rate=0.01 * (i % 5),
                    matches=4000 + 150 * i,
                    pro_pick_rate=0.2 if i <= 3 else 0.0,
                    pro_ban_rate=0.1 if i <= 3 else 0.0,
                    d_win_rate=None if j == 0 else 0.002 * ((i + j) % 5 - 2),
                    d_pick_rate=None if j == 0 else 0.001 * ((i * j) % 5 - 2),
                    d_ban_rate=None if j == 0 else 0.0,
                    adjusted_next=adjusted,
                    direction_next=direction,
                    direction_source="label" if adjusted else None,
                )
            )
    blocks = {
        "15_10": [ChangeBlock("C4", "E - Dash", "E", ("Range reduced to 400",))],
        "15_11": [
            ChangeBlock("C3", "Stats", None, ("Base health reduced to 600 from 630",)),
            ChangeBlock("C3", "C3 skin", None, ("Skin name changed",)),
        ],
        "15_12": [
            ChangeBlock("C3", "Q - Blade", "Q", ("Damage reduced to 50 from 60",))
        ],
        "15_15": [ChangeBlock("C3", "W - Ward", "W", ("Cooldown increased to 12",))],
    }
    return Corpus(
        rows=tuple(rows),
        blocks=blocks,
        rules=(),
        churn={},
        seed=20260824,
        labeled=frozenset(TINY_PATCHES[:-1]),
    )
