"""테스트 공용 도구.

`tests/` 는 패키지가 아니라 테스트끼리 import 하면 깨진다. 여러 파일이 쓰는
것은 여기에 둔다.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from lol_balance.panel import PanelRow, patch_index

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
            d_win_rate=None,
            d_pick_rate=None,
            d_ban_rate=None,
            adjusted_next=False,
            direction_next=None,
            direction_source=None,
        )
        values.update(overrides)
        return PanelRow(**values)  # type: ignore[arg-type]

    return build
