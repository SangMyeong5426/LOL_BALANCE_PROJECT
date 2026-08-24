"""Data Dragon 버전 간 수치 변경 추출.

버전 스냅샷 두 개를 비교해 **무엇이 몇에서 몇으로 바뀌었는지** 뽑는다.

**이것만으로는 정답지가 못 된다.** 대부분 스킬의 피해량이 Data Dragon 에 없다 —
툴팁이 `{{ totaldamage }}` 같은 플레이스홀더이고 실제 값은 게임 클라이언트에만
있다. 근거와 한계는 `docs/spec/ddragon-format.md` 에 있다.

여기서 뽑는 것은 **기본 스탯·쿨다운·코스트·사거리** 다. 이 넷은 완전하고 정확하다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# 툴팁의 `{{ 변수 }}` 와 `<태그>`. 값이 아니라 표시 형식이라 비교에서 걷어낸다.
_PLACEHOLDER = re.compile(r"\{\{.*?\}\}")
_TAG = re.compile(r"<[^>]+>")

_SPELL_SLOTS = ("Q", "W", "E", "R")

# 게임 모드 변형은 정식 챔피언이 아니다. 걸러내지 않으면 한 버전에서 60종이
# "신규 챔피언"으로 잡힌다 — 실제로 16.15.1 의 `Jade_*` 가 그랬다.
# 정식 챔피언 id 에는 밑줄이 없고(`Ahri`, `MonkeyKing`, `DrMundo`),
# 변형은 `Jade_Ahri` 처럼 접두사가 붙으며 key 도 원본 + 60000 이다.
_VARIANT_KEY_FLOOR = 10000

# 레벨별 값을 문자열로 합쳐 둔 필드. 배열보다 비교가 안정적이다.
_SPELL_BURN_FIELDS = {
    "cooldownBurn": "cooldown",
    "costBurn": "cost",
    "rangeBurn": "range",
}


@dataclass(frozen=True)
class Change:
    """수치 하나가 바뀐 기록."""

    champion: str
    kind: str
    field: str
    before: Any
    after: Any

    def __str__(self) -> str:
        return f"{self.champion} {self.field}: {self.before} → {self.after}"


def _tooltip_literals(text: str | None) -> list[str]:
    """툴팁에서 **플레이스홀더와 태그를 걷어낸 뒤 남는 진짜 숫자.**

    이걸 안 하면 `{{ e1 }}` → `{{ stackduration }}` 같은 **변수명 정리**가 수치
    변경으로 잡힌다. 실제로 한 패치의 툴팁 변경 32건 중 30건이 그것이었다.
    """
    stripped = _TAG.sub(" ", _PLACEHOLDER.sub(" ", text or ""))
    return re.findall(r"\d+(?:\.\d+)?", stripped)


def diff_champion(
    name: str, before: dict[str, Any], after: dict[str, Any]
) -> list[Change]:
    """챔피언 한 종의 변경을 뽑는다."""
    changes: list[Change] = []

    for stat, old in before.get("stats", {}).items():
        new = after.get("stats", {}).get(stat)
        if new != old:
            changes.append(Change(name, "stat", stat, old, new))

    # strict=False 다. 리워크로 스킬 수가 달라질 수 있고, 그때는 겹치는 만큼만
    # 비교하는 것이 맞다. 스킬 수 변화 자체는 아래에서 따로 기록한다.
    spells_a, spells_b = before.get("spells", []), after.get("spells", [])
    if len(spells_a) != len(spells_b):
        changes.append(Change(name, "spell", "count", len(spells_a), len(spells_b)))

    for slot, sa, sb in zip(_SPELL_SLOTS, spells_a, spells_b, strict=False):
        for raw, label in _SPELL_BURN_FIELDS.items():
            old, new = sa.get(raw), sb.get(raw)
            if old != new:
                changes.append(Change(name, "spell", f"{slot}.{label}", old, new))

        old_lit, new_lit = (
            _tooltip_literals(sa.get("tooltip")),
            _tooltip_literals(sb.get("tooltip")),
        )
        if old_lit != new_lit:
            changes.append(Change(name, "tooltip", f"{slot}.numbers", old_lit, new_lit))

        if sa.get("effectBurn") != sb.get("effectBurn"):
            changes.append(
                Change(
                    name,
                    "effect",
                    f"{slot}.effect",
                    sa.get("effectBurn"),
                    sb.get("effectBurn"),
                )
            )

    return changes


def is_standard_champion(champion_id: str, entry: dict[str, Any]) -> bool:
    """정식 챔피언인지. 게임 모드 변형을 걸러낸다."""
    if "_" in champion_id:
        return False
    key = entry.get("key", "")
    return not (key.isdigit() and int(key) >= _VARIANT_KEY_FLOOR)


def standard_champions(data: dict[str, Any]) -> dict[str, Any]:
    """`championFull.json` 의 `data` 에서 정식 챔피언만 남긴다."""
    return {k: v for k, v in data.items() if is_standard_champion(k, v)}


def diff_versions(before: dict[str, Any], after: dict[str, Any]) -> list[Change]:
    """두 버전의 `championFull.json` `data` 를 비교한다.

    양쪽에 다 있는 챔피언만 본다. 신규 출시와 삭제는 「변경」이 아니라 별개
    사건이라 여기서 다루지 않는다.
    """
    a, b = standard_champions(before), standard_champions(after)
    changes: list[Change] = []
    for name in sorted(set(a) & set(b)):
        changes.extend(diff_champion(name, a[name], b[name]))
    return changes


def added_champions(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """새로 나온 챔피언. 출시 직후는 조정 빈도가 높아 따로 다뤄야 한다."""
    return sorted(set(standard_champions(after)) - set(standard_champions(before)))
