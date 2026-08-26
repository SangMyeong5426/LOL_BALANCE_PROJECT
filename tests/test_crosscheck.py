"""추출 결과 채점 테스트.

네트워크를 쓰지 않는다. 채점 규칙만 본다 — **숫자로 맞추고, 이름은 사전으로
잇고, Data Dragon 이 못 보는 것을 놓침으로 세지 않는다.**
"""

from __future__ import annotations

import pytest

from lol_balance.cdragon import Change as ValueChange
from lol_balance.crosscheck import cross_check, numbers, verify_values
from lol_balance.ddragon import Change
from lol_balance.extract import ChangeRecord

NAMES = {"Belveth": "Bel'Veth", "MonkeyKing": "Wukong"}


def _record(champion: str, field: str, before: str, after: str) -> ChangeRecord:
    return ChangeRecord(
        champion=champion,
        ability=None,
        field=field,
        before=before,
        after=after,
        direction="nerf",
        source=f"{field} changed to {after} from {before}.",
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (330, (330.0,)),
        ("22/19/16/13/10", (22.0, 19.0, 16.0, 13.0, 10.0)),
        ("15 / 45 / 75 / 105 / 135", (15.0, 45.0, 75.0, 105.0, 135.0)),
        ("60% AD", (60.0,)),
        ("0.85", (0.85,)),
    ],
)
def test_numbers_normalizes_every_notation(
    value: object, expected: tuple[float, ...]
) -> None:
    """표기가 달라도 숫자열이 같으면 같은 변경이다."""
    assert numbers(value) == expected


def test_matches_across_different_field_names() -> None:
    """`hp` 와 「base health」를 잇는 사전 없이 맞아야 한다."""
    change = Change("Belveth", "stat", "hp", 610, 580)
    record = _record("Bel'Veth", "base health", "610", "580")

    result = cross_check([change], [record], NAMES)

    assert result.scored == 1
    assert result.recall == 1.0
    assert result.unverifiable == ()


def test_display_name_is_bridged_by_the_mapping() -> None:
    """위키는 표시 이름을, diff 는 id 를 쓴다. 표기를 믿지 않는다."""
    change = Change("MonkeyKing", "spell", "Q.cooldown", "9/8/7/6/5", "8/7/6/5/4")
    record = _record("Wukong", "cooldown", "9 / 8 / 7 / 6 / 5", "8 / 7 / 6 / 5 / 4")

    assert cross_check([change], [record], NAMES).recall == 1.0


def test_a_change_the_extraction_missed_counts_against_it() -> None:
    change = Change("Belveth", "stat", "hp", 610, 580)

    result = cross_check([change], [], NAMES)

    assert result.missed == (change,)
    assert result.recall == 0.0


def test_damage_only_records_are_unverifiable_not_wrong() -> None:
    """피해량은 Data Dragon 에 아예 없다. 환각으로 세면 안 된다."""
    record = _record("Ahri", "base damage", "40 / 65 / 90", "45 / 70 / 95")

    result = cross_check([], [record], NAMES)

    assert result.unverifiable == (record,)
    assert result.scored == 0


def test_noisy_kinds_are_not_scored() -> None:
    """툴팁 변경은 대부분 변수명 정리라 채점 기준이 못 된다."""
    noisy = Change("Ahri", "tooltip", "Q.numbers", ["40"], ["45"])

    assert cross_check([noisy], [], NAMES).scored == 0


def test_one_record_cannot_satisfy_two_changes() -> None:
    """같은 값이 두 번 바뀌면 두 기록이 있어야 한다."""
    twice = [
        Change("Belveth", "stat", "hp", 610, 580),
        Change("Belveth", "stat", "mp", 610, 580),
    ]
    result = cross_check(
        twice, [_record("Bel'Veth", "base health", "610", "580")], NAMES
    )

    assert len(result.matched) == 1
    assert len(result.missed) == 1


# ── cdragon 2차 통과 ─────────────────────────────────────────────────────
#
# **1차에 합치지 않는다.** cdragon 은 노트에 한 줄도 없는 내부 값 변경까지
# 잡는데, 그것을 「놓침」으로 세면 라이엇이 안 적은 것을 못 뽑았다고 벌점을
# 주는 셈이다. 그래서 1차에서 「대조 불가」로 남은 것만 다시 본다.


def _value(champion: str, field: str, before: object, after: object) -> ValueChange:
    return ValueChange(champion, "value", field, before, after)  # type: ignore[arg-type]


def test_a_ratio_matches_across_the_scale_difference() -> None:
    """cdragon 은 `0.675`, 노트는 `67.5% AD`. **같은 값이다.**"""
    record = _record("Aatrox", "first cast ad ratio", "60 / 70% AD", "60 / 67.5% AD")
    change = _value("Aatrox", "AatroxQ.QTotalADRatio", (0.6, 0.7), (0.6, 0.675))

    got = verify_values([record], [change])

    assert got.confirmed == ((change, record),)
    assert got.unverifiable == ()


def test_float32_noise_does_not_break_the_match() -> None:
    """**실제로 이것 때문에 일치가 6/41 로 떨어졌다.**

    `0.6000000238418579 × 100 = 60.00000238418579` 이라 소수 6자리로 자르면
    `60.000002` 가 되어 노트의 `60` 과 안 맞는다.
    """
    record = _record("Aatrox", "ad ratio", "60% AD", "67.5% AD")
    change = _value(
        "Aatrox", "AatroxQ.QRatio", (0.6000000238418579,), (0.675000011920929,)
    )

    assert verify_values([record], [change]).confirmed


def test_what_cannot_be_matched_stays_unverifiable() -> None:
    """노트가 파생값을 쓰면 원리적으로 못 맞춘다.

    Aatrox Q 는 `QTotalADRatio` 하나에서 「maximum ad ratio」(3연타 합) 같은
    문장이 나온다. 저장된 것은 하나뿐이다.
    """
    record = _record("Aatrox", "maximum ad ratio", "360% AD", "540% AD")
    change = _value("Aatrox", "AatroxQ.QTotalADRatio", (0.6,), (0.9,))

    got = verify_values([record], [change])

    assert got.confirmed == ()
    assert got.unverifiable == (record,)
    assert got.rate == 0.0


def test_champion_names_are_bridged_here_too() -> None:
    """diff 는 id 를, 노트는 표시 이름을 쓴다. **이름 표기를 믿지 않는다.**"""
    record = _record("Bel'Veth", "q damage", "10", "20")
    change = _value("Belveth", "BelvethQ.QDamage", (10.0,), (20.0,))

    assert verify_values([record], [change], NAMES).confirmed


def test_one_change_is_not_spent_twice() -> None:
    same = [_record("Ahri", "q damage", "10", "20") for _ in range(2)]
    change = _value("Ahri", "AhriQ.QDamage", (10.0,), (20.0,))

    got = verify_values(same, [change])

    assert len(got.confirmed) == 1
    assert len(got.unverifiable) == 1
