"""방향 판정 테스트.

**Data Dragon diff 는 방향을 모른다.** 필드를 알면 기계적으로 정해진다는 것이
이 모듈의 전부다. 극성이 틀리면 정답지가 통째로 뒤집힌다.
"""

from __future__ import annotations

import pytest

from lol_balance.cdragon import Change as ValueChange
from lol_balance.ddragon import Change
from lol_balance.direction import (
    champion_direction,
    change_direction,
    drop_mass_changes,
    value_direction,
    value_polarity,
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


# ── CommunityDragon 값의 방향 ────────────────────────────────────────────
#
# 이름이 챔피언마다 달라(13.15 한 패치에 1,044종) 정확한 목록으로는 못 잡는다.
# 낱말로 잡되 **모르면 판정하지 않는다** — 틀린 라벨은 없느니만 못하다.


def _change(field: str, before: object, after: object) -> ValueChange:
    return ValueChange("Aatrox", "value", field, before, after)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("QBaseDamage", 1),
        ("QTotalADRatio", 1),
        ("ShieldBase", 1),
        ("SlowDuration", 1),  # 적을 더 오래 느리게 = 버프
        ("QCooldown", -1),
        ("ManaCost", -1),
        ("SelfSlowDuration", -1),  # slow 를 갖지만 자기가 느려진다
        ("MonsterDamageCap", None),  # damage 를 갖지만 상한이다
        ("AttackWindup", None),
        ("SomethingUnknown", None),
    ],
)
def test_polarity_reads_the_words_in_the_name(name: str, expected: int | None) -> None:
    assert value_polarity(name) == expected


def test_self_prefixed_names_are_read_before_the_plain_ones() -> None:
    """`SelfSlow` 가 `slow` 로 읽히면 방향이 뒤집힌다."""
    assert value_polarity("SelfSlowAmount") == -1
    assert value_polarity("SlowAmount") == 1


def test_damage_going_up_is_a_buff() -> None:
    changes = [_change("AatroxQ.QBaseDamage", (10.0, 20.0), (15.0, 25.0))]

    assert value_direction(changes) == "buff"


def test_cooldown_going_up_is_a_nerf() -> None:
    changes = [_change("AatroxQ.QCooldown", (9.0,), (10.0,))]

    assert value_direction(changes) == "nerf"


def test_two_ways_at_once_is_mixed() -> None:
    changes = [
        _change("AatroxQ.QBaseDamage", (10.0,), (15.0,)),
        _change("AatroxQ.QCooldown", (9.0,), (10.0,)),
    ]

    assert value_direction(changes) == "mixed"


def test_an_unknown_name_is_not_guessed() -> None:
    assert value_direction([_change("AatroxQ.Whatever", (1.0,), (2.0,))]) is None


def test_arrays_of_different_length_are_not_compared() -> None:
    """**Jax 15.22 가 여기서 걸렸다.**

    공식 부품이 `(0.7, 2.0) → (2.0,)` 로 하나 사라졌는데 평균이 1.35 → 2.0
    이라 「올랐다」로 읽혔다. 실제로는 몬스터 피해량 상한 신설이라 너프였고,
    손 라벨 241개 중 유일한 충돌이었다.
    """
    changes = [_change("JaxE.TotalDamage.mFormulaParts[1]", (0.7, 2.0), (2.0,))]

    assert value_direction(changes) is None


def test_a_field_that_appears_from_nothing_has_no_direction() -> None:
    """없던 값이 생긴 것은 크기 비교로 못 읽는다."""
    assert value_direction([_change("JaxE.MonsterDamageCap", None, (9000.0,))]) is None
