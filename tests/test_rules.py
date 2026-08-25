"""규칙 엔진 테스트.

**제안은 사람·LLM 이 하고 채점은 코드가 한다.** 채점이 틀리면 밸런스 기준
문서에 우연이 실린다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import PanelRowFactory
from lol_balance.rules import Condition, Rule, read_rules, score, write_rules


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
