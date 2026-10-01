"""후속 질문의 답을 **코드가 대조한다** — [extension 3절](../../../docs/extension.md).

    ⚠  도구 결과에 없는 숫자를 썼거나, 도구가 준 적 없는 노트 블록을 인용했거나,
       **도구가 낸 주의를 답에서 뺐다**
    ⚪  대조할 수 없다 — 숫자도 인용도 없거나 형식이 맞지 않는다
    ✅  대조를 통과했다. **실제로 확인한 것만 말한다**

모델에게 「맞게 썼나」를 묻지 않는다. 답에 나온 숫자와 인용을 도구 출력과 맞춰 본다.

## 도구가 낸 주의는 답이 옮겨야 한다

도구는 표본이 얇거나, 출처가 섞였거나, 기준 패치 뒤라 볼 수 없으면 그렇게 말한다.
**답이 그 말을 빼면 ⚠ 다** — 3,183판으로 잰 변화를 효과처럼 읽게 되고, 「볼 수 없다」가
「없었다」로 바뀐다(로컬 모델이 실제로 그렇게 답했다, 2026-10-01). 화면은 모델이 뭐라고
쓰든 그 주의를 답 아래에 **코드로 붙인다**(`cautions`).

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
class Caution:
    """도구가 붙인 주의 하나. `text` 는 도구가 쓴 문장 그대로다."""

    kind: str  # "표본" · "경계" · "출처"
    text: str


# (종류, 도구 출력에서 찾는 말, 답에 이 중 하나는 있어야 한다, 빠졌을 때 적는 말)
_CARRY: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("표본", "표본이 얇다", ("표본",), "표본이 얇다"),
    (
        "경계",
        "경계 밖",
        ("경계 밖", "볼 수 없", "알 수 없", "확인할 수 없"),
        "기준 패치 뒤라 볼 수 없다",
    ),
    ("출처", "출처가", ("출처", "직접 집계"), "출처가 다르다"),
)


def cautions(steps: Sequence[Step]) -> list[Caution]:
    """도구 출력에서 주의 문장을 **그대로** 꺼낸다. 같은 문장은 한 번만."""
    out: list[Caution] = []
    for step in steps:
        for line in step.output.splitlines():
            kind = next((k for k, needle, _, _ in _CARRY if needle in line), None)
            text = line.strip().removeprefix("⚠").strip()
            if kind and all(c.text != text for c in out):
                out.append(Caution(kind, text))
    return out


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
    asked: Sequence[Step] | None = None,
    text: str = "",
) -> Check:
    """답 하나를 출처와 맞춰 본다. **모델을 부르지 않는다.**

    steps      이 대화에서 지금까지 부른 도구와 그 출력 — 숫자와 인용의 출처다
    context    코드가 만든 맥락(기준 패치 지표 · 통계 모델 점수 · 경고)
    questions  사람이 한 질문들
    asked      **이번 질문에서** 부른 도구. 답이 옮겨야 할 주의는 여기서만 본다 —
               앞 질문의 주의를 뒤 질문의 답에 요구하지 않는다. 안 주면 `steps` 전부
    text       구조화된 답이 없을 때(`answer` 가 None) 모델이 글로 쓴 답. **글도
               훑는다** — 없는 숫자나 빠뜨린 주의가 있으면 ⚪ 가 아니라 ⚠ 다
    """
    prose = answer.answer if answer is not None else text
    fields = answer.numbers if answer is not None else []
    notes = answer.notes if answer is not None else []

    sources = [context, *questions, *(s.output for s in steps)]
    allowed = [n for text in sources for n in numbers_in(text)]
    patches = {f"{a}.{b}" for text in sources for a, b in _PATCH.findall(text)}

    def grounded(n: Number) -> bool:
        if any(n.same(a) for a in allowed):
            return True
        # `14.7` 은 패치 `14_7` 을 점으로 쓴 것이다 — 측정값이 아니다
        return not n.unit and not n.sign and n.core in patches

    declared = [numbers_in(entry) for entry in fields]
    malformed = [e for e, found in zip(fields, declared, strict=True) if not found]
    used = [n for found in declared for n in found]
    used += [n for n in numbers_in(prose) if n.measured]

    checked: dict[tuple[str, Decimal, str], Number] = {}
    for n in used:
        checked.setdefault((n.sign, n.value, n.unit), n)
    missing = [n.raw for n in checked.values() if not grounded(n)]

    returned = blocks_in(steps)
    cited = {(c.patch.strip().replace(".", "_"), _squeeze(c.section)): c for c in notes}
    absent = [
        f"[{c.patch}] {c.section}" for key, c in cited.items() if key not in returned
    ]

    # 도구가 낸 주의를 답이 옮겼나. 띄어쓰기는 안 본다(「볼수없다」도 옮긴 것이다)
    said = _squeeze(prose)
    raised = {c.kind for c in cautions(steps if asked is None else asked)}
    if not checked:
        # 표본 · 출처 주의는 숫자를 쓴 답에만 요구한다. 「경계 밖」은 늘 요구한다 —
        # 못 본 것을 없었다고 말하는 것은 숫자가 없어도 틀린 답이다
        raised &= {"경계"}
    dropped = [
        label
        for kind, _, words, label in _CARRY
        if kind in raised and not any(_squeeze(w) in said for w in words)
    ]

    problems = []
    if missing:
        problems.append("도구 결과에 없는 숫자: " + ", ".join(missing))
    if absent:
        problems.append("도구가 준 적 없는 노트 블록: " + ", ".join(absent))
    if dropped:
        problems.append("도구가 낸 주의를 답에서 뺐다: " + ", ".join(dropped))
    if problems:
        return Check("⚠", tuple(problems))
    if answer is None:
        return Check("⚪", why="형식이 맞지 않는다 — 구조화된 답이 없다")
    if malformed:
        return Check(
            "⚪",
            why="형식이 맞지 않는다 — 숫자 칸에 숫자가 아닌 것: "
            + ", ".join(malformed),
        )
    if not checked and not cited:
        return Check("⚪", why="대조할 것이 없다 — 답에 숫자도 노트 인용도 없다")
    return Check("✅", numbers=len(checked), notes=len(cited))
