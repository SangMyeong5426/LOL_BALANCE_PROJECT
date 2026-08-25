"""조정의 방향 — 너프인가 버프인가.

**Data Dragon diff 는 방향을 모른다.** 「Zac E 쿨다운 22 → 21」이 바뀐 사실만
준다. 그런데 **필드를 알면 방향은 기계적으로 정해진다** — 쿨다운은 줄면 버프,
체력은 줄면 너프다.

그래서 diff 가 닿는 범위에서는 방향 라벨이 **자동으로** 만들어진다. 이것이
두 가지로 쓰인다.

1. 손으로 붙인 라벨을 채점하는 기준
2. 그만큼은 손으로 붙일 필요가 없다

닿지 않는 범위가 남는다. 대부분 스킬의 피해량이 Data Dragon 에 없어서
(`ddragon-format.md`) **피해량만 바꾼 조정은 여기서 안 잡힌다.** 그것이 노트를
읽어야 하는 몫이다.
"""

from __future__ import annotations

import re
from typing import Literal

from lol_balance.ddragon import Change

Direction = Literal["nerf", "buff", "mixed"]

# 값이 **오르면** 버프인가 너프인가. 실제 diff 에 나온 필드만 적는다 —
# 추측으로 채우지 않는다. 모르는 필드는 채점하지 않고 넘어간다.
#
# 기본 스탯은 전부 「높을수록 강하다」다. 공격속도(`attackspeed`)도 초당 타수라
# 높을수록 빠르다 — 노트가 0.85 → 0.67 을 "reduced" 로 쓰는 것과 맞는다.
_BUFF_WHEN_UP = frozenset(
    {
        "hp",
        "hpperlevel",
        "hpregen",
        "hpregenperlevel",
        "mp",
        "mpperlevel",
        "mpregen",
        "mpregenperlevel",
        "armor",
        "armorperlevel",
        "spellblock",
        "spellblockperlevel",
        "attackdamage",
        "attackdamageperlevel",
        "attackspeed",
        "attackspeedperlevel",
        "attackrange",
        "movespeed",
        "crit",
        "critperlevel",
    }
)
# 스킬 쪽은 자원과 대기시간이 반대다.
_BUFF_WHEN_DOWN = frozenset({"cooldown", "cost"})
_BUFF_WHEN_UP_SPELL = frozenset({"range"})

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _values(raw: object) -> tuple[float, ...]:
    return tuple(float(m) for m in _NUMBER.findall(str(raw)))


def value_shift(before: object, after: object) -> Literal["up", "down"] | None:
    """값이 올랐나 내렸나.

    레벨별 배열이라 원소별로 본다. **같은 원소는 무시하고**, 남은 것이 모두
    한 방향이면 그 방향이다. 섞이면 `None` — 「저레벨 너프 · 고레벨 버프」
    같은 조정이 실제로 있고, 그것을 한 방향으로 뭉개면 안 된다.

    길이가 다르면 한쪽이 스칼라일 때만 펴서 맞춘다(`11 → 11/10.5/10/9.5/9`).
    둘 다 배열인데 길이가 다르면 비교하지 않는다.
    """
    a, b = _values(before), _values(after)
    if not a or not b:
        return None
    if len(a) != len(b):
        if len(a) == 1:
            a = a * len(b)
        elif len(b) == 1:
            b = b * len(a)
        else:
            return None
    ups = sum(1 for x, y in zip(a, b, strict=True) if y > x)
    downs = sum(1 for x, y in zip(a, b, strict=True) if y < x)
    if ups and downs:
        return None
    if ups:
        return "up"
    if downs:
        return "down"
    return None


def change_direction(change: Change) -> Literal["nerf", "buff"] | None:
    """변경 하나의 방향. 극성을 모르는 필드는 `None`."""
    shift = value_shift(change.before, change.after)
    if shift is None:
        return None

    if change.kind == "stat":
        if change.field not in _BUFF_WHEN_UP:
            return None
        return "buff" if shift == "up" else "nerf"

    if change.kind == "spell":
        _, _, name = change.field.partition(".")
        if name in _BUFF_WHEN_DOWN:
            return "nerf" if shift == "up" else "buff"
        if name in _BUFF_WHEN_UP_SPELL:
            return "buff" if shift == "up" else "nerf"
    return None


def champion_direction(changes: tuple[Change, ...] | list[Change]) -> Direction | None:
    """한 챔피언의 한 패치 방향.

    너프와 버프가 함께 오면 `mixed` 다. **한쪽으로 뭉개지 않는다** — 라이엇이
    한 챔피언을 조정하면서 어떤 것은 올리고 어떤 것은 내리는 일이 흔하다.
    채점 가능한 변경이 하나도 없으면 `None`.
    """
    seen = {d for d in (change_direction(c) for c in changes) if d is not None}
    if not seen:
        return None
    if len(seen) == 2:
        return "mixed"
    return "nerf" if "nerf" in seen else "buff"
