"""베이스라인 피처·지표 테스트."""

from __future__ import annotations

import numpy as np
import pytest

from conftest import PanelRowFactory
from lol_balance.baseline import (
    encode,
    fit_encoder,
    precision_at,
    r_precision,
    roc_auc,
)


def test_encoder_columns_do_not_depend_on_whether_this_subset_has_gaps(
    make_row: PanelRowFactory,
) -> None:
    """실제로 여기서 한 번 터졌다.

    학습 구간에만 밴 결측이 있고 평가 구간에는 없었더니 열 개수가 12 대 11 로
    갈렸다. 결측 표시 열은 **있든 없든 항상** 만들어야 한다.
    """
    with_gap = (make_row("13_14", 1, ban_rate=None), make_row("13_15", 2))
    without_gap = (make_row("16_1", 3), make_row("16_2", 4))
    encoder = fit_encoder(with_gap, with_trend=False)

    assert (
        encode(with_gap, encoder).x.shape[1] == encode(without_gap, encoder).x.shape[1]
    )
    assert "ban_rate_missing" in encoder.columns


def test_missing_values_are_flagged_not_silently_filled(
    make_row: PanelRowFactory,
) -> None:
    rows = (make_row("13_14", 1, ban_rate=None), make_row("13_15", 2, ban_rate=0.04))
    encoder = fit_encoder(rows, with_trend=False)
    matrix = encode(rows, encoder)

    flag = matrix.x[:, encoder.columns.index("ban_rate_missing")]
    assert flag.tolist() == [1.0, 0.0]


def test_fill_comes_from_the_encoder_not_the_encoded_rows(
    make_row: PanelRowFactory,
) -> None:
    """평가 구간의 중앙값으로 평가 구간을 채우면 평가 정보가 입력에 섞인다."""
    train = (make_row("13_14", 1, ban_rate=0.10), make_row("13_15", 2, ban_rate=0.10))
    encoder = fit_encoder(train, with_trend=False)

    filled = encode((make_row("16_1", 3, ban_rate=None),), encoder)
    assert filled.x[0, encoder.columns.index("ban_rate")] == pytest.approx(0.10)


def test_trend_columns_appear_only_when_asked(make_row: PanelRowFactory) -> None:
    rows = (make_row("13_14", 1),)
    assert "d_win_rate" not in fit_encoder(rows, with_trend=False).columns
    assert "d_win_rate" in fit_encoder(rows, with_trend=True).columns


# --- 지표 ---


def test_r_precision_uses_the_actual_positive_count_as_k() -> None:
    y = np.array([1, 1, 0, 0, 0])
    assert r_precision(y, np.array([9.0, 8.0, 1.0, 1.0, 1.0])) == pytest.approx(1.0)
    assert r_precision(y, np.array([1.0, 1.0, 9.0, 8.0, 1.0])) == pytest.approx(0.0)


def test_precision_at_k_looks_only_at_the_top() -> None:
    y = np.array([1, 0, 1, 0])
    assert precision_at(y, np.array([4.0, 3.0, 2.0, 1.0]), 2) == pytest.approx(0.5)


def test_roc_auc_gives_a_constant_predictor_exactly_half() -> None:
    """동점을 평균 순위로 다루지 않으면 상수 예측기가 0.5 가 아니게 나온다."""
    y = np.array([1, 0, 1, 0])
    assert roc_auc(y, np.ones(4)) == pytest.approx(0.5)


def test_roc_auc_is_one_for_a_perfect_ranking() -> None:
    y = np.array([0, 0, 1, 1])
    assert roc_auc(y, np.array([1.0, 2.0, 3.0, 4.0])) == pytest.approx(1.0)


def test_metrics_are_undefined_when_a_patch_has_no_positives() -> None:
    y = np.array([0, 0, 0])
    assert np.isnan(r_precision(y, np.array([1.0, 2.0, 3.0])))
    assert np.isnan(roc_auc(y, np.array([1.0, 2.0, 3.0])))
