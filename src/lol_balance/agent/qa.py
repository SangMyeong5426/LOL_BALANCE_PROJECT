"""후속 질문의 답을 **코드가 대조한다** — [extension 3절](../../../docs/extension.md).

    ⚠  도구 결과에 없는 숫자를 썼거나, 도구가 준 적 없는 노트 블록을 인용했다
    ⚪  대조할 수 없다 — 숫자도 인용도 없거나 형식이 맞지 않는다
    ✅  대조를 통과했다. **실제로 확인한 것만 말한다**

모델에게 「맞게 썼나」를 묻지 않는다. 답에 나온 숫자와 인용을 도구 출력과 맞춰 본다.

## 무엇을 출처로 치나

    도구 출력            이 대화에서 도구가 실제로 돌려준 글
    코드가 만든 맥락      기준 패치 지표 · 통계 모델 점수 · 코드가 낸 경고
    사람의 질문          사람이 물으면서 쓴 숫자

**모델이 쓴 글은 출처가 아니다** — 앞선 답도, 해설도. 거기 있던 숫자를 다시 쓰려면
도구로 다시 확인해야 한다.

## ADR 0007 과 어떻게 다른가

ADR 0007 은 「글에서 값을 뽑는 파서」를 기각했다 — 뽑은 값을 **쓰려는** 것이었다.
여기서는 값을 쓰지 않는다. 답의 숫자가 **출처에 있는지 없는지만** 본다. 주된 길은
모델이 옮겨 적은 칸(`numbers` · `notes`)이고, 글을 훑는 것은 칸을 비워 두고
빠져나가는 것을 막는 보조다.

## 못 잡는 것

숫자는 맞는데 **뜻을 틀리게 쓴 것**은 못 잡는다 — 승률을 픽률이라고 부르거나, 부호
없이 「4.4%p 올랐다」고 쓴 것(실제로는 내렸다). 부호를 적었으면 부호까지 본다.
그래서 ✅ 는 「맞는 답」이 아니라 「숫자와 인용이 출처에 있다」는 뜻이다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from lol_balance.agent.judge import Step
from lol_balance.agent.schema import Answer

Mark = Literal["✅", "⚪", "⚠"]

# 숫자 하나. 앞이 영문 · 숫자 · 밑줄 · 점이면 이름의 일부다(`R3` · `14_7` · `qwen3.5`).
# 뒤가 밑줄 + 숫자여도 패치 이름이다(`14_7` 의 `14`).
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.,])"
    r"(?P<sign>[+\-−])?"
    r"(?P<whole>\d{1,3}(?:,\d{3})+|\d+)"
    r"(?P<frac>\.\d+)?"
    r"(?!\d|_\d)"
    r"\s?(?P<unit>%p|%|판|종|위)?"
)
_PATCH = re.compile(r"(?<![\d_])(\d{2})_(\d{1,2})(?![\d_])")
# `search_patch_notes` 가 블록마다 내는 머리 줄 — `  [14_7] Q - Piercing Darkness`
_BLOCK = re.compile(r"^\s*\[(\d{2}_\d{1,2})\]\s+(.+?)\s*$", re.M)


@dataclass(frozen=True)
class Number:
    """글에서 찾은 숫자 하나. 쉼표와 개수 단위(판 · 종 · 위)는 뗀다."""

    raw: str
    sign: str  # "+" · "-" · ""
    core: str  # 쉼표를 뺀 값 — "3183" · "49.4"
    unit: str  # "%" · "%p" · "" — `%` 와 `%p` 는 다른 것이다
    counted: bool  # 개수 단위가 붙어 있었나

    @property
    def value(self) -> Decimal:
        return Decimal(self.core)

    @property
    def measured(self) -> bool:
        """**재야 하는 숫자인가.** 단위가 붙었거나 소수거나 세 자리 이상이면 잰다.

        「두 가지」를 `2` 로 쓴 것까지 잡으면 경고가 흔해져 아무도 안 본다. 연도처럼
        보이는 네 자리 정수도 뺀다.
        """
        if self.unit or self.counted or "." in self.core:
            return True
        return self.value >= 100 and not 1900 <= self.value <= 2100

    def same(self, other: Number) -> bool:
        """같은 숫자인가. 부호를 둘 다 적었으면 부호까지, `%` · `%p` 를 적었으면 단위까지."""
        if self.value != other.value:
            return False
        if self.unit and self.unit != other.unit:
            return False
        return not (self.sign and other.sign and self.sign != other.sign)


def numbers_in(text: str) -> list[Number]:
    """글에 나온 숫자 전부. 패치 이름(`14_7`)과 이름 속 숫자(`R3`)는 숫자가 아니다."""
    return [
        Number(
            raw=m.group(0).strip(),
            sign=(m.group("sign") or "").replace("−", "-"),
            core=m.group("whole").replace(",", "") + (m.group("frac") or ""),
            unit=u if (u := m.group("unit") or "") in ("%", "%p") else "",
            counted=(m.group("unit") or "") in ("판", "종", "위"),
        )
        for m in _NUMBER.finditer(text)
    ]


def blocks_in(steps: Sequence[Step]) -> set[tuple[str, str]]:
    """노트 도구가 **실제로 돌려준** 블록 — (패치, 띄어쓰기를 뺀 절 이름)."""
    return {
        (patch, _squeeze(section))
        for step in steps
        if step.tool == "search_patch_notes"
        for patch, section in _BLOCK.findall(step.output)
    }


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


@dataclass(frozen=True)
class Check:
    """대조 결과. `numbers` · `notes` 는 **실제로 확인한 개수**다."""

    mark: Mark
    problems: tuple[str, ...] = ()
    numbers: int = 0
    notes: int = 0
    why: str = ""  # ⚪ 일 때 — 왜 대조할 수 없나

    def line(self) -> str:
        """화면에 보일 한 줄. ✅ 는 확인한 것만 말한다."""
        if self.mark == "⚠":
            return "⚠ 코드 대조 — " + " · ".join(self.problems)
        if self.mark == "⚪":
            return f"⚪ 대조할 수 없다 — {self.why}"
        seen = [f"숫자 {self.numbers}개가 도구 결과에 있다"] if self.numbers else []
        if self.notes:
            seen.append(f"인용한 노트 블록 {self.notes}개를 도구가 실제로 줬다")
        return "✅ 코드 대조 통과 — " + " · ".join(seen)


def check(
    answer: Answer | None,
    steps: Sequence[Step],
    *,
    context: str,
    questions: Sequence[str],
) -> Check:
    """답 하나를 출처와 맞춰 본다. **모델을 부르지 않는다.**

    steps      이 대화에서 지금까지 부른 도구와 그 출력
    context    코드가 만든 맥락(기준 패치 지표 · 통계 모델 점수 · 경고)
    questions  사람이 한 질문들
    """
    if answer is None:
        return Check("⚪", why="형식이 맞지 않는다 — 구조화된 답이 없다")

    sources = [context, *questions, *(s.output for s in steps)]
    allowed = [n for text in sources for n in numbers_in(text)]
    patches = {f"{a}.{b}" for text in sources for a, b in _PATCH.findall(text)}

    def grounded(n: Number) -> bool:
        if any(n.same(a) for a in allowed):
            return True
        # `14.7` 은 패치 `14_7` 을 점으로 쓴 것이다 — 측정값이 아니다
        return not n.unit and not n.sign and n.core in patches

    declared = [numbers_in(entry) for entry in answer.numbers]
    malformed = [
        e for e, found in zip(answer.numbers, declared, strict=True) if not found
    ]
    used = [n for found in declared for n in found]
    used += [n for n in numbers_in(answer.answer) if n.measured]

    checked: dict[tuple[str, Decimal, str], Number] = {}
    for n in used:
        checked.setdefault((n.sign, n.value, n.unit), n)
    missing = [n.raw for n in checked.values() if not grounded(n)]

    returned = blocks_in(steps)
    cited = {
        (c.patch.strip().replace(".", "_"), _squeeze(c.section)): c
        for c in answer.notes
    }
    absent = [
        f"[{c.patch}] {c.section}" for key, c in cited.items() if key not in returned
    ]

    problems = []
    if missing:
        problems.append("도구 결과에 없는 숫자: " + ", ".join(missing))
    if absent:
        problems.append("도구가 준 적 없는 노트 블록: " + ", ".join(absent))
    if problems:
        return Check("⚠", tuple(problems))
    if malformed:
        return Check(
            "⚪",
            why="형식이 맞지 않는다 — 숫자 칸에 숫자가 아닌 것: "
            + ", ".join(malformed),
        )
    if not checked and not cited:
        return Check("⚪", why="대조할 것이 없다 — 답에 숫자도 노트 인용도 없다")
    return Check("✅", numbers=len(checked), notes=len(cited))
