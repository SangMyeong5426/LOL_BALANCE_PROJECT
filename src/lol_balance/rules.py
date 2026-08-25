"""밸런스 기준 규칙 — **제안은 사람·LLM, 채점은 코드.**

증명 대상 1번(「공개 데이터만으로 조정 기준을 문서화할 수 있다」)의 산출물이다.

**규칙은 기계가 실행할 수 있어야 한다.** 「승률이 높고 픽률도 높으면 너프한다」는
사람에게는 읽히지만 백테스트가 안 되고, 그러면 그 기준이 맞는지 영영 못 잰다.
그래서 조건을 구조로 받는다.

    승률 ≥ 0.525 그리고 픽률 ≥ 0.05  →  너프

**LLM 은 후보를 내고 코드가 거른다.** 학습 구간에서 성적이 안 나오는 규칙은
밸런스 기준 문서에 못 들어간다. 이 경계가 이 프로젝트의 축이다.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from lol_balance.panel import PanelRow

Op = Literal[">=", "<=", ">", "<"]
# 규칙이 볼 수 있는 값. **다음 패치 정보는 없다** — 그것이 맞히려는 답이다.
METRICS = ("win_rate", "wr_gap", "pick_rate", "ban_rate", "matches", "d_win_rate")
Action = Literal["nerf", "buff", "adjusted"]


@dataclass(frozen=True)
class Condition:
    metric: str
    op: Op
    value: float

    def holds(self, row: PanelRow) -> bool:
        """값이 없으면 **성립하지 않는 것으로 본다.**

        밴 데이터가 빠진 패치(14_5)나 직전 패치가 없어 추세를 못 구한 곳이
        있다. 없는 값을 0 으로 치면 「밴이 0회였다」가 되어 조용히 틀린다.
        """
        actual = getattr(row, self.metric, None)
        if actual is None:
            return False
        if self.op == ">=":
            return float(actual) >= self.value
        if self.op == "<=":
            return float(actual) <= self.value
        if self.op == ">":
            return float(actual) > self.value
        return float(actual) < self.value

    def __str__(self) -> str:
        return f"{self.metric} {self.op} {self.value:g}"


# 규칙을 누가 냈는가. **이것을 안 적으면 자동화된 파이프라인이 돈 것처럼 읽힌다.**
#
#   conversation  대화 중 Claude 가 학습 구간을 읽고 제안했다. API 호출 없음
#   api           `scripts/` 가 Anthropic API 를 불러 받았다
#   human         사람이 직접 적었다
Provenance = Literal["conversation", "api", "human"]


@dataclass(frozen=True)
class Rule:
    """조건이 **모두** 맞으면 행동을 예측한다."""

    id: str
    when: tuple[Condition, ...]
    then: Action
    rationale: str = ""
    proposed_by: Provenance = "conversation"

    def fires(self, row: PanelRow) -> bool:
        return all(c.holds(row) for c in self.when)

    def __str__(self) -> str:
        return f"{self.id}: {' 그리고 '.join(str(c) for c in self.when)} → {self.then}"


def _matches(row: PanelRow, action: Action) -> bool:
    if action == "adjusted":
        return row.adjusted_next
    return row.direction_next == action


def _population(rows: tuple[PanelRow, ...], action: Action) -> tuple[PanelRow, ...]:
    """그 행동을 물을 수 있는 행만 남긴다.

    방향 규칙은 **조정된 챔피언 중 방향이 분명한 것**에만 뜻이 있다. 조정 안 된
    챔피언까지 넣으면 「조정될까」와 「어느 쪽인가」가 한 규칙에 섞인다.
    """
    if action == "adjusted":
        return rows
    return tuple(r for r in rows if r.direction_next in ("nerf", "buff"))


@dataclass(frozen=True)
class Score:
    """규칙 하나의 성적."""

    rule: Rule
    fired: int
    correct: int
    population: int
    positives: int

    @property
    def precision(self) -> float:
        """규칙이 걸린 것 중 실제로 맞은 비율."""
        return self.correct / self.fired if self.fired else float("nan")

    @property
    def coverage(self) -> float:
        """모집단 중 규칙이 걸린 비율. **낮으면 규칙이 아니라 예외다.**"""
        return self.fired / self.population if self.population else float("nan")

    @property
    def base_rate(self) -> float:
        return self.positives / self.population if self.population else float("nan")

    @property
    def lift(self) -> float:
        """기준선 대비 몇 배인가. **1.0 이면 아무것도 설명하지 못한 것이다.**"""
        base = self.base_rate
        return self.precision / base if base else float("nan")


def score(rule: Rule, rows: tuple[PanelRow, ...]) -> Score:
    pool = _population(rows, rule.then)
    fired = [r for r in pool if rule.fires(r)]
    return Score(
        rule=rule,
        fired=len(fired),
        correct=sum(1 for r in fired if _matches(r, rule.then)),
        population=len(pool),
        positives=sum(1 for r in pool if _matches(r, rule.then)),
    )


def write_rules(path: Path, rules: tuple[Rule, ...]) -> None:
    """규칙을 저장한다. **텍스트로 커밋한다** — 다시 만들면 같다는 보장이 없다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            json.dumps(asdict(r), ensure_ascii=False, sort_keys=True) for r in rules
        )
        + "\n"
    )


