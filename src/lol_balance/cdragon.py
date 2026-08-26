"""CommunityDragon `.bin.json` 에서 스킬 수치를 읽는다.

**Data Dragon 에 없는 것을 읽는다** — 피해량과 계수. 도입 근거와 후보 비교는
[ADR-0005](../../docs/adr/0005-skill-damage-data-source.md) 에 있다.

## 숫자가 두 군데에 나뉘어 있다

    mDataValues        랭크별 배열.  QBaseDamage [-5, 10, 25, 40, 55, 70, 85]
    mSpellCalculations 공식.         QDamage = QBaseDamage + AD × QTotalADRatio

**둘 다 읽어야 한다.** 공식은 값을 이름으로 참조하지만, **계수를 공식 안에 직접
박아 두기도 한다** — 13.15 한 패치에서 `StatByCoefficientCalculationPart` 의
`mCoefficient` 가 364개다. `mDataValues` 만 읽으면 그만큼을 통째로 놓친다.

## 배열은 7칸인데 게임이 쓰는 것은 가운데다

랭크 0~6 으로 7칸인데 **양 끝은 외삽용**이다. 5랭크 스킬이면 `[1:6]` 이 실제
랭크 1~5 다. 실측으로 확인했다 — 13.15 Aatrox Q 가 그 자리에서 패치 노트와
정확히 맞는다.

    QTotalADRatio  [0] 0.5   → 0.525   외삽
                   [1] 0.6   → 0.6     랭크1  노트: 60% 그대로
                   [2] 0.7   → 0.675   랭크2  노트: 70% → 67.5%
                   ...
                   [6] 1.1   → 0.975   외삽

**그래도 diff 는 7칸을 다 본다.** 외삽 칸도 같이 움직이므로 잘라 내면 정보가
줄기만 한다. 자르는 것은 사람에게 보여 줄 때뿐이고 `ranks()` 가 그 일을 한다.

## 반올림하지 않는다

값이 안 바뀌면 직렬화가 비트 단위로 같다. 13.14↔13.15 32종을 소수 3자리부터
10자리까지 다섯 가지로 비교했는데 **전부 429개로 같았다.** 부동소수 잡음이
없으므로 보정이 없다 — 보정을 넣으면 진짜 미세 변경을 지우게 된다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 챔피언 하나의 `.bin.json` 에서 스킬 노드를 알아보는 표시.
SPELL_MARKER = "mSpell"

# 랭크 배열의 칸 수. 0~6.
RANK_SLOTS = 7

# `mSpell` 에서 바로 읽는 항목.
#
# `cooldownTime` 은 **Data Dragon 과 겹친다** — ADR-0005 가 정한 대조 지점이다.
# 커뮤니티 미러를 그대로 믿지 않으려면 겹치는 데가 있어야 하고, 10패치 1,971개를
# 맞춰 보니 **100% 일치했다**(`scripts/check-cdragon`).
#
# **`castRange` 는 Data Dragon 의 `range` 와 다른 값이다.** 표시 사거리가 아니라
# 내부 타게팅 값이라 자기 강화 스킬이 `25000` 으로 온다. 같은 대조에서 64.8%
# 밖에 안 맞았다. **대조에 쓰면 안 되지만 값 자체는 뽑아 둔다** — 바뀌면 실제
# 변경일 수 있다.
SPELL_FIELDS = ("cooldownTime", "castRange")

Kind = str  # "value" | "formula" | "spell"


@dataclass(frozen=True)
class Change:
    """한 챔피언의 값 하나가 바뀐 것.

    `ddragon.Change` 와 같은 모양이다 — 두 출처의 변경을 한 자리에서 다루려면
    구조가 같아야 한다.
    """

    champion: str
    kind: Kind
    field: str
    before: float | None
    after: float | None


def read_champion(root: Path, version: str, champion: str) -> dict[str, Any] | None:
    """`data/cdragon/{version}/{champion}.json` 을 읽는다.

    **없으면 `None` 이다. 그것이 실패라는 뜻은 아니다** — 그 버전에 아직 없던
    챔피언이면 파일이 없는 것이 맞다. 수집에서 7종이 그랬다(briar · hwei ·
    smolder · aurora · mel · yunara · locke). 부르는 쪽이 갈라서 봐야 한다.
    """
    path = root / version / f"{champion}.json"
    if not path.exists():
        return None
    loaded: dict[str, Any] = json.loads(path.read_bytes())
    return loaded


def spells(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """스킬 노드만 골라낸다. 키는 마지막 조각(`.../AatroxQ` → `AatroxQ`)."""
    out: dict[str, dict[str, Any]] = {}
    for key, node in data.items():
        if isinstance(node, dict) and SPELL_MARKER in node:
            spell = node[SPELL_MARKER]
            if isinstance(spell, dict):
                out[key.rsplit("/", 1)[-1]] = spell
    return out


def _numbers(node: Any, path: str, into: dict[str, float]) -> None:
    """중첩 구조 안의 숫자를 경로와 함께 전부 긁는다.

    `__type` 은 건너뛴다 — 값이 아니라 이름표다. `bool` 도 건너뛴다. 파이썬에서
    `True` 는 `int` 라, 안 걸러 내면 깃발이 숫자로 섞여 들어온다.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key != "__type":
                _numbers(value, f"{path}.{key}", into)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _numbers(value, f"{path}[{index}]", into)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        into[path] = float(node)


