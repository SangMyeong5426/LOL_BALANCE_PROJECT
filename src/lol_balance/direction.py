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
from collections.abc import Sequence
from typing import Any, Literal, Protocol

from lol_balance.ddragon import Change


class ValueChanged(Protocol):
    """cdragon 변경 하나. **구조로 받는다** — `cdragon.Change` 는 다른 클래스다."""

    @property
    def field(self) -> str: ...

    @property
    def before(self) -> Any: ...

    @property
    def after(self) -> Any: ...


# `adjust` 는 **조정은 됐는데 강해진 것도 약해진 것도 아닌 경우**다. 버그 수정,
# 조작감 변경, 판정 로직 손질이 여기 들어간다. 노트에 실리지만 승률을 어느
# 방향으로 밀지 알 수 없으므로 너프·버프와 섞으면 안 된다.
Direction = Literal["nerf", "buff", "mixed", "adjust"]

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

# 한 필드가 전 챔피언의 이만큼을 한꺼번에 바꾸면 밸런스 조정이 아니다.
MASS_CHANGE_SHARE = 0.5


def drop_mass_changes(
    changes: tuple[Change, ...] | list[Change],
    champion_count: int,
    share: float = MASS_CHANGE_SHARE,
) -> tuple[Change, ...]:
    """전 챔피언이 한꺼번에 바뀐 필드를 걷어낸다.

    **밸런스 조정으로 세면 안 된다.** 16.5 에서 `attackdamageperlevel` 이
    171종 전부 5 → 0 이 됐고 16.15 까지 0 으로 남아 있다. 스키마가 바뀐 것이지
    라이엇이 전 챔피언을 너프한 것이 아니다.

    걸러내지 않으면 그 패치의 방향 라벨이 통째로 `nerf` 가 된다. 실제로
    노트에 버그 수정 한 줄뿐인 챔피언까지 너프로 잡혔다.
    """
    if champion_count <= 0:
        return tuple(changes)
    counts: dict[tuple[str, str], int] = {}
    for change in changes:
        key = (change.kind, change.field)
        counts[key] = counts.get(key, 0) + 1
    limit = champion_count * share
    return tuple(c for c in changes if counts[(c.kind, c.field)] <= limit)


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


# ── CommunityDragon 값의 방향 ────────────────────────────────────────────
#
# **Data Dragon 과 방식이 다르다.** 저쪽은 필드가 `hp`·`cooldown` 처럼 정해져
# 있어 정확한 이름 목록으로 잡히는데, cdragon 은 **이름이 챔피언마다 다르다** —
# 13.15 한 패치에 `mDataValues` 항목 이름이 **1,044종**이다(`QBaseDamage` ·
# `RAPCoefficient` · `ExplosionBaseDamage` …). 목록으로는 못 덮는다.
#
# 그래서 **이름에 든 낱말**로 잡는다. 정확도를 위해 세 가지를 지킨다.
#
# 1. 애매한 것을 **먼저** 걸러 판정하지 않는다
# 2. `self-` 가 붙으면 뜻이 뒤집히므로 너프 쪽을 먼저 본다
# 3. 모르는 이름은 **판정하지 않는다.** 틀린 라벨은 없느니만 못하다
#
# 손 라벨 241개에 붙여 **충돌 0** 을 확인하고 채택했다. 판정이 붙는 것은
# 노트에 이름 오른 챔피언의 13.7% 이고, Data Dragon 의 25.5% 와 합쳐 39.2% 다.

# 뜻이 뒤집히거나 크기로 못 읽는 것. **가장 먼저 본다.**
#
# `MonsterDamageCap` 이 그렇다 — 이름에 `damage` 가 있지만 상한이라 신설되면
# 너프다. 「크면 강하다」가 성립하지 않는다.
_VALUE_AMBIGUOUS = (
    "cap",
    "threshold",
    "minimum",
    "maximum",
    "delay",
    "windup",
)

# 오르면 너프. **버프 쪽보다 먼저 본다** — `SelfSlowDuration` 은 `slow` 를
# 갖지만 자기가 느려지는 것이라 반대다.
_VALUE_NERF_UP = (
    "cooldown",
    "cost",
    "recharge",
    "selfslow",
    "selfroot",
    "selfstun",
    "selfdamage",
)

# 오르면 버프.
_VALUE_BUFF_UP = (
    "damage",
    "ratio",
    "heal",
    "lifesteal",
    "omnivamp",
    "shield",
    "slow",
    "stun",
    "root",
    "snare",
    "knockup",
    "knockback",
    "airborne",
    "silence",
    "taunt",
    "charm",
    "fear",
    "flee",
    "suppress",
    "grounded",
    "movementspeed",
    "movespeed",
    "haste",
    "attackspeed",
    "radius",
    "castrange",
    "missilespeed",
    "dashspeed",
    "armorpen",
    "magicpen",
    "shred",
    "resist",
    "armor",
    "health",
    "maxstacks",
    "duration",
)


def value_polarity(name: str) -> int | None:
    """`+1` = 오르면 버프, `-1` = 오르면 너프, `None` = 모른다."""
    low = name.lower()
    for token in _VALUE_AMBIGUOUS:
        if token in low:
            return None
    for token in _VALUE_NERF_UP:
        if token in low:
            return -1
    for token in _VALUE_BUFF_UP:
        if token in low:
            return 1
    return None


def _comparable(before: object, after: object) -> bool:
    """크기로 비교해도 되는가.

    **길이가 다르면 안 된다.** Jax 15.22 가 여기서 걸렸다 — 공식 부품이
    `(0.7, 2.0) → (2.0,)` 로 하나 사라졌는데 평균이 1.35 → 2.0 이라 「올랐다」로
    읽혔다. 실제로는 몬스터 피해량 상한 신설이라 너프였고, 손 라벨과 충돌한
    유일한 건이었다. **구조가 바뀐 것은 크기 비교로 못 읽는다.**
    """
    if isinstance(before, tuple) and isinstance(after, tuple):
        return len(before) == len(after)
    return isinstance(before, (int, float)) and isinstance(after, (int, float))


def _magnitude(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, tuple) and value:
        return sum(value) / len(value)
    return None


def value_direction(changes: Sequence[ValueChanged]) -> Direction | None:
    """cdragon 변경 묶음의 방향. **모르면 `None` 이다.**

    `champion_direction` 과 같은 규칙으로 센다 — 강해진 항목과 약해진 항목이
    같이 있으면 `mixed`, 한쪽뿐이면 그 방향, 셀 것이 없으면 판정하지 않는다.
    """
    up = down = 0
    for change in changes:
        polarity = value_polarity(change.field.split(".", 1)[-1])
        if polarity is None or not _comparable(change.before, change.after):
            continue
        a, b = _magnitude(change.before), _magnitude(change.after)
        if a is None or b is None or a == b:
            continue
        if (b > a) == (polarity > 0):
            up += 1
        else:
            down += 1
    if up and down:
        return "mixed"
    if up:
        return "buff"
    if down:
        return "nerf"
    return None
