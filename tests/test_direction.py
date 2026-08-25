"""방향 판정 테스트.

**Data Dragon diff 는 방향을 모른다.** 필드를 알면 기계적으로 정해진다는 것이
이 모듈의 전부다. 극성이 틀리면 정답지가 통째로 뒤집힌다.
"""

from __future__ import annotations

import pytest

from lol_balance.ddragon import Change
from lol_balance.direction import (
    champion_direction,
    change_direction,
    drop_mass_changes,
    value_shift,
)
from lol_balance.groundtruth import compare


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (330, 335, "up"),
        (610, 580, "down"),
        ("22/19/16/13/10", "21/18/15/12/9", "down"),
        # 스칼라가 배열로 펴지는 경우. 첫 값은 그대로고 나머지가 내려간다.
        ("11", "11/10.5/10/9.5/9", "down"),
        ("1500/2250/3000", "2000/2500/3000", "up"),
        # 저레벨 너프 · 고레벨 버프. 한 방향으로 뭉개면 안 된다.
        ("10/20/30", "5/20/40", None),
        (100, 100, None),
        ("a", "b", None),
    ],
)
def test_value_shift(before: object, after: object, expected: str | None) -> None:
    assert value_shift(before, after) == expected


@pytest.mark.parametrize(
    ("kind", "field", "before", "after", "expected"),
    [
        ("stat", "hp", 610, 580, "nerf"),
        ("stat", "movespeed", 330, 335, "buff"),
        ("stat", "attackspeed", 0.85, 0.67, "nerf"),
        # 스킬은 자원과 대기시간이 반대다.
        ("spell", "Q.cooldown", 12, 10, "buff"),
        ("spell", "W.cost", 40, 60, "nerf"),
        ("spell", "E.range", 650, 800, "buff"),
        # 극성을 모르는 필드는 채점하지 않는다. 추측하면 정답지가 오염된다.
        ("stat", "unknownstat", 1, 2, None),
        ("tooltip", "Q.numbers", ["10"], ["20"], None),
    ],
)
def test_change_direction(
    kind: str, field: str, before: object, after: object, expected: str | None
) -> None:
    assert change_direction(Change("Ahri", kind, field, before, after)) == expected


def test_champion_with_both_directions_is_mixed() -> None:
    """한 챔피언을 조정하며 어떤 것은 올리고 어떤 것은 내리는 일이 흔하다."""
    changes = [
        Change("Ahri", "stat", "hp", 610, 580),
        Change("Ahri", "spell", "Q.cooldown", 12, 10),
    ]
    assert champion_direction(changes) == "mixed"


def test_champion_with_no_scorable_change_is_unknown() -> None:
    """피해량만 바꾼 조정은 Data Dragon 에 안 나타난다."""
    assert (
        champion_direction([Change("Ahri", "tooltip", "Q.numbers", ["1"], ["2"])])
        is None
    )


@pytest.mark.parametrize(
    ("label", "automatic", "verdict"),
    [
        ("nerf", "nerf", "agree"),
        # 자동은 diff 가 본 것만 안다. 노트에서 반대 방향을 더 찾으면 mixed 가 맞다.
        ("mixed", "buff", "extends"),
        ("mixed", "nerf", "extends"),
        ("buff", "nerf", "conflict"),
        ("nerf", "mixed", "conflict"),
    ],
)
def test_compare_separates_extension_from_conflict(
    label: str, automatic: str, verdict: str
) -> None:
    assert compare(label, automatic) == verdict  # type: ignore[arg-type]


def test_a_field_that_changes_for_everyone_is_not_a_balance_change() -> None:
    """16.5 에서 `attackdamageperlevel` 이 171종 전부 5 → 0 이 됐다.

    스키마가 바뀐 것이지 라이엇이 전 챔피언을 너프한 것이 아니다. 걸러내지
    않으면 그 패치의 방향 라벨이 통째로 nerf 가 되고, 실제로 노트에 버그 수정
    한 줄뿐인 챔피언까지 너프로 잡혔다.
    """
    schema = [Change(f"C{i}", "stat", "attackdamageperlevel", 5, 0) for i in range(90)]
    real = Change("Ahri", "stat", "hp", 610, 580)

    kept = drop_mass_changes([*schema, real], champion_count=100)

    assert kept == (real,)


def test_a_field_that_changes_for_a_few_survives() -> None:
    """열 종이 같은 스탯을 조정받는 것은 흔한 밸런스 패치다."""
    changes = [Change(f"C{i}", "stat", "hp", 610, 580) for i in range(10)]

    assert len(drop_mass_changes(changes, champion_count=100)) == 10


def test_mass_filter_is_a_no_op_without_a_champion_count() -> None:
    changes = [Change("Ahri", "stat", "hp", 610, 580)]
    assert drop_mass_changes(changes, champion_count=0) == tuple(changes)
