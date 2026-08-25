"""Data Dragon 버전 diff 테스트."""

from __future__ import annotations

from typing import Any

from lol_balance.ddragon import (
    added_champions,
    diff_versions,
    is_standard_champion,
    standard_champions,
)


def champ(key: str = "103", **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "key": key,
        "stats": {"hp": 590, "armor": 21, "attackdamage": 53},
        "spells": [
            {
                "cooldownBurn": "7",
                "costBurn": "55/65/75/85/95",
                "rangeBurn": "970",
                "effectBurn": [None, "0"],
                "tooltip": "Deals <magicDamage>{{ totaldamage }} damage</magicDamage> for 3 seconds.",
            }
        ],
    }
    base.update(over)
    return base


def test_detects_a_base_stat_change() -> None:
    a, b = {"Ahri": champ()}, {"Ahri": champ()}
    b["Ahri"]["stats"]["hp"] = 620

    (change,) = diff_versions(a, b)

    assert (change.champion, change.kind, change.field) == ("Ahri", "stat", "hp")
    assert (change.before, change.after) == (590, 620)


def test_detects_a_cooldown_change() -> None:
    a, b = {"Ahri": champ()}, {"Ahri": champ()}
    b["Ahri"]["spells"][0]["cooldownBurn"] = "6"

    (change,) = diff_versions(a, b)

    assert change.field == "Q.cooldown"
    assert (change.before, change.after) == ("7", "6")


def test_placeholder_rename_is_not_a_balance_change() -> None:
    """`{{ e1 }}` → `{{ stackduration }}` 은 변수명 정리다.

    한 패치의 툴팁 변경 32건 중 30건이 이것이었다. 걸러내지 않으면 조정이
    없는 패치가 조정 투성이로 보인다.
    """
    a, b = {"Ahri": champ()}, {"Ahri": champ()}
    b["Ahri"]["spells"][0]["tooltip"] = (
        "Deals <magicDamage>{{ e1 }} damage</magicDamage> for 3 seconds."
    )

    assert diff_versions(a, b) == []


def test_real_number_in_tooltip_is_a_change() -> None:
    a, b = {"Ahri": champ()}, {"Ahri": champ()}
    b["Ahri"]["spells"][0]["tooltip"] = (
        "Deals <magicDamage>{{ totaldamage }} damage</magicDamage> for 5 seconds."
    )

    (change,) = diff_versions(a, b)

    assert change.kind == "tooltip"
    assert (change.before, change.after) == (["3"], ["5"])


def test_spell_count_change_is_recorded() -> None:
    a, b = {"Ahri": champ()}, {"Ahri": champ()}
    b["Ahri"]["spells"] = b["Ahri"]["spells"] * 2

    kinds = {(c.kind, c.field) for c in diff_versions(a, b)}

    assert ("spell", "count") in kinds


def test_game_mode_variants_are_excluded() -> None:
    """`Jade_Ahri`(key 60103)는 정식 챔피언이 아니다."""
    assert is_standard_champion("Ahri", champ("103"))
    assert not is_standard_champion("Jade_Ahri", champ("60103"))

    data = {"Ahri": champ("103"), "Jade_Ahri": champ("60103")}
    assert set(standard_champions(data)) == {"Ahri"}


def test_added_champions_ignores_variants() -> None:
    a = {"Ahri": champ("103")}
    b = {"Ahri": champ("103"), "Jade_Ahri": champ("60103"), "Aatrox": champ("266")}

    assert added_champions(a, b) == ["Aatrox"]
