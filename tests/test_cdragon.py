"""CommunityDragon 파서 테스트.

**파일을 읽지 않는다.** `.bin.json` 의 모양만 흉내 낸 최소 구조로 판단만 본다 —
어디까지 내려가는가, 칸을 하나씩 보는가, 없는 파일을 어떻게 다루는가.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lol_balance.cdragon import (
    RANK_SLOTS,
    Change,
    changed_share,
    diff_versions,
    field_changes,
    rank_arrays,
    ranks,
    read_champion,
    spells,
    values,
)


def champion(
    data_values: list[dict[str, Any]] | None = None,
    calculations: dict[str, Any] | None = None,
    **spell: Any,
) -> dict[str, Any]:
    """스킬 하나짜리 `.bin.json` 을 만든다."""
    return {
        "{061d1d0b}": {"mSomethingElse": 1},  # 스킬이 아닌 노드도 섞여 있다
        "Characters/Aatrox/Spells/AatroxQAbility/AatroxQ": {
            "mSpell": {
                "mDataValues": data_values or [],
                "mSpellCalculations": calculations or {},
                "__type": "SpellObject",
                **spell,
            }
        },
    }


def value(name: str, *numbers: float) -> dict[str, Any]:
    return {"mName": name, "mValues": list(numbers), "__type": "SpellDataValue"}


def test_only_spell_nodes_are_picked_up() -> None:
    """최상위에 해시 키 노드가 섞여 있다. 실제 파일이 그렇다."""
    assert list(spells(champion())) == ["AatroxQ"]


def test_each_rank_slot_is_its_own_value() -> None:
    """**칸을 통째로 비교하면 안 된다.**

    Data Dragon 쪽에서 이미 겪었다 — 배열째 비교하니 안 바뀐 칸이 섞여
    일치율이 0% 였다.
    """
    got = values(champion([value("QBaseDamage", 10.0, 20.0, 30.0)]))

    assert got[("value", "AatroxQ.QBaseDamage[0]")] == 10.0
    assert got[("value", "AatroxQ.QBaseDamage[2]")] == 30.0


def test_coefficients_buried_in_formulas_are_found() -> None:
    """**계수가 mDataValues 가 아니라 공식 안에 박혀 있다.**

    13.15 한 패치에서 `mCoefficient` 가 364개다. 공식을 안 내려가면 그만큼을
    통째로 놓친다.
    """
    got = values(
        champion(
            calculations={
                "QDamage": {
                    "mFormulaParts": [
                        {"mCoefficient": 0.45, "__type": "StatByCoefficient"},
                    ]
                }
            }
        )
    )

    assert got[("formula", "AatroxQ.QDamage.mFormulaParts[0].mCoefficient")] == 0.45


def test_type_labels_are_not_values() -> None:
    """`__type` 은 이름표다. 값으로 세면 안 된다."""
    got = values(champion(calculations={"QDamage": {"mFormulaParts": []}}))

    assert not [k for k in got if "__type" in k[1]]


def test_flags_are_not_numbers() -> None:
    """파이썬에서 `True` 는 `int` 다. 안 걸러 내면 깃발이 숫자로 섞인다."""
    got = values(champion(mSpellRevealsChampion=True))

    assert not [k for k in got if "Reveals" in k[1]]


def test_fields_that_overlap_data_dragon_are_kept_apart() -> None:
    """쿨다운은 두 출처가 겹치는 자리다 — ADR-0005 가 정한 대조 지점이라
    따로 표시해 둔다."""
    got = values(champion(cooldownTime=7.0))

    assert got[("spell", "AatroxQ.cooldownTime")] == 7.0


def test_a_changed_slot_becomes_one_change() -> None:
    before = champion([value("QRatio", 0.5, 0.6, 0.7)])
    after = champion([value("QRatio", 0.5, 0.6, 0.675)])

    assert diff_versions(before, after, "Aatrox") == (
        Change("Aatrox", "value", "AatroxQ.QRatio[2]", 0.7, 0.675),
    )


def test_an_unchanged_champion_yields_nothing() -> None:
    same = champion([value("QRatio", 0.5, 0.6, 0.7)])

    assert diff_versions(same, same, "Aatrox") == ()


def test_a_missing_previous_version_is_not_a_crash() -> None:
    """그 버전에 아직 없던 챔피언이면 파일이 없는 것이 맞다.

    **출시인지 수집 실패인지는 여기서 정하지 않는다** — 부르는 쪽이 안다.
    """
    after = champion([value("QRatio", 0.6)])

    changes = diff_versions(None, after, "Briar")

    assert changes == (Change("Briar", "value", "AatroxQ.QRatio[0]", None, 0.6),)


def test_ranks_cuts_the_extrapolation_slots() -> None:
    """7칸 중 게임이 쓰는 것은 가운데다. 5랭크면 `[1:6]`."""
    row = [0.5, 0.6, 0.675, 0.75, 0.825, 0.9, 0.975]
    assert len(row) == RANK_SLOTS

    assert ranks(row, 5) == (0.6, 0.675, 0.75, 0.825, 0.9)


def test_ranks_leaves_a_short_row_alone() -> None:
    assert ranks([1.0, 2.0], 5) == (1.0, 2.0)


def test_changed_share_counts_champions_not_fields() -> None:
    """`drop_mass_changes` 를 그대로 못 쓴다 — 그것은 `(kind, field)` 로 세는데
    cdragon 의 필드 이름은 챔피언마다 다르다."""
    assert changed_share(["Aatrox", "Aatrox", "Ahri"], 4) == 0.5
    assert changed_share([], 0) == 0.0


def test_read_champion_returns_none_when_absent(tmp_path: Path) -> None:
    assert read_champion(tmp_path, "13.15", "briar") is None


# ── 필드 단위로 다시 묶기 ────────────────────────────────────────────────
#
# 채점은 **노트가 쓰는 단위**로 이뤄진다. 「60 / 67.5 / 75 / 82.5 / 90% AD」는
# 다섯 칸을 한 문장으로 말한다. 칸별 변경을 그대로 내면 어느 문장과도 안 맞는다.

RANKS = {"AatroxQ": 5}


def test_rank_arrays_puts_the_slots_back_together() -> None:
    got = rank_arrays(champion([value("QRatio", 0.5, 0.6, 0.7)]))

    assert got[("AatroxQ", "QRatio")] == (0.5, 0.6, 0.7)


def test_field_changes_cuts_to_the_game_ranks() -> None:
    before = champion([value("QRatio", 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1)])
    after = champion([value("QRatio", 0.525, 0.6, 0.675, 0.75, 0.825, 0.9, 0.975)])

    got = field_changes(before, after, "Aatrox", RANKS)

    assert got == (
        Change(
            "Aatrox",
            "value",
            "AatroxQ.QRatio",
            (0.6, 0.7, 0.8, 0.9, 1.0),
            (0.6, 0.675, 0.75, 0.825, 0.9),
        ),
    )


def test_a_change_only_in_the_extrapolation_slots_is_not_a_change() -> None:
    """`[0]` 과 `[6]` 은 게임이 안 쓴다. 그것만 움직였으면 아무 일도 없었다."""
    before = champion([value("QRatio", 0.1, 0.6, 0.6, 0.6, 0.6, 0.6, 9.9)])
    after = champion([value("QRatio", 0.2, 0.6, 0.6, 0.6, 0.6, 0.6, 8.8)])

    assert field_changes(before, after, "Aatrox", RANKS) == ()


def test_a_constant_array_collapses_to_one_number() -> None:
    """**노트가 그렇게 쓴다.** 「75% AP」는 다섯 랭크가 모두 75라는 뜻이다.

    다섯 칸으로 내면 한 숫자짜리 문장과 안 맞는다.
    """
    before = champion([value("QAP", 0.0, 0.75, 0.75, 0.75, 0.75, 0.75, 0.75)])
    after = champion([value("QAP", 0.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5)])

    got = field_changes(before, after, "Aatrox", RANKS)

    assert got == (Change("Aatrox", "value", "AatroxQ.QAP", (0.75,), (0.5,)),)


def test_formula_numbers_are_grouped_by_part() -> None:
    """한 문장이 한 부품에서 나온다.

    「1150 – 3500 (based on level)」은 `mStartValue` 와 `mEndValue` 둘을 한
    번에 말한다. 낱개로 내면 어느 쪽도 그 문장과 안 맞는다.
    """
    part = {"mStartValue": 1300.0, "mEndValue": 3200.0, "__type": "ByCharLevel"}
    before = champion(calculations={"RDamage": {"mFormulaParts": [part]}})
    after = champion(
        calculations={
            "RDamage": {
                "mFormulaParts": [{**part, "mStartValue": 1150.0, "mEndValue": 3500.0}]
            }
        }
    )

    got = [
        c for c in field_changes(before, after, "Annie", RANKS) if c.kind == "formula"
    ]

    assert len(got) == 1
    assert got[0].before == (3200.0, 1300.0)  # mEndValue · mStartValue (경로 정렬순)
    assert got[0].after == (3500.0, 1150.0)
