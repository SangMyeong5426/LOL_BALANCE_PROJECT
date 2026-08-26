"""규칙 엔진 테스트.

**제안은 사람·LLM 이 하고 채점은 코드가 한다.** 채점이 틀리면 밸런스 기준
문서에 우연이 실린다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import PanelRowFactory
from lol_balance.rules import (
    Condition,
    Rule,
    RulePredictor,
    read_rules,
    score,
    write_rules,
)


def test_all_conditions_must_hold(make_row: PanelRowFactory) -> None:
    rule = Rule(
        "r",
        (Condition("win_rate", ">=", 0.52), Condition("pick_rate", ">=", 0.08)),
        "nerf",
    )

    assert rule.fires(make_row("13_14", 1, win_rate=0.53, pick_rate=0.09))
    assert not rule.fires(make_row("13_14", 2, win_rate=0.53, pick_rate=0.05))


def test_a_missing_value_never_satisfies_a_condition(make_row: PanelRowFactory) -> None:
    """밴 데이터가 빠진 패치가 있다. 없는 값을 0 으로 치면 「밴 0회」가 된다."""
    rule = Rule("r", (Condition("ban_rate", "<", 0.05),), "buff")

    assert not rule.fires(make_row("13_14", 1, ban_rate=None))
    assert rule.fires(make_row("13_14", 2, ban_rate=0.01))


def test_direction_rules_ignore_champions_that_were_not_adjusted(
    make_row: PanelRowFactory,
) -> None:
    """「조정될까」와 「어느 쪽인가」가 한 규칙에 섞이면 안 된다."""
    rows = (
        make_row("13_14", 1, win_rate=0.52, adjusted_next=True, direction_next="nerf"),
        make_row("13_14", 2, win_rate=0.52, adjusted_next=True, direction_next="buff"),
        make_row("13_14", 3, win_rate=0.52),
        make_row("13_14", 4, win_rate=0.52, adjusted_next=True, direction_next="mixed"),
    )
    result = score(Rule("r", (Condition("win_rate", ">=", 0.5),), "nerf"), rows)

    assert result.population == 2  # nerf 와 buff 만
    assert result.fired == 2
    assert result.correct == 1


def test_target_rules_see_every_champion(make_row: PanelRowFactory) -> None:
    rows = (
        make_row("13_14", 1, adjusted_next=True),
        make_row("13_14", 2),
        make_row("13_14", 3),
    )
    result = score(Rule("r", (Condition("win_rate", ">=", 0.0),), "adjusted"), rows)

    assert result.population == 3
    assert result.correct == 1


def test_lift_is_one_when_the_rule_explains_nothing(make_row: PanelRowFactory) -> None:
    """기준선과 같은 정확도면 그 규칙은 아무것도 설명하지 못한 것이다."""
    rows = tuple(
        make_row("13_14", i, win_rate=0.52, adjusted_next=i <= 5) for i in range(1, 11)
    )
    result = score(Rule("r", (Condition("win_rate", ">=", 0.5),), "adjusted"), rows)

    assert result.precision == pytest.approx(0.5)
    assert result.lift == pytest.approx(1.0)


def test_coverage_shows_when_a_rule_is_really_an_exception(
    make_row: PanelRowFactory,
) -> None:
    rows = tuple(make_row("13_14", i, win_rate=0.4 + i * 0.01) for i in range(1, 21))
    result = score(Rule("r", (Condition("win_rate", ">=", 0.595),), "adjusted"), rows)

    assert result.fired == 1  # 0.60 하나뿐
    assert result.coverage == pytest.approx(0.05)


def test_rules_survive_a_round_trip(tmp_path: Path) -> None:
    rules = (
        Rule("a", (Condition("ban_rate", ">=", 0.2),), "adjusted", "근거"),
        Rule("b", (Condition("win_rate", "<", 0.49),), "buff"),
    )
    path = tmp_path / "rules.jsonl"
    write_rules(path, rules)

    assert read_rules(path) == rules


def test_reading_an_absent_file_gives_nothing(tmp_path: Path) -> None:
    assert read_rules(tmp_path / "none.jsonl") == ()


# --- 규칙 예측기 -------------------------------------------------------


def _fitted(make_row: PanelRowFactory, rules: tuple[Rule, ...]) -> RulePredictor:
    train = tuple(
        make_row(
            "13_14",
            i,
            win_rate=0.52 if i <= 10 else 0.48,
            adjusted_next=i <= 8,
            direction_next=("nerf" if i <= 10 else "buff")
            if i <= 8 or i > 12
            else None,
        )
        for i in range(1, 21)
    )
    return RulePredictor.fit(rules, train)


def test_weights_come_from_the_training_rows(make_row: PanelRowFactory) -> None:
    """평가 성적을 가중치로 쓰면 그것이 곧 누출이다."""
    rule = Rule("r", (Condition("win_rate", ">=", 0.5),), "adjusted")
    predictor = _fitted(make_row, (rule,))

    # 학습에서 승률 0.52 인 10종 중 8종이 조정됐다
    assert predictor.weights["r"] == pytest.approx(0.8)


def test_a_rule_that_never_fires_gets_no_weight(make_row: PanelRowFactory) -> None:
    """걸리지 않으면 정확도를 못 재므로 가중치가 없다."""
    rule = Rule("r", (Condition("win_rate", ">=", 0.99),), "adjusted")

    assert _fitted(make_row, (rule,)).weights["r"] == 0.0


def test_more_matching_rules_give_a_higher_score(make_row: PanelRowFactory) -> None:
    """단순히 걸린 규칙 수를 세면 동점이 너무 많아 표를 못 세운다."""
    rules = (
        Rule("a", (Condition("win_rate", ">=", 0.5),), "adjusted"),
        Rule("b", (Condition("pick_rate", ">=", 0.04),), "adjusted"),
    )
    predictor = _fitted(make_row, rules)

    both = predictor.adjusted_score(make_row("15_1", 1, win_rate=0.52, pick_rate=0.05))
    one = predictor.adjusted_score(make_row("15_1", 2, win_rate=0.52, pick_rate=0.01))
    assert both > one


def test_no_matching_rule_falls_back_to_the_training_base_rate(
    make_row: PanelRowFactory,
) -> None:
    rule = Rule("r", (Condition("win_rate", ">=", 0.9),), "adjusted")
    predictor = _fitted(make_row, (rule,))

    assert predictor.adjusted_score(make_row("15_1", 1, win_rate=0.5)) == pytest.approx(
        predictor.fallback["adjusted"]
    )


def test_direction_score_weighs_both_sides(make_row: PanelRowFactory) -> None:
    """너프 근거와 버프 근거를 견줘 비율로 만든다."""
    rules = (
        Rule("n", (Condition("win_rate", ">=", 0.51),), "nerf"),
        Rule("b", (Condition("win_rate", "<", 0.49),), "buff"),
    )
    predictor = _fitted(make_row, rules)
    middle = predictor.nerf_score(make_row("15_1", 3, win_rate=0.50))

    assert predictor.nerf_score(make_row("15_1", 1, win_rate=0.55)) > middle
    assert predictor.nerf_score(make_row("15_1", 2, win_rate=0.45)) < middle
    assert middle == pytest.approx(predictor.fallback["nerf"])


def test_one_sided_evidence_does_not_collapse(make_row: PanelRowFactory) -> None:
    """**한쪽만 걸려도 근거 크기가 점수에 남아야 한다.**

    섞기 전에는 너프 근거만 있으면 그 크기와 무관하게 전부 정확히 `1.0` 이었다.
    평가 234행에서 점수가 5종밖에 안 나왔고, AUC 는 줄 세우기 점수라 그 동점이
    그대로 손해였다(0.807 → 섞은 뒤 0.836).
    """
    # 가중치를 직접 준다. `fit` 으로 만들면 픽스처의 승률이 두 값뿐이라
    # 두 규칙 정확도가 모두 1.0 이 되고 noisy-OR 이 포화해 차이가 안 보인다.
    rules = (
        Rule("weak", (Condition("win_rate", ">=", 0.51),), "nerf"),
        Rule("strong", (Condition("win_rate", ">=", 0.53),), "nerf"),
    )
    predictor = RulePredictor(
        weights={"weak": 0.7, "strong": 0.9},
        rules=rules,
        fallback={"adjusted": 0.2, "nerf": 0.5},
    )
    barely = predictor.nerf_score(make_row("15_1", 1, win_rate=0.515))
    clearly = predictor.nerf_score(make_row("15_1", 2, win_rate=0.55))

    assert barely < clearly < 1.0
    assert barely > predictor.fallback["nerf"]
