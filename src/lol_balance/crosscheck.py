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
from dataclasses import dataclass

from lol_balance.ddragon import Change
from lol_balance.extract import ChangeRecord

# Data Dragon 이 완전한 종류만 채점한다. `tooltip` 은 대부분 변수명 정리이고
# `effect` 는 스킬 932개 중 306개만 값이 살아 있다 — 채점 기준이 못 된다.
SCORED_KINDS = ("stat", "spell")

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
