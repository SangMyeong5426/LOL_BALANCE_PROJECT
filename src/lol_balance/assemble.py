"""패널의 **정답 라벨을 조립한다** — 어느 출처가 무엇을 이기는가.

여기 있는 판정 넷이 정답지를 만든다. 한때 `scripts/build-panel` 안에 있었는데
**스크립트는 테스트가 안 붙어서** 옮겼다. 정답지 조립은 조용히 틀리면 모든
성적이 같이 틀리는 자리다.

    adjusted_in         ① 대상의 정답 — 그 패치 노트에 이름이 올랐나
    merge_directions    Data Dragon 과 cdragon 이 반대를 말할 때
    directions_in       ② 방향의 정답 — 손 라벨이 기계 판정을 이긴다
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lol_balance.cdragon import field_changes, read_champion
from lol_balance.ddragon import diff_versions, standard_champions
from lol_balance.direction import (
    Direction,
    champion_direction,
    drop_mass_changes,
    value_direction,
)
from lol_balance.groundtruth import read_labels
from lol_balance.patchnotes import champion_changes, changed_champions


def version(patch: str) -> str:
    """`16_9` → `16.9.1`. Data Dragon 과 노트 파일명이 이 형태다."""
    return patch.replace("_", ".") + ".1"


def version_short(patch: str) -> str:
    """`13_15` → `13.15`. cdragon 은 빌드 번호를 안 쓴다."""
    return patch.replace("_", ".")


def adjusted_in(patch: str, notes: Path) -> frozenset[str]:
    """그 패치 노트에 나온 챔피언 이름. **① 대상의 정답이다.**

    노트가 없으면 빈 집합이다. 0 으로 채우는 것과 다르다 — 그 패치는 예측
    지점이 안 되고, 조용히 「아무도 조정 안 됐다」가 되지 않는다.
    """
    path = notes / f"{version(patch)}.html"
    if not path.exists():
        return frozenset()
    return frozenset(b.champion for b in champion_changes(path.read_bytes()))


def merge_directions(a: Direction | None, b: Direction | None) -> Direction | None:
    """두 출처의 판정을 합친다. **반대를 말하면 그것이 `mixed` 다.**

    Data Dragon 은 스탯·쿨다운을, cdragon 은 피해량·계수를 본다. **서로 다른
    것을 보므로 한쪽이 다른 쪽을 이기게 하면 안 된다** — 쿨다운을 줄이면서
    (버프) 피해량도 줄였으면(너프) 실제로는 `mixed` 이고, 손 라벨이 정확히
    그렇게 붙는다(`groundtruth.compare` 의 `extends`).
    """
    if a is None:
        return b
    if b is None:
        return a
    return a if a == b else "mixed"


def _source(from_stats: Direction | None, from_values: Direction | None) -> str:
    if from_stats and from_values:
        return "both"
    return "auto" if from_stats else "value"


def directions_in(
    patch: str,
    previous: str,
    *,
    ddragon: Path,
    cdragon: Path,
    notes: Path,
    labels: Path,
) -> dict[str, tuple[Direction, str]]:
    """그 패치의 챔피언별 방향. **손 라벨이 기계 판정을 이긴다.**

    기계 판정은 두 출처를 합친 것이다.

        auto    Data Dragon — 스탯 · 쿨다운 · 코스트 · 사거리
        value   CommunityDragon — 피해량 · 계수 (ADR-0005)

    손으로 붙인 라벨은 노트 전체를 읽은 것이라 더 정확하고, 라벨이 쌓일수록
    패널이 좋아진다. 어느 쪽에서 왔는지는 `direction_source` 에 남긴다.

    **cdragon 판정은 손 라벨 241개에 붙여 충돌 0 을 확인하고 넣었다.**
    """
    after = json.loads((ddragon / f"{version(patch)}.json").read_bytes())["data"]
    before = json.loads((ddragon / f"{version(previous)}.json").read_bytes())["data"]
    standard = standard_champions(after)
    names = {k: v.get("name", k) for k, v in standard.items()}

    grouped: dict[str, list[Any]] = {}
    for change in drop_mass_changes(diff_versions(before, after), len(standard)):
        grouped.setdefault(change.champion, []).append(change)

    # **스냅샷은 노트보다 늦게 움직인다.** 직전 패치의 조정이 이 diff 에 처음
    # 나타나므로, 그대로 두면 직전 패치 것이 이 패치 것으로 적힌다. 근거는
    # `patchnotes.changed_champions`.
    late = changed_champions((notes / f"{version(previous)}.html").read_bytes())

    out: dict[str, tuple[Direction, str]] = {}
    for champion_id, entry in standard.items():
        name = names.get(champion_id, champion_id)
        if name in late:
            continue
        from_stats = champion_direction(grouped.get(champion_id, []))
        from_values = value_direction(
            field_changes(
                read_champion(cdragon, version_short(previous), champion_id.lower()),
                read_champion(cdragon, version_short(patch), champion_id.lower()),
                champion_id,
                {s["id"]: s["maxrank"] for s in entry.get("spells", [])},
            )
        )
        merged = merge_directions(from_stats, from_values)
        if merged is None:
            continue
        out[name] = (merged, _source(from_stats, from_values))

    # **손 라벨이 마지막에 덮는다.** 순서가 뒤집히면 기계 판정이 이긴다.
    for label in read_labels(labels / f"{patch}.jsonl"):
        out[label.champion] = (label.direction, "label")
    return out
