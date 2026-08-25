"""패널 저장·조회 테스트.

**패치 경계가 조회에서 물리적으로 막히는지**가 핵심이다. 4단계 에이전트가
「패치 t 를 예측하라」는 과제를 받고 t 이후를 읽으면 정답을 그냥 본 것이 된다.
프롬프트로 막으면 안 되고 조회 함수가 못 주게 해야 한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import PanelRowFactory
from lol_balance.store import read_panel, write_panel


@pytest.fixture
def panel(tmp_path: Path, make_row: PanelRowFactory) -> Path:
    path = tmp_path / "panel.sqlite"
    write_panel(
        path,
        (
            make_row("13_14", 1),
            make_row("13_15", 1, adjusted_next=True),
            make_row("15_1", 1, ban_rate=None),
            make_row("16_1", 1),
        ),
    )
    return path


def test_round_trip_preserves_values_and_types(panel: Path) -> None:
    rows = read_panel(panel)

    assert [r.patch for r in rows] == ["13_14", "13_15", "15_1", "16_1"]
    assert rows[1].adjusted_next is True
    assert rows[0].adjusted_next is False


def test_missing_ban_rate_survives_as_none(panel: Path) -> None:
    """None 이 0.0 으로 돌아오면 「밴 0회」가 되어 조용히 틀린다."""
    row = next(r for r in read_panel(panel) if r.patch == "15_1")
    assert row.ban_rate is None


def test_before_patch_excludes_the_boundary(panel: Path) -> None:
    """경계 패치 자신도 나오면 안 된다 — 그 패치의 통계가 곧 예측 시점이다."""
    rows = read_panel(panel, before_patch="15_1")

    assert [r.patch for r in rows] == ["13_14", "13_15"]


def test_before_patch_uses_time_order_not_string_order(panel: Path) -> None:
    """문자열 비교였다면 `16_1` 이 `13_14` 보다 작다고 볼 수도 있다."""
    assert read_panel(panel, before_patch="13_14") == ()
    assert len(read_panel(panel, before_patch="16_1")) == 3


def test_rebuilding_gives_the_same_order(panel: Path, tmp_path: Path) -> None:
    other = tmp_path / "again.sqlite"
    write_panel(other, tuple(reversed(read_panel(panel))))

    assert [r.patch for r in read_panel(other)] == [r.patch for r in read_panel(panel)]
