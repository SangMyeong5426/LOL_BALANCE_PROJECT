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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from sklearn.ensemble import HistGradientBoostingClassifier
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
from lol_balance.ragjudge import Judgment, anon_key
from lol_balance.retrieval import CASE_FEATURES, CASE_FEATURES_PRO, CaseSearch
from lol_balance.rules import Rule, RulePredictor


@dataclass(frozen=True)
class Result:
    """arm 하나의 성적.

    `uses_llm` 은 **모델이 이 arm 의 산출물을 만드는 데 관여했는가**다.
    실행 시점에 API 를 부른다는 뜻이 아니다 — A4·B4 의 규칙은 대화 중 Claude 가
    제안했고 저장소에 텍스트로 들어 있다. 그 구분을 `rules.Provenance` 가 담는다.
    """

    arm: str
    label: str
    uses_llm: bool
    scores: dict[str, float]


# 부스팅 트리 설정. **학습 구간 안에서 시간순 3겹 교차검증으로 골랐다** —
# 평가 구간을 보고 고르면 그것이 곧 누출이다.
#
#   ① 대상(학습 5,334행)  기본값 0.567 → 규제 0.587. 규제가 확실히 낫다
#   ② 방향(학습   296건)  기본값 0.831 · 규제 0.830. 구분되지 않는 차이라
#                        표본이 작은 쪽을 감안해 안전한 규제를 택했다
_BOOST = {
    "max_depth": 2,
    "max_iter": 300,
    "learning_rate": 0.05,
    "l2_regularization": 1.0,
}


def _boosting(seed: int) -> HistGradientBoostingClassifier:
    """선형 모델이 못 잡는 것을 잡으라고 넣는다.

    회귀는 피처를 더해서 쓰므로 **「승률이 높고 동시에 픽률도 높을 때만」** 같은
    조건을 표현하지 못한다. 규칙 엔진이 회귀를 이긴 것이 그 증거였다. 트리는
    그 상호작용을 나눠 담을 수 있다.
    """
    return HistGradientBoostingClassifier(
        random_state=seed, class_weight="balanced", **_BOOST
    )


def _neighbours(wanted: int, available: int) -> int:
    """이웃 수를 학습 표본 안으로 눌러 담는다.

    표본보다 큰 이웃 수를 주면 sklearn 이 죽는다. 이른 분할 지점을 쓰면 실제로
    그렇게 되므로 클램프한다. 몇 개를 실제로 썼는지는 arm 이름에 남긴다.
    """
    return max(1, min(wanted, available))


# 거리 0 으로 나누지 않기 위한 값.
_EPS = 1e-6

# 사례 검색 이웃 수. **학습 구간 3겹 교차검증으로 골랐다** — 25·50·75 중 50.
_RETRIEVAL_K = 50


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


def _retrieved(
    rows: tuple[PanelRow, ...],
    test: tuple[PanelRow, ...],
    at: str,
    k: int,
    expanding: bool,
    features: Sequence[str] = CASE_FEATURES,
    weighted: bool = False,
) -> tuple[list[float], list[float]]:
    """사례 검색만으로 두 과제를 예측한다. **모델이 없다 — 이웃의 다수결이다.**

    `expanding` 이면 참고 범위를 **각 행의 패치 직전까지** 넓힌다. 실제 시스템은
    그렇게 돌므로 더 현실적인데, 그러면 A3(k-NN)와 참고 범위가 달라져 직접
    비교가 안 된다. 그래서 둘 다 낸다.

    검색기가 `as_of` 를 생성자에서 받으므로 **경계는 도구가 지킨다.** 여기서
    빠뜨려도 경계 밖 사례가 섞이지 않는다.
    """
    fixed = None if expanding else CaseSearch(rows, at, features)
    adjusted: list[float] = []
    nerf: list[float] = []
    cache: dict[str, CaseSearch] = {}

    for row in test:
        if fixed is not None:
            search = fixed
        else:
            search = cache.setdefault(row.patch, CaseSearch(rows, row.patch, features))
        cases = search.similar(row, k=k)
        if not cases:
            adjusted.append(0.0)
            nerf.append(0.5)
            continue
        # 거리 가중을 쓰면 **가까운 사례가 더 센다.** 단순 다수결은 k 칸짜리
        # 눈금이라 234행에 점수가 25종밖에 안 나오고, AUC 는 줄 세우기 점수라
        # 그 동점이 손해다. 가중을 쓰면 200종으로 늘어난다.
        weights = [1.0 / (c.distance + _EPS) if weighted else 1.0 for c in cases]
        adjusted.append(
            sum(w for w, c in zip(weights, cases, strict=True) if c.row.adjusted_next)
            / sum(weights)
        )
        pairs = [
            (w, c)
            for w, c in zip(weights, cases, strict=True)
            if c.row.direction_next in ("nerf", "buff")
        ]
        nerf.append(
            sum(w for w, c in pairs if c.row.direction_next == "nerf")
            / sum(w for w, _ in pairs)
            if pairs
            else 0.5
        )
    return adjusted, nerf


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
    rows: tuple[PanelRow, ...],
    at: str,
    seed: int,
    rules: tuple[Rule, ...] = (),
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

    # `A2p` 는 프로 경기 피처를 더한 것이다. **안 늘어난다는 것을 보이려고 둔다** —
    # 같은 피처가 ② 방향에서는 AUC 를 0.04 올린다. 어느 과제에 듣고 어느 과제에
    # 안 듣는지가 결과다.
    for arm, label, trend, history, pro in (
        ("A1", "로지스틱 회귀 — 수준", False, False, False),
        ("A2", "로지스틱 회귀 — 수준 + 추세", True, False, False),
        ("A2p", "로지스틱 회귀 — 수준 + 추세 + 프로", True, False, True),
        ("A2h", "로지스틱 회귀 — + 이력·역할", True, True, False),
    ):
        enc = fit_encoder(train, with_trend=trend, with_history=history, with_pro=pro)
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

    if rules:
        # **가중치는 학습 구간에서만 뽑는다.** 규칙 자체도 학습만 보고 제안했다.
        predictor = RulePredictor.fit(rules, train)
        scores = np.array([predictor.adjusted_score(r) for r in test])
        out.append(
            Result("A4", "규칙 엔진 — 대화 중 제안", True, _rank_scores(te, scores))
        )

    for arm, label, expanding in (
        ("A5", "사례 검색만 (RAG 검색부)", False),
        ("A5b", "사례 검색만 — 매 패치 갱신", True),
    ):
        found, _ = _retrieved(rows, test, at, k=25, expanding=expanding)
        out.append(Result(arm, label, False, _rank_scores(te, np.array(found))))

    for arm, label, history in (
        ("A7", "부스팅 트리 — 수준 + 추세", False),
        ("A7h", "부스팅 트리 — + 이력·역할", True),
    ):
        enc = fit_encoder(train, with_trend=True, with_history=history)
        tr, ts = encode(train, enc), encode(test, enc)
        boost = _boosting(seed).fit(tr.x, tr.y)
        out.append(
            Result(arm, label, False, _rank_scores(ts, boost.predict_proba(ts.x)[:, 1]))
        )

    base = sum(r.adjusted_next for r in test) / len(test)
    return out, {"기준선": base, "학습": len(train), "평가": len(test)}


