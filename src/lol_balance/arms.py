"""arm 정의와 채점 — **LLM 이 넘어야 할 선.**

[CLAUDE.md](../../CLAUDE.md) 가 요구하는 순서다. 단순한 방법을 먼저 세우고,
LLM 이 그것을 못 이기면 못 이겼다고 적는다.

과제가 셋이고 **셋이 서로 다른 질문**이다.

    ① 대상   누가 조정되나        패치 안에서 줄 세우기
    ② 방향   너프냐 버프냐        조정된 것 중 이진 분류
    ③ 효과   먹혔나              측정. 예측이 아니다

같은 피처가 ① 과 ② 에서 반대로 움직인다는 것이
[`prediction-signals.md`](../../docs/spec/prediction-signals.md) 에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from sklearn.linear_model import LogisticRegression
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

from lol_balance.baseline import (
    Matrix,
    direction_rows,
    encode,
    fit_encoder,
    precision_at,
    r_precision,
    roc_auc,
)
from lol_balance.panel import PanelRow, patch_index


@dataclass(frozen=True)
class Result:
    """arm 하나의 성적."""

    arm: str
    label: str
    uses_llm: bool
    scores: dict[str, float]


def _neighbours(wanted: int, available: int) -> int:
    """이웃 수를 학습 표본 안으로 눌러 담는다.

    표본보다 큰 이웃 수를 주면 sklearn 이 죽는다. 이른 분할 지점을 쓰면 실제로
    그렇게 되므로 클램프한다. 몇 개를 실제로 썼는지는 arm 이름에 남긴다.
    """
    return max(1, min(wanted, available))


def _model(seed: int) -> Pipeline:
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=2000, class_weight="balanced", random_state=seed),
    )


def split(rows: tuple[PanelRow, ...], at: str) -> tuple[tuple[PanelRow, ...], ...]:
    """시간순으로 자른다. **무작위 분할은 미래가 과거로 샌다.**"""
    cut = patch_index(at)
    return (
        tuple(r for r in rows if r.patch_index < cut),
        tuple(r for r in rows if r.patch_index >= cut),
    )


# --- ① 대상 -----------------------------------------------------------


def _rank_scores(matrix: Matrix, score: NDArray[np.float64]) -> dict[str, float]:
    """패치마다 재고 평균 낸다. **평가 단위는 패치다** — 행이 아니라."""
    per: dict[str, list[float]] = {"r_precision": [], "p_at_10": [], "auc": []}
    for patch in sorted(set(matrix.patches), key=patch_index):
        mask = matrix.patches == patch
        per["r_precision"].append(r_precision(matrix.y[mask], score[mask]))
        per["p_at_10"].append(precision_at(matrix.y[mask], score[mask], 10))
        per["auc"].append(roc_auc(matrix.y[mask], score[mask]))
    return {k: float(np.nanmean(v)) for k, v in per.items()}


def target_arms(
    rows: tuple[PanelRow, ...], at: str, seed: int
) -> tuple[list[Result], dict[str, float]]:
    """누가 조정될 것인가. 패치 안에서 챔피언을 줄 세운다."""
    train, test = split(rows, at)
    flat = fit_encoder(train, with_trend=False)
    te = encode(test, flat)
    rng = np.random.default_rng(seed)

    out = [Result("A0", "무작위 순서", False, _rank_scores(te, rng.random(len(te))))]
    for arm, label, column in (
        ("A0b", "픽률 높은 순", "pick_rate"),
        ("A0c", "승률 높은 순", "win_rate"),
        ("A0d", "|승률 − 0.5| 큰 순", "wr_gap"),
    ):
        column_scores = te.x[:, te.columns.index(column)]
        out.append(Result(arm, label, False, _rank_scores(te, column_scores)))

    for arm, label, trend in (
        ("A1", "로지스틱 회귀 — 수준", False),
        ("A2", "로지스틱 회귀 — 수준 + 추세", True),
    ):
        enc = fit_encoder(train, with_trend=trend)
        tr, ts = encode(train, enc), encode(test, enc)
        model = _model(seed).fit(tr.x, tr.y)
        out.append(
            Result(arm, label, False, _rank_scores(ts, model.predict_proba(ts.x)[:, 1]))
        )

    tr = encode(train, flat)
    k = _neighbours(50, len(tr.y))
    knn = make_pipeline(StandardScaler(), KNeighborsClassifier(n_neighbors=k))
    knn.fit(tr.x, tr.y)
    out.append(
        Result(
            "A3",
            f"유사 사례 {k}개 (k-NN)",
            False,
            _rank_scores(te, knn.predict_proba(te.x)[:, 1]),
        )
    )

    base = sum(r.adjusted_next for r in test) / len(test)
    return out, {"기준선": base, "학습": len(train), "평가": len(test)}


# --- ② 방향 -----------------------------------------------------------


def direction_arms(
    rows: tuple[PanelRow, ...], at: str, seed: int
) -> tuple[list[Result], dict[str, float]]:
    """조정된다면 어느 쪽인가. 조정된 챔피언만 본다."""
    pool = direction_rows(rows)
    train, test = split(pool, at)
    flat = fit_encoder(train, with_trend=False)
    te = encode(test, flat, target="direction")
    rng = np.random.default_rng(seed)

    def scored(score: NDArray[np.float64], probability: bool) -> dict[str, float]:
        out = {"auc": roc_auc(te.y, score)}
        if probability:
            out["accuracy"] = float(((score >= 0.5).astype(int) == te.y).mean())
        return out

    out = [Result("B0", "무작위", False, scored(rng.random(len(te)), True))]
    for arm, label, column in (
        ("B0b", "승률 높은 순", "win_rate"),
        ("B0c", "밴율 높은 순", "ban_rate"),
        ("B0d", "픽률 높은 순", "pick_rate"),
    ):
        out.append(
            Result(arm, label, False, scored(te.x[:, te.columns.index(column)], False))
        )

    for arm, label, trend in (
        ("B1", "로지스틱 회귀 — 수준", False),
        ("B2", "로지스틱 회귀 — 수준 + 추세", True),
    ):
        enc = fit_encoder(train, with_trend=trend)
        tr = encode(train, enc, target="direction")
        ts = encode(test, enc, target="direction")
        model = _model(seed).fit(tr.x, tr.y)
        probability = model.predict_proba(ts.x)[:, 1]
        out.append(
            Result(
                arm,
                label,
                False,
                {
                    "auc": roc_auc(ts.y, probability),
                    "accuracy": float(
                        ((probability >= 0.5).astype(int) == ts.y).mean()
                    ),
                },
            )
        )

    tr = encode(train, flat, target="direction")
    k = _neighbours(25, len(tr.y))
    knn = make_pipeline(StandardScaler(), KNeighborsClassifier(n_neighbors=k))
    knn.fit(tr.x, tr.y)
    out.append(
        Result(
            "B3",
            f"유사 사례 {k}개 (k-NN)",
            False,
            scored(knn.predict_proba(te.x)[:, 1], True),
        )
    )

    nerf = sum(1 for r in test if r.direction_next == "nerf")
    return out, {
        "기준선": nerf / len(test),
        "학습": len(train),
        "평가": len(test),
        "손 라벨": sum(1 for r in pool if r.direction_source == "label"),
    }
