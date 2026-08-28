"""정답 라벨 조립 테스트.

**여기가 조용히 틀리면 모든 성적이 같이 틀린다.** 한때 이 로직이
`scripts/build-panel` 안에 있었는데 스크립트는 테스트가 안 붙었다.

    adjusted_in         ① 대상의 정답
    merge_directions    두 기계 출처가 반대를 말할 때
    directions_in       ② 방향의 정답 — 손 라벨이 기계 판정을 이긴다
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lol_balance.assemble import (
    adjusted_in,
    directions_in,
    merge_directions,
    version,
    version_short,
)


def test_version_forms() -> None:
    """Data Dragon 은 빌드 번호를 쓰고 cdragon 은 안 쓴다."""
    assert version("16_9") == "16.9.1"
    assert version_short("13_15") == "13.15"


# --- ① 대상의 정답 ------------------------------------------------------


def note(tmp: Path, patch: str, *champions: str) -> Path:
    body = "".join(
        '<div class="mw-heading"><h3>Champions</h3></div>'
        f'<dl><dt><span data-champion="{c}"></span></dt></dl>'
        "<ul><li>Stats<ul><li>Base health increased to 600 from 580.</li>"
        "</ul></li></ul>"
        for c in champions
    )
    (tmp / f"{version(patch)}.html").write_text(body, encoding="utf-8")
    return tmp


def test_adjusted_in_reads_the_note(tmp_path: Path) -> None:
    note(tmp_path, "16_9", "Ahri", "Zed")

    assert adjusted_in("16_9", tmp_path) == {"Ahri", "Zed"}


def test_a_missing_note_is_empty_not_zero(tmp_path: Path) -> None:
    """**「노트가 없다」와 「아무도 조정 안 됐다」는 다르다.**

    빈 집합이면 그 패치는 예측 지점이 안 된다. 0 으로 채우면 조용히
    「전원 조정 안 됨」이 되어 ① 의 정답이 통째로 틀린다.
    """
    assert adjusted_in("16_9", tmp_path) == frozenset()


# --- 두 기계 출처를 합치기 ----------------------------------------------
#
# Data Dragon 은 스탯·쿨다운을, cdragon 은 피해량·계수를 본다. **서로 다른
# 것을 보므로 한쪽이 다른 쪽을 이기게 하면 안 된다.**


@pytest.mark.parametrize(
    ("stats", "values", "want"),
    [
        (None, None, None),
        ("nerf", None, "nerf"),
        (None, "buff", "buff"),
        ("nerf", "nerf", "nerf"),
        ("buff", "buff", "buff"),
        ("buff", "nerf", "mixed"),
        ("nerf", "buff", "mixed"),
    ],
)
def test_merge_directions(
    stats: str | None, values: str | None, want: str | None
) -> None:
    assert merge_directions(stats, values) == want  # type: ignore[arg-type]


def test_opposite_sources_never_let_one_win() -> None:
    """쿨다운을 줄이면서(버프) 피해량도 줄였으면(너프) 실제로는 `mixed` 다.

    손 라벨이 정확히 그렇게 붙고, `groundtruth.compare` 가 그것을 `extends`
    로 받는다.
    """
    assert merge_directions("buff", "nerf") == merge_directions("nerf", "buff")


# --- ② 방향의 정답 ------------------------------------------------------


def champion(hp: int, name: str = "Ahri") -> dict:
    return {
        "id": name,
        "name": name,
        "tags": ["Mage"],
        "stats": {"hp": hp},
        "spells": [],
    }


# **곁들이 챔피언을 넣는 이유가 있다.** `drop_mass_changes` 는 「전 챔피언이
# 한꺼번에 바뀐 필드」를 걷어내는데, 풀에 한 종뿐이면 그 한 종의 변경이 곧
# 100% 라 통째로 걸러진다. 16.5 의 `attackdamageperlevel` 을 잡으려고 넣은
# 장치이고, 여기서는 풀을 넉넉히 둬서 피한다.
QUIET = 6


def ddragon(tmp: Path, patch: str, hp: int) -> None:
    data = {"Ahri": champion(hp, "Ahri")}
    for i in range(QUIET):
        data[f"Q{i}"] = champion(500, f"Q{i}")
    (tmp / f"{version(patch)}.json").write_text(
        json.dumps({"data": data}), encoding="utf-8"
    )


def setup(tmp_path: Path, *, hp_before: int = 580, hp_after: int = 600) -> dict:
    dd, notes, labels, cd = (tmp_path / n for n in ("dd", "notes", "labels", "cd"))
    for d in (dd, notes, labels, cd):
        d.mkdir()
    ddragon(dd, "13_14", hp_before)
    ddragon(dd, "13_15", hp_after)
    note(notes, "13_14")  # 직전 패치 노트는 비워 둔다
    return {"ddragon": dd, "cdragon": cd, "notes": notes, "labels": labels}


def test_data_dragon_direction_is_used_when_no_hand_label(tmp_path: Path) -> None:
    where = setup(tmp_path)

    got = directions_in("13_15", "13_14", **where)

    assert got["Ahri"] == ("buff", "auto")


def test_a_hand_label_beats_the_machine(tmp_path: Path) -> None:
    """**손 라벨이 마지막에 덮는다.** 순서가 뒤집히면 기계가 이긴다."""
    where = setup(tmp_path)
    (where["labels"] / "13_15.jsonl").write_text(
        json.dumps(
            {
                "patch": "13_15",
                "champion": "Ahri",
                "direction": "mixed",
                "evidence": ["체력은 올랐지만 계수가 내렸다"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    got = directions_in("13_15", "13_14", **where)

    assert got["Ahri"] == ("mixed", "label")


def test_the_previous_patch_note_excludes_a_champion(tmp_path: Path) -> None:
    """**스냅샷은 노트보다 늦게 움직인다.**

    직전 패치의 조정이 이 diff 에 처음 나타나므로, 그대로 두면 직전 패치 것이
    이 패치 것으로 적힌다. Corki 13.24 → 14.1 이 그렇게 부딪혔다.
    """
    where = setup(tmp_path)
    note(where["notes"], "13_14", "Ahri")  # 직전 패치가 이미 Ahri 를 적었다

    got = directions_in("13_15", "13_14", **where)

    assert "Ahri" not in got


def test_no_change_means_no_entry(tmp_path: Path) -> None:
    """**모르는 것을 라벨로 만들지 않는다.**"""
    where = setup(tmp_path, hp_before=580, hp_after=580)

    assert directions_in("13_15", "13_14", **where) == {}