def read_rules(path: Path) -> tuple[Rule, ...]:
    if not path.exists():
        return ()
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        raw: dict[str, Any] = json.loads(line)
        out.append(
            Rule(
                id=raw["id"],
                when=tuple(Condition(**c) for c in raw["when"]),
                then=raw["then"],
                rationale=raw.get("rationale", ""),
                proposed_by=raw.get("proposed_by", "conversation"),
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class RulePredictor:
    """규칙 묶음을 점수 하나로 바꾼다.

    **가중치는 학습 구간 정확도다.** 평가 성적을 가중치로 쓰면 그것이 곧 누출이다.
    `fit` 은 학습 행만 받는다.

    여러 규칙이 걸리면 **noisy-OR** 로 합친다 — `1 − Π(1 − pᵢ)`. 규칙끼리
    독립이라고 가정하는 것이고 실제로는 겹치지만, 「많이 걸릴수록 확신이 높다」를
    표현하면서 [0, 1] 안에 머무는 가장 단순한 방법이다. 표를 세우려면 연속
    점수가 필요한데 단순히 걸린 규칙 수를 세면 동점이 너무 많다.
    """

    weights: dict[str, float]
    rules: tuple[Rule, ...]
    fallback: dict[str, float]

    @staticmethod
    def fit(rules: tuple[Rule, ...], train: tuple[PanelRow, ...]) -> RulePredictor:
        weights: dict[str, float] = {}
        for rule in rules:
            result = score(rule, train)
            # 걸리지 않은 규칙은 가중치가 없다. 정확도를 못 재기 때문이다.
            weights[rule.id] = result.precision if result.fired else 0.0
        fallback: dict[str, float] = {}
        for action in ("adjusted", "nerf"):
            pool = _population(train, action)
            positives = sum(1 for r in pool if _matches(r, action))
            fallback[action] = positives / len(pool) if pool else 0.0
        return RulePredictor(weights, rules, fallback)

    def _evidence(self, row: PanelRow, action: Action) -> float:
        product = 1.0
        for rule in self.rules:
            if rule.then == action and rule.fires(row):
                product *= 1.0 - self.weights.get(rule.id, 0.0)
        return 1.0 - product

    def adjusted_score(self, row: PanelRow) -> float:
        """조정될 가능성. 걸리는 규칙이 없으면 학습 구간 기준선."""
        evidence = self._evidence(row, "adjusted")
        return evidence if evidence > 0 else self.fallback["adjusted"]

    def nerf_score(self, row: PanelRow) -> float:
        """너프일 가능성. 양쪽 근거를 견줘 비율로 만든다."""
        nerf, buff = self._evidence(row, "nerf"), self._evidence(row, "buff")
        if nerf == 0 and buff == 0:
            return self.fallback["nerf"]
        return nerf / (nerf + buff)
