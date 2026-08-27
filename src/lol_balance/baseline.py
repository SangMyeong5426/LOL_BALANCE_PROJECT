"""통계 베이스라인 — **LLM 이 값을 하는지 비교할 대상.**

[CLAUDE.md](../../CLAUDE.md) 가 요구하는 순서다. 단순한 방법을 먼저 만들고,
LLM 이 그것을 못 이기면 못 이겼다고 적는다.

과제는 **줄 세우기**다. 한 패치에 챔피언 약 172종이 있고 그중 평균 29.7종이
다음 패치에서 조정된다. 각 챔피언에 「조정될 확률」을 매겨 정렬하고, 위쪽에
실제 조정된 챔피언이 얼마나 모이는지를 본다.

**결측을 값으로 채우되 채웠다는 사실을 피처로 남긴다.** 선형 모델은 빈 칸을
못 받으므로 채워야 하는데, 그냥 채우면 「모름」이 「그 값이었음」으로 바뀐다.
표시 열을 함께 넣으면 모델이 그 둘을 구분할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from lol_balance.panel import PanelRow

# 수준 피처 — 그 패치의 상태
LEVEL_FEATURES = (
    "win_rate",
    "wr_gap",
    "pick_rate",
    "ban_rate",
    "matches",
    "kills",
    "deaths",
    "assists",
    "cs",
    "gold",
    "damage",
    "role_count",
)
# 추세 피처 — 직전 패치 대비 변화
# 이력 피처 — 과거 몇 패치의 조정 이력과 승률 흐름.
# **따로 켜고 끌 수 있어야 기여를 잴 수 있다.** 수준 피처에 섞어 넣으면 모든
# arm 이 한꺼번에 바뀌어 「이력이 도움이 됐나」에 답할 수 없다.
HISTORY_FEATURES = ("history_len", "recent_adjustments", "high_wr_streak")

# 프로 경기 피처 — Oracle's Elixir. **따로 켜고 끌 수 있어야 기여를 잴 수 있다.**
#
# 방향 예측에서 값을 한다. ① 대상에서는 너프·버프의 부호가 반대라 상쇄된다
# (0.597 → 0.597). **한쪽에만 듣는 피처라 섞어 넣으면 그 사실이 사라진다.**
PRO_FEATURES = ("pro_pick_rate", "pro_ban_rate", "pro_presence")

# 범주형. 원핫으로 편다. **범주 목록은 학습 구간에서만 정한다** — 평가에만
# 있는 값이 열을 만들면 학습·평가의 열 구성이 달라진다.
CATEGORICAL = ("main_role",)
TREND_FEATURES = ("d_win_rate", "d_pick_rate", "d_ban_rate")


# 값이 없을 수 있는 피처. **부분집합에 결측이 있든 없든 표시 열을 항상 만든다.**
# 「이번 데이터에 결측이 있을 때만」 열을 만들면 학습과 평가의 열 개수가 달라진다.
NULLABLE = (
    "ban_rate",
    "recent_adjustments",
    "d_win_rate",
    "d_pick_rate",
    "d_ban_rate",
    # 프로 경기가 아예 없는 패치가 있다(13_23 · 14_24 — 12월 비시즌).
    "pro_pick_rate",
    "pro_ban_rate",
    "pro_presence",
)


@dataclass(frozen=True)
class Matrix:
    """모델에 넣을 형태로 편 패널."""

    x: NDArray[np.float64]
    y: NDArray[np.int_]
    patches: NDArray[np.str_]
    champions: NDArray[np.str_]
    columns: tuple[str, ...]

    def __len__(self) -> int:
        return len(self.y)


@dataclass(frozen=True)
class Encoder:
    """열 구성과 채움값. **학습 구간에서만 만든다.**

    평가 구간의 중앙값으로 평가 구간을 채우면 평가 정보가 모델 입력에 섞인다.
    금액이 작아 보여도 이런 것이 시간순 분할을 무력화한다.
    """

    names: tuple[str, ...]
    columns: tuple[str, ...]
    fill: tuple[float, ...]
    levels: tuple[tuple[str, tuple[str, ...]], ...] = ()


def feature_names(
    with_trend: bool, with_history: bool = False, with_pro: bool = False
) -> tuple[str, ...]:
    return (
        LEVEL_FEATURES
        + (TREND_FEATURES if with_trend else ())
        + (HISTORY_FEATURES if with_history else ())
        + (PRO_FEATURES if with_pro else ())
    )


def fit_encoder(
    rows: tuple[PanelRow, ...],
    with_trend: bool,
    with_history: bool = False,
    with_pro: bool = False,
) -> Encoder:
    """학습 구간에서 열 구성과 채움값을 정한다.

    `with_history` 일 때만 범주형(`main_role`)도 함께 편다 — 역할과 이력은
    「그 챔피언이 어떤 자리에서 어떻게 다뤄져 왔나」라는 같은 갈래다.
    """
    names = feature_names(with_trend, with_history, with_pro)
    fill: list[float] = []
    columns: list[str] = []
    for name in names:
        values = [getattr(r, name) for r in rows]
        present = [float(v) for v in values if v is not None]
        fill.append(float(np.median(present)) if present else 0.0)
        if name in NULLABLE:
            columns.append(f"{name}_missing")
        columns.append(name)

    levels = []
    for name in CATEGORICAL if with_history else ():
        seen = tuple(sorted({str(getattr(r, name)) for r in rows}))
        levels.append((name, seen))
        columns += [f"{name}={value}" for value in seen]
    return Encoder(names, tuple(columns), tuple(fill), tuple(levels))


# 방향 예측에 쓸 행. `mixed` 와 `adjust` 는 뺀다 — 한쪽으로 뭉개면 그 사실이
# 사라지고, 「어느 쪽도 아니다」를 억지로 이진 분류에 넣는 셈이 된다.
DIRECTION_CLASSES = ("nerf", "buff")


def direction_rows(rows: tuple[PanelRow, ...]) -> tuple[PanelRow, ...]:
    """방향 예측 과제의 표본. **조정된 것 중 방향이 분명한 것만.**

    이 과제는 「조정될까」를 이미 맞혔다고 치고 「어느 쪽인가」를 묻는다.
    조정 안 된 챔피언을 넣으면 두 과제가 뒤섞인다.
    """
    return tuple(r for r in rows if r.direction_next in DIRECTION_CLASSES)


def encode(
    rows: tuple[PanelRow, ...], encoder: Encoder, target: str = "adjusted"
) -> Matrix:
    """행을 인코더가 정한 열 구성으로 편다.

    `target` 이 `direction` 이면 라벨이 **너프인가(1) 버프인가(0)** 가 된다.
    `direction_rows` 로 거른 행을 넘겨야 한다.
    """
    blocks: list[NDArray[np.float64]] = []
    for name, fill in zip(encoder.names, encoder.fill, strict=True):
        raw = [getattr(r, name) for r in rows]
        missing = np.array([v is None for v in raw], dtype=float)
        values = np.array([fill if v is None else float(v) for v in raw], dtype=float)
        if name in NULLABLE:
            blocks.append(missing)
        blocks.append(values)

    for name, seen in encoder.levels:
        actual = [str(getattr(r, name)) for r in rows]
        # 학습에 없던 값은 어느 열에도 1 이 안 붙는다. 그것이 정직하다.
        for value in seen:
            blocks.append(np.array([1.0 if a == value else 0.0 for a in actual]))

    if target == "direction":
        y = np.array([int(r.direction_next == "nerf") for r in rows], dtype=int)
    else:
        y = np.array([int(r.adjusted_next) for r in rows], dtype=int)
    return Matrix(
        x=np.column_stack(blocks).astype(np.float64),
        y=y,
        patches=np.array([r.patch for r in rows]),
        champions=np.array([r.champion for r in rows]),
        columns=encoder.columns,
    )


# --- 지표 -------------------------------------------------------------


def r_precision(y: NDArray[np.int_], score: NDArray[np.float64]) -> float:
    """실제 조정된 수만큼 뽑았을 때의 정확도.

    **K 를 고정하지 않는다.** 패치당 조정 수가 11~68 종으로 크게 다르므로
    고정 K 는 패치마다 뜻이 달라진다. 실제 수를 K 로 쓰면 패치 간 비교가 된다.
    이것은 평가 쪽 선택이고 모델에 알려 주는 정보가 아니다 — 모든 arm 이 같은
    K 로 채점된다.
    """
    k = int(y.sum())
    if k == 0:
        return float("nan")
    top = np.argsort(-score, kind="stable")[:k]
    return float(y[top].sum() / k)


def precision_at(y: NDArray[np.int_], score: NDArray[np.float64], k: int) -> float:
    """상위 k 종 중 실제 조정된 비율. 목록 맨 위의 품질을 본다."""
    if len(y) < k:
        return float("nan")
    top = np.argsort(-score, kind="stable")[:k]
    return float(y[top].sum() / k)


def roc_auc(y: NDArray[np.int_], score: NDArray[np.float64]) -> float:
    """무작위로 고른 양성이 무작위 음성보다 위에 올 확률. 문턱값이 필요 없다."""
    positive, negative = int(y.sum()), int((1 - y).sum())
    if positive == 0 or negative == 0:
        return float("nan")
    rank = np.empty(len(score), dtype=float)
    order = np.argsort(score, kind="stable")
    rank[order] = np.arange(1, len(score) + 1)
    # 동점은 평균 순위로. 안 그러면 상수 예측기가 0.5 가 아닌 값을 받는다.
    for value in np.unique(score):
        tie = score == value
        if tie.sum() > 1:
            rank[tie] = rank[tie].mean()
    return float(
        (rank[y == 1].sum() - positive * (positive + 1) / 2) / (positive * negative)
    )