def values(data: dict[str, Any]) -> dict[tuple[Kind, str], float]:
    """`.bin.json` 하나에서 (종류, 경로) → 숫자를 전부 뽑는다."""
    out: dict[tuple[Kind, str], float] = {}
    for name, spell in spells(data).items():
        for entry in spell.get("mDataValues") or []:
            if not isinstance(entry, dict):
                continue
            label = entry.get("mName", "?")
            for index, value in enumerate(entry.get("mValues") or []):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    out[("value", f"{name}.{label}[{index}]")] = float(value)

        found: dict[str, float] = {}
        _numbers(spell.get("mSpellCalculations") or {}, name, found)
        for path, value in found.items():
            out[("formula", path)] = value

        for field in SPELL_FIELDS:
            found = {}
            _numbers(spell.get(field), f"{name}.{field}", found)
            for path, value in found.items():
                out[("spell", path)] = value
    return out


def diff_versions(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    champion: str,
) -> tuple[Change, ...]:
    """두 버전의 같은 챔피언을 비교한다. **칸 하나씩 본다.**

    배열을 통째로 비교하면 안 된다. Data Dragon 쪽에서 이미 겪었다 — 안 바뀐
    칸이 섞여 들어와 일치율이 0% 였고, 칸별로 가르니 75% 가 됐다.

    한쪽이 `None` 이면 빈 것으로 본다. **출시·삭제를 여기서 판정하지 않는다** —
    파일이 없는 이유는 부르는 쪽이 안다.
    """
    old = values(before) if before else {}
    new = values(after) if after else {}
    out: list[Change] = []
    for key in sorted(old.keys() | new.keys()):
        a, b = old.get(key), new.get(key)
        if a != b:
            out.append(Change(champion, key[0], key[1], a, b))
    return tuple(out)


def ranks(row: Sequence[float], maxrank: int) -> tuple[float, ...]:
    """7칸에서 게임이 실제로 쓰는 칸만 잘라낸다.

    **보여 줄 때만 쓴다.** diff 는 7칸을 다 본다 — 외삽 칸도 같이 움직이므로
    잘라 내면 변경을 놓친다.
    """
    if len(row) < RANK_SLOTS:
        return tuple(row)
    return tuple(row[1 : 1 + maxrank])


def changed_share(changed: Sequence[str], total: int) -> float:
    """변경이 잡힌 챔피언의 비율.

    **스키마가 바뀐 것을 알아채기 위한 것이다.** Data Dragon 에서 16.5 의
    `attackdamageperlevel` 이 171종 전부 0 이 되어 「전 챔피언 너프」로 잡힌
    적이 있다. 여기서는 `drop_mass_changes` 를 그대로 못 쓴다 — 그것은
    `(kind, field)` 로 세는데 **cdragon 의 필드 이름은 챔피언마다 다르다**
    (`AatroxQ.QTotalADRatio[2]`). 그래서 필드가 아니라 **몇 종이 움직였는가**로
    본다. 절반을 넘으면 밸런스 패치가 아니라 형식이 바뀐 것을 의심한다.
    """
    if total <= 0:
        return 0.0
    return len(set(changed)) / total
