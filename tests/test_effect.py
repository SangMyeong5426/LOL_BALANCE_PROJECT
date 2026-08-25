"""조정 효과 측정 테스트.

**대조군을 빼는 것이 요지다.** 같은 패치에 아이템·룬이 함께 바뀌면 승률이
그것 때문에 움직이는데, 조정 안 된 챔피언이 그 공통 변화를 흡수한다.
"""

from __future__ import annotations

import pytest

from conftest import PanelRowFactory
from lol_balance.effect import outcomes


def test_effect_subtracts_what_happened_to_everyone(make_row: PanelRowFactory) -> None:
    """모두가 +2%p 오른 패치에서 너프 대상이 +1%p 올랐다면 상대적으로는 내렸다."""
    before = (
        make_row("13_14", 1, win_rate=0.52, adjusted_next=True, direction_next="nerf"),
        make_row("13_14", 2, win_rate=0.50),
        make_row("13_14", 3, win_rate=0.50),
    )
    after = (
        make_row("13_15", 1, win_rate=0.53),
        make_row("13_15", 2, win_rate=0.52),
        make_row("13_15", 3, win_rate=0.52),
    )

    (result,) = outcomes(before, after)

    assert result.raw_shift == pytest.approx(0.01)
    assert result.baseline_shift == pytest.approx(0.02)
    assert result.adjusted_shift == pytest.approx(-0.01)
    assert result.worked is True


def test_a_nerf_that_raised_the_win_rate_did_not_work(
    make_row: PanelRowFactory,
) -> None:
    """5건 중 1건은 이렇게 된다. 그것도 결과다."""
    before = (
        make_row("13_14", 1, win_rate=0.52, adjusted_next=True, direction_next="nerf"),
        make_row("13_14", 2, win_rate=0.50),
    )
    after = (make_row("13_15", 1, win_rate=0.55), make_row("13_15", 2, win_rate=0.50))

    (result,) = outcomes(before, after)

    assert result.adjusted_shift > 0
    assert result.worked is False


def test_buff_and_nerf_are_judged_against_opposite_intentions(
    make_row: PanelRowFactory,
) -> None:
    before = (
        make_row("13_14", 1, win_rate=0.50, adjusted_next=True, direction_next="buff"),
        make_row("13_14", 2, win_rate=0.50, adjusted_next=True, direction_next="nerf"),
        make_row("13_14", 3, win_rate=0.50),
    )
    after = (
        make_row("13_15", 1, win_rate=0.53),
        make_row("13_15", 2, win_rate=0.53),
        make_row("13_15", 3, win_rate=0.50),
    )

    buffed, nerfed = outcomes(before, after)

    assert buffed.worked is True
    assert nerfed.worked is False


def test_mixed_and_adjust_are_not_measured(make_row: PanelRowFactory) -> None:
    """의도한 방향이 없으면 「먹혔나」를 물을 수 없다."""
    before = (
        make_row("13_14", 1, adjusted_next=True, direction_next="mixed"),
        make_row("13_14", 2, adjusted_next=True, direction_next="adjust"),
        make_row("13_14", 3),
    )
    after = tuple(make_row("13_15", i) for i in (1, 2, 3))

    assert outcomes(before, after) == ()


def test_champions_absent_from_either_patch_are_skipped(
    make_row: PanelRowFactory,
) -> None:
    """한쪽이 없으면 변화를 정의할 수 없다. 0 으로 채우지 않는다."""
    before = (
        make_row("13_14", 1, adjusted_next=True, direction_next="nerf"),
        make_row("13_14", 2),
    )
    after = (make_row("13_15", 2),)

    assert outcomes(before, after) == ()


def test_nothing_is_measured_without_a_control_group(make_row: PanelRowFactory) -> None:
    """전원이 조정된 패치라면 공통 변화를 분리할 수 없다."""
    before = (make_row("13_14", 1, adjusted_next=True, direction_next="nerf"),)
    after = (make_row("13_15", 1),)

    assert outcomes(before, after) == ()