# --- ② 방향 -----------------------------------------------------------


def direction_arms(
    rows: tuple[PanelRow, ...],
    at: str,
    seed: int,
    rules: tuple[Rule, ...] = (),
    judgments: Mapping[tuple[str, str], Judgment] | None = None,
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

    # `B1p` · `B2p` 는 프로 경기 피처를 더한 것이다. **같은 모델·같은 분할에서
    # 피처만 다르게 둬야** 프로 데이터가 값을 하는지 답할 수 있다.
    for arm, label, trend, history, pro in (
        ("B1", "로지스틱 회귀 — 수준", False, False, False),
        ("B1p", "로지스틱 회귀 — 수준 + 프로", False, False, True),
        ("B2", "로지스틱 회귀 — 수준 + 추세", True, False, False),
        ("B2p", "로지스틱 회귀 — 수준 + 추세 + 프로", True, False, True),
        ("B2h", "로지스틱 회귀 — + 이력·역할", True, True, False),
    ):
        enc = fit_encoder(train, with_trend=trend, with_history=history, with_pro=pro)
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

    if rules:
        predictor = RulePredictor.fit(rules, train)
        scores = np.array([predictor.nerf_score(r) for r in test])
        out.append(
            Result(
                "B4",
                "규칙 엔진 — 대화 중 제안",
                True,
                {
                    "auc": roc_auc(te.y, scores),
                    "accuracy": float(((scores >= 0.5).astype(int) == te.y).mean()),
                },
            )
        )

    for arm, label, expanding in (
        ("B5", "사례 검색만 (RAG 검색부)", False),
        ("B5b", "사례 검색만 — 매 패치 갱신", True),
    ):
        _, found = _retrieved(pool, test, at, k=25, expanding=expanding)
        out.append(Result(arm, label, False, scored(np.array(found), True)))

    # `B5p` 는 거리에 프로 경기를 넣고 가까운 사례에 가중을 준다.
    # **셋 다 학습 구간 3겹 교차검증으로 골랐다** — 피처·k·가중 방식.
    _, found = _retrieved(
        pool,
        test,
        at,
        k=_RETRIEVAL_K,
        expanding=False,
        features=CASE_FEATURES_PRO,
        weighted=True,
    )
    out.append(
        Result(
            "B5p", "사례 검색 — + 프로 · 거리가중", False, scored(np.array(found), True)
        )
    )

    # `B6` — **검색부 위에 판단을 올린다.** `B5` 와 같은 이웃 25종을 보고 대화
    # 안에서 매긴 점수를 읽어 채점한다. 실행 시점 API 호출은 0회다.
    # 근거는 `docs/adr/0006-rag-generation-and-contamination-control.md`.
    if judgments:
        picked = [
            (row, judgments[(anon_key(row), "anon")])
            for row in test
            if (anon_key(row), "anon") in judgments
        ]
        # **평가 구간을 다 덮지 못하면 `B5` 와 짝지어 비교가 안 된다.**
        # 부분만 있는 채로 표에 실으면 다른 표본의 수치를 나란히 놓게 된다.
        if len(picked) == len(test):
            scores = np.array([j.nerf_prob / 100 for _, j in picked])
            out.append(
                Result(
                    "B6",
                    "사례 검색 + 판단 — 대화 중",
                    True,
                    scored(scores, True),
                )
            )

    for arm, label, history in (
        ("B7", "부스팅 트리 — 수준 + 추세", False),
        ("B7h", "부스팅 트리 — + 이력·역할", True),
    ):
        enc = fit_encoder(train, with_trend=True, with_history=history)
        tr = encode(train, enc, target="direction")
        ts = encode(test, enc, target="direction")
        boost = _boosting(seed).fit(tr.x, tr.y)
        probability = boost.predict_proba(ts.x)[:, 1]
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

    nerf = sum(1 for r in test if r.direction_next == "nerf")
    return out, {
        "기준선": nerf / len(test),
        "학습": len(train),
        "평가": len(test),
        "손 라벨": sum(1 for r in pool if r.direction_source == "label"),
    }
