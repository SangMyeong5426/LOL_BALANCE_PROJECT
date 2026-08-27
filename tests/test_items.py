"""아이템 수치 변경 테스트.

**아이템 조정은 정답지에 없다.** 라벨은 패치 노트의 챔피언 절에서 오는데 조정의
상당수가 아이템으로 이뤄진다. 여기서 지키는 것은 둘이다.

    협곡만 센다      아레나·ARAM 전용 아이템은 수치가 따로 논다
    완성템을 가른다   빌드를 실제로 흔드는 것은 부품이 아니라 완성템이다
"""

from __future__ import annotations

import json
from pathlib import Path

from lol_balance.items import LOUD, churn, diff_items, read_items


def item(
    name: str,
    gold: int = 3000,
    *,
    sr: bool = True,
    buy: bool = True,
    **stats: float,
) -> dict:
    return {
        "name": name,
        "gold": {"total": gold, "purchasable": buy},
        "maps": {"11": sr, "12": True},
        "stats": stats,
    }


def write(path: Path, items: dict) -> Path:
    path.write_text(json.dumps({"data": items}), encoding="utf-8")
    return path


def test_only_rift_items_that_can_be_bought(tmp_path: Path) -> None:
    """다른 맵 전용 수치를 협곡 승률 옆에 두면 값이 튄다."""
    got = read_items(
        write(
            tmp_path / "a.json",
            {
                "1": item("협곡 완성템"),
                "2": item("아레나 전용", sr=False),
                "3": item("못 사는 것", buy=False),
            },
        )
    )

    assert set(got) == {"1"}


def test_gold_and_stat_changes_are_both_caught() -> None:
    before = {"1": item("Essence Reaver", 2900, FlatCritChanceMod=0.2)}
    after = {"1": item("Essence Reaver", 3200, FlatCritChanceMod=0.25)}

    got = {(c.field, c.before, c.after) for c in diff_items(before, after)}

    assert got == {("gold", 2900.0, 3200.0), ("FlatCritChanceMod", 0.2, 0.25)}


def test_new_and_removed_items_are_not_changes() -> None:
    """출시·삭제는 값 변경이 아니다. 챔피언 쪽에서 신규 출시를 빼는 것과 같다."""
    before = {"1": item("있던 것")}
    after = {"1": item("있던 것"), "2": item("새로 나온 것")}

    assert diff_items(before, after) == ()
    assert diff_items(after, before) == ()


def test_finished_items_are_counted_apart() -> None:
    """**부품 열 개보다 완성템 하나가 빌드를 더 흔든다.**"""
    before = {"1": item("완성템", 3000), "2": item("부품", 900)}
    after = {"1": item("완성템", 3200), "2": item("부품", 800)}

    got = churn(before, after)

    assert got.items == 2
    assert got.finished == 1
    assert got.total == 2


def test_a_quiet_patch_has_no_churn() -> None:
    same = {"1": item("그대로", 3000)}

    assert churn(same, same).items == 0


def test_the_threshold_sits_at_the_ninetieth_percentile() -> None:
    """**73패치의 90% 분위가 8종이고 중앙값은 1종이다.**

    문턱이 낮으면 절반의 패치에 경고가 붙어 뜻이 없어지고, 높으면 `14_10`
    (Lucian–Nami 가 무너진 패치, 완성템 30종)을 놓친다.
    """
    assert LOUD == 8
