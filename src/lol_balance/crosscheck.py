"""추출 결과를 Data Dragon diff 로 채점한다.

**이 프로젝트에서 LLM 출력이 자동으로 채점되는 유일한 자리다.** 스탯·쿨다운·
코스트·사거리 네 종은 Data Dragon 에 완전하게 들어 있으므로(`ddragon-format.md`),
LLM 이 노트에서 뽑은 값과 기계적으로 대조할 수 있다.

**필드 이름을 맞추지 않는다.** 「Base health」와 `hp`, 「Cooldown」과 `Q.cooldown`
을 이어 붙이는 사전을 만들면 그 사전이 또 하나의 오류원이 된다. 대신 **바뀐
숫자 자체**로 맞춘다 — 같은 챔피언에서 330 → 335 가 양쪽에 다 있으면 같은 변경이다.

세 갈래로 나뉜다.

    맞음        양쪽에 다 있다
    놓침        Data Dragon 에 있는데 추출에 없다        ← 추출 품질 지표
    대조 불가   추출에만 있다                            ← 피해량이 대부분. 아래 참조

**「대조 불가」를 환각으로 세면 안 된다.** 대부분 스킬의 피해량이 Data Dragon 에
아예 없어서(툴팁이 `{{ totaldamage }}` 플레이스홀더) 노트에만 있는 것이 정상이다.
그것을 뽑으라고 LLM 을 쓰는 것이다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from lol_balance.ddragon import Change
from lol_balance.extract import ChangeRecord


class Changed(Protocol):
    """「무엇이 몇에서 몇으로」를 말하는 것.

    `ddragon.Change` 와 `cdragon.Change` 를 한 자리에서 받기 위한 것이다. 둘은
    모양이 같지만 다른 클래스라, **구조로 받지 않으면 한쪽만 쓸 수 있다.**
    """

    @property
    def champion(self) -> str: ...

    @property
    def kind(self) -> str: ...

    @property
    def field(self) -> str: ...

    @property
    def before(self) -> Any: ...

    @property
    def after(self) -> Any: ...


# 채점에 쓰는 종류.
#
# `stat` 과 `spell`(쿨다운·코스트·사거리)은 Data Dragon 에 **완전하다** — 여기서
# 안 잡히면 놓친 것이 맞다.
#
# `effect` 는 **절반만 살아 있다**(13.15 기준 스킬 656개 중 322개). 그래서
# 「안 잡혔으니 놓쳤다」로는 못 쓰지만, **잡힌 것은 진짜 피해량 변경이다.**
# 빼 두면 대조 가능한 범위가 부당하게 좁아진다 — 13.15 에서 이것만으로 3건이
# 더 확인됐다.
#
# `tooltip` 은 뺀다. 대부분 `{{ e1 }}` → `{{ stackduration }}` 같은 변수명
# 정리라 수치 변경이 아니다.
SCORED_KINDS = ("stat", "spell", "effect")

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def numbers(value: object) -> tuple[float, ...]:
    """어떤 표기에서든 숫자만 뽑아 정규화한다.

    Data Dragon 은 `330` 이나 `"22/19/16/13/10"` 으로 주고, 노트는
    `"15 / 45 / 75 / 105 / 135"` 나 `"60% AD"` 로 쓴다. 숫자열만 남기면 같아진다.
    """
    return tuple(float(m) for m in _NUMBER.findall(str(value)))


@dataclass(frozen=True)
class CrossCheck:
    """한 패치의 대조 결과."""

    matched: tuple[tuple[Change, ChangeRecord], ...]
    missed: tuple[Change, ...]
    unverifiable: tuple[ChangeRecord, ...]

    @property
    def scored(self) -> int:
        """채점 대상 수 — Data Dragon 이 확실히 아는 변경."""
        return len(self.matched) + len(self.missed)

    @property
    def recall(self) -> float:
        """Data Dragon 이 아는 변경 중 추출이 잡아낸 비율.

        **이것이 추출 품질 지표다.** 분모가 0이면 정의되지 않으므로 호출 전에
        `scored` 를 본다.
        """
        return len(self.matched) / self.scored


def cross_check(
    changes: tuple[Change, ...] | list[Change],
    records: tuple[ChangeRecord, ...] | list[ChangeRecord],
    champion_names: dict[str, str],
) -> CrossCheck:
    """Data Dragon 변경과 추출 기록을 맞춘다.

    `champion_names` 는 Data Dragon 의 id → 표시 이름 사전이다(`Belveth` →
    `Bel'Veth`). 위키는 표시 이름을 쓰고 diff 는 id 를 쓰므로 여기서 잇는다.
    **이름 표기를 믿지 않는다**는 규칙이 여기에도 적용된다.
    """
    pool: dict[
        tuple[str, tuple[float, ...], tuple[float, ...]], list[ChangeRecord]
    ] = {}
    for record in records:
        key = (record.champion, numbers(record.before), numbers(record.after))
        pool.setdefault(key, []).append(record)

    matched: list[tuple[Change, ChangeRecord]] = []
    missed: list[Change] = []
    used: set[int] = set()

    for change in changes:
        if change.kind not in SCORED_KINDS:
            continue
        name = champion_names.get(change.champion, change.champion)
        key = (name, numbers(change.before), numbers(change.after))
        candidates = [r for r in pool.get(key, []) if id(r) not in used]
        if candidates:
            used.add(id(candidates[0]))
            matched.append((change, candidates[0]))
        else:
            missed.append(change)

    unverifiable = tuple(r for r in records if id(r) not in used)
    return CrossCheck(tuple(matched), tuple(missed), unverifiable)


# ── CommunityDragon 으로 「대조 불가」를 줄인다 ──────────────────────────
#
# **cdragon 변경을 `cross_check` 에 그냥 넣으면 안 된다.** 그러면 「놓침」이
# 폭발한다 — cdragon 은 노트에 한 줄도 없는 내부 값 변경까지 전부 잡는데,
# 그것을 추출 실패로 세면 재현율이 무너진다. 라이엇이 안 적은 것을 뽑지
# 못했다고 벌점을 주는 셈이다.
#
# 그래서 **2차 통과**로 붙인다. Data Dragon 채점은 그대로 두고, 거기서
# 「대조 불가」로 남은 기록만 cdragon 과 맞춰 본다. 재현율의 분모는 안 바뀐다.

# 비율 표기 차이를 흡수할 배율.
#
# cdragon 은 `0.675` 로, 패치 노트는 `67.5% AD` 로 쓴다. 같은 값이다.
# **필드 이름을 맞추지 않는다는 원칙은 그대로다** — 배율만 둘 다 시도한다.
SCALES = (1.0, 100.0)

# 맞출 때 쓰는 자릿수.
#
# **diff 는 반올림하지 않는다**(`cdragon.py`). 여기는 다른 일이다 — 저장된
# float32 값을 사람이 쓴 문장과 맞춰야 한다.
#
#     0.6000000238418579 × 100 = 60.00000238418579
#
# float32 오차가 ×100 되면서 소수 6자리를 넘는다. 6자리로 자르면 `60.000002`
# 가 되어 노트의 `60` 과 안 맞는다. **실제로 이것 때문에 일치가 6/41 로
# 떨어졌다.** 노트가 쓰는 가장 잘은 자리가 3자리(`84.375`)라 거기에 맞춘다.
MATCH_DIGITS = 3


def _scaled(row: tuple[float, ...]) -> tuple[tuple[float, ...], ...]:
    """한 값에서 나올 수 있는 표기들. 중복은 없앤다."""
    out: list[tuple[float, ...]] = []
    for scale in SCALES:
        key = tuple(round(v * scale, MATCH_DIGITS) for v in row)
        if key not in out:
            out.append(key)
    return tuple(out)


def _rounded(row: tuple[float, ...]) -> tuple[float, ...]:
    """양쪽을 같은 자리로 맞춘다. 한쪽만 반올림하면 그것 때문에 어긋난다."""
    return tuple(round(v, MATCH_DIGITS) for v in row)


@dataclass(frozen=True)
class ValueCheck:
    """cdragon 2차 통과 결과."""

    confirmed: tuple[tuple[Changed, ChangeRecord], ...]
    unverifiable: tuple[ChangeRecord, ...]

    @property
    def rate(self) -> float:
        """2차 통과가 확인한 비율. 분모가 0이면 부르지 않는다."""
        total = len(self.confirmed) + len(self.unverifiable)
        return len(self.confirmed) / total if total else 0.0


def verify_values(
    records: Sequence[ChangeRecord],
    changes: Sequence[Changed],
    champion_names: dict[str, str] | None = None,
) -> ValueCheck:
    """「대조 불가」 기록을 cdragon 변경으로 확인한다.

    `changes` 는 `cdragon.field_changes` 가 낸 **필드 단위** 변경이다. 칸별
    변경을 그대로 넣으면 안 된다 — 노트는 다섯 랭크를 한 문장으로 쓴다.

    **다 맞출 수는 없다.** 노트가 파생값을 쓰는 경우가 있다 — Aatrox Q 는
    `QTotalADRatio` 하나에서 「first cast」·「sweetspot」·「maximum」 일곱 문장이
    나온다. 저장된 것은 하나뿐이라 나머지는 원리적으로 안 맞는다.
    """
    names = champion_names or {}
    pool: dict[tuple[str, tuple[float, ...], tuple[float, ...]], list[Changed]] = {}
    for change in changes:
        name = names.get(change.champion, change.champion)
        for before in _scaled(numbers(change.before)):
            for after in _scaled(numbers(change.after)):
                pool.setdefault((name, before, after), []).append(change)

    confirmed: list[tuple[Changed, ChangeRecord]] = []
    left: list[ChangeRecord] = []
    used: set[int] = set()
    for record in records:
        key = (
            record.champion,
            _rounded(numbers(record.before)),
            _rounded(numbers(record.after)),
        )
        found = [c for c in pool.get(key, []) if id(c) not in used]
        if found:
            used.add(id(found[0]))
            confirmed.append((found[0], record))
        else:
            left.append(record)
    return ValueCheck(tuple(confirmed), tuple(left))
