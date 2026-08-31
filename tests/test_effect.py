"""조정 효과 측정 테스트.

**대조군을 빼는 것이 요지다.** 같은 패치에 아이템·룬이 함께 바뀌면 승률이
그것 때문에 움직이는데, 조정 안 된 챔피언이 그 공통 변화를 흡수한다.
"""

from __future__ import annotations

import pytest

from conftest import PanelRowFactory
from lol_balance.effect import Outcome, balance_control, outcomes


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


# --- ③′ 균형에 가까워졌나 --------------------------------------------------


def kept(before: float, after: float) -> Outcome:
    return Outcome("13_14", "C", "nerf", "label", before, after, 0.0)


def test_closer_asks_a_different_question_than_worked() -> None:
    """**너프가 의도대로 갔는데 균형에서는 멀어질 수 있다.**

    이미 5할 아래인 챔피언을 너프하면 승률은 의도대로 내려가지만 5할에서는
    더 멀어진다. ③ 과 ③′ 이 다른 답을 내는 자리다.
    """
    o = kept(0.47, 0.45)

    assert o.worked is True
    assert o.closer is False


def test_closer_is_true_when_it_moves_toward_fifty() -> None:
    assert kept(0.56, 0.52).closer is True
    assert kept(0.44, 0.48).closer is True


def test_the_control_group_also_drifts_toward_fifty(
    make_row: PanelRowFactory,
) -> None:
    """**조정 안 해도 5할 쪽으로 돌아온다.** 그 비율을 빼야 성과가 보인다."""
    before = (make_row("13_14", 1, win_rate=0.56), make_row("13_14", 2, win_rate=0.44))
    after = (make_row("13_15", 1, win_rate=0.52), make_row("13_15", 2, win_rate=0.42))

    assert balance_control(before, after) == (1, 2)


def test_the_control_group_ignores_adjusted_champions(
    make_row: PanelRowFactory,
) -> None:
    """조정된 챔피언은 대조군이 아니다."""
    before = (make_row("13_14", 1, win_rate=0.56, adjusted_next=True),)
    after = (make_row("13_15", 1, win_rate=0.52),)

    assert balance_control(before, after) == (0, 0)


# --- 프로 경기 픽·밴율 변화 -------------------------------------------------


def pro(before: float | None, after: float | None) -> Outcome:
    return Outcome("13_14", "C", "buff", "label", 0.5, 0.5, 0.0, before, after)


def test_pro_change_is_a_ratio_not_a_point_difference() -> None:
    """**챔피언마다 자릿수가 달라 %p 로는 비교가 안 된다.**"""
    assert pro(0.10, 0.14).pro_change == pytest.approx(0.4)
    assert pro(0.50, 0.70).pro_change == pytest.approx(0.4)


def test_never_seen_in_pro_before_or_after_counts_as_no_change() -> None:
    """**빼면 「그 외」 무리가 통째로 사라진다.**

    프로 경기에 거의 안 나오는 챔피언이 그 무리의 대부분이라, 0 → 0 을 빼면
    남는 것이 편향된다. 안 나오던 것이 조정 뒤에도 안 나온 것은 관측된 사실이다.
    """
    assert pro(0.0, 0.0).pro_change == 0.0


def test_appearing_from_nothing_has_no_ratio() -> None:
    """0 에서 늘어난 것은 비율이 무한이라 중앙값에 못 넣는다."""
    assert pro(0.0, 0.2).pro_change is None


def test_missing_pro_data_is_not_zero() -> None:
    """**모르는 것을 0 으로 적지 않는다.**"""
    assert pro(None, 0.2).pro_change is None
    assert pro(0.2, None).pro_change is None
