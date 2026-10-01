"""후속 질문의 답을 **코드가 대조한다** — [extension 3절](../../../docs/extension.md).

    ⚠  도구 결과에 없는 숫자를 썼거나, **도구가 낸 주의를 답에서 뺐거나, 볼 수 없는 것을
       없었다고 말했다**
    ⚪  대조할 것이 없다 — 답에 숫자가 없다
    ✅  대조를 통과했다. **실제로 확인한 것만 말한다**

모델에게 「맞게 썼나」를 묻지 않는다. 답에 나온 숫자를 도구 출력과 맞춰 본다.

## 글로만 답해도 본다 — 표시는 확인한 것을 따른다

모델이 `Answer` 를 부르지 않고 글로만 답해도 **글의 숫자를 맞춰 본다.** 확인했으면 ✅ 고,
없는 숫자나 빠뜨린 주의가 있으면 ⚠ 다. **칸에 넣었는지도, 숫자 칸에 숫자가 아닌 것을 같이
적었는지도 표시를 가르지 않는다** — ⚪ 는 확인한 숫자가 하나도 없을 때뿐이다.

한때는 칸에 넣지 않은 답을 「형식이 맞지 않는다」며 ⚪ 로 뒀다 — 확인하고도 「대조할 수
없다」고 적는 셈이었다. 로컬 모델은 칸을 강제할 수 없다(Ollama 가 `tool_choice` 를 무시한다).
인용 칸을 빼자 글로만 답한 것이 27건 중 6 → 16건으로 늘었고, 숫자가 전부 맞는데 ✅ 가
15 → 5개로 줄었다. 그래서 고쳤다(주인 승인 2026-10-01, ADR 0017 덧붙임).

## 노트 블록은 코드가 붙인다 (ADR 0017)

근거로 본 노트 블록은 모델이 적지 않는다. **이번 질문에서 노트 도구가 돌려준 블록**을 코드가
꺼내(`note_blocks`) 화면이 답 아래에 붙인다 — 그래서 없는 블록이 나올 수 없고, 대조할 것도
없다. 노트 질문에 숫자 없이 답하면 확인한 것이 없으니 ✅ 가 아니라 ⚪ 다.

한때는 모델이 인용을 적었고 코드가 그것을 도구가 준 블록과 맞춰 봤다. 그때 받아 커밋한
답은 **그때의 규칙으로 다시 채점한다** — 그 인용을 `check(cited=…)` 로 넘기면 「도구가 준 적
없는 노트 블록」을 전처럼 잡는다. 문서에 적은 수치가 계속 재현돼야 해서 남겨 둔다.

## 도구가 낸 주의는 답이 옮겨야 한다

도구는 표본이 얇거나, 출처가 섞였거나, 기준 패치 뒤라 볼 수 없으면 그렇게 말한다.
**답이 그 말을 빼면 ⚠ 다** — 3,183판으로 잰 변화를 효과처럼 읽게 되고, 「볼 수 없다」가
「없었다」로 바뀐다(로컬 모델이 실제로 그렇게 답했다, 2026-10-01). 화면은 모델이 뭐라고
쓰든 그 주의를 답 아래에 **코드로 붙인다**(`cautions`).

**「볼 수 없다」고 쓰고도 「조정이 이루어지지 않았다」고 단정하면 ⚠ 다.** 주의하는 말이
있어도 결론이 틀렸다 — 고정 질문 27개에서 그런 답이 통과했다(2026-10-01). 그 말 앞에서
가장 가까운 패치 이름이 경계 밖 패치일 때만 잡는다. 「15_13 에는 조정되지 않았고 15_14 는
볼 수 없다」는 맞는 답이다. 「기록이 없다 · 보이지 않는다」는 잡지 않는다 — 자료에 없다는
말이지 일어나지 않았다는 말이 아니다.

## 무엇을 출처로 치나

    도구 출력            이 대화에서 도구가 실제로 돌려준 글
    코드가 만든 맥락      기준 패치 지표 · 통계 모델 점수 · 코드가 낸 경고
    사람의 질문          사람이 물으면서 쓴 숫자

**모델이 쓴 글은 출처가 아니다** — 앞선 답도, 해설도. 거기 있던 숫자를 다시 쓰려면
도구로 다시 확인해야 한다.

## ADR 0007 과 어떻게 다른가

ADR 0007 은 「글에서 값을 뽑는 파서」를 기각했다 — 뽑은 값을 **쓰려는** 것이었다.
여기서는 값을 쓰지 않는다. 답의 숫자가 **출처에 있는지 없는지만** 본다. 주된 길은
모델이 옮겨 적은 칸(`numbers`)이고, 글을 훑는 것은 칸을 비워 두고 빠져나가는 것을
막는 보조다.

## 걸지 않는 것

고정 질문 27개에서 「틀리지 않았는데 걸린 것」을 보고 뺐다(2026-10-01).

    「50%」            승률의 균형선이다 — 기준으로 쓰는 말이지 잰 값이 아니다(「50% 선 근처」).
                      `50.0%` 처럼 소수까지 적으면 잰다
    패치 이름          `14.7` 은 패치 `14_7` 을 점으로 쓴 것이다. 확인한 숫자로 세지도 않는다
    패치의 대괄호      (옛 형식) 인용의 패치를 `[14_7]` 로 적어도 같은 패치다
    도구가 준 줄       (옛 형식) 모델이 `effect_of` 의 머리 줄이나 거절한 말을 노트 블록 칸에
                      적었다. 지어낸 것이 아니라 근거로 본 줄을 가리킨 것이고, 그 줄은 도구가
                      실제로 줬다. 노트 블록으로 세지 않을 뿐이다(`misfiled` 에 적어 따로 센다).
                      **어느 도구도 준 적 없는 것만 「없는 노트 블록」이다**

## 못 잡는 것

숫자는 맞는데 **뜻을 틀리게 쓴 것**은 못 잡는다 — 승률을 픽률이라고 부르거나, 부호
없이 「4.4%p 올랐다」고 쓴 것(실제로는 내렸다). 부호를 적었으면 부호까지 본다.
그래서 ✅ 는 「맞는 답」이 아니라 「숫자가 출처에 있다」는 뜻이다.

- **글에서 노트 내용을 지어낸 것** — 숫자면 잡고 숫자가 아니면 못 잡는다. 인용을 모델이 적던
  때에도 인용 목록만 봤지 글의 주장을 블록과 맞춰 보지는 않았다
- 잰 값이 아닌데 「승률이 50% 였다」고 쓴 것 — 균형선과 구별하지 못한다
- 표의 줄을 잘못 읽은 것 — `lookup_stats` 의 「→ 다음 패치」 열을 그 패치의 조정으로 읽었다
- 「없었다」는 말은 **적어 둔 표현만** 잡는다(`_DENIAL`). 다르게 돌려 말하면 지나간다.
  경계 밖이 아닌 패치를 두고 한 말은 보지 않는다
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from lol_balance.agent.judge import Step
from lol_balance.agent.schema import Answer, Cited

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
# 답에 나온 패치 이름 — `15_14` 도 `15.14` 도. 뒤에 `%` 가 붙으면 숫자다(`15.4%`)
_MENTION = re.compile(r"(?<![\d_.])(\d{2})([_.])(\d{1,2})(?!\d|_\d|\s?%)")
# **일어나지 않았다**고 단정하는 말 — 「조정되지 않았다 · 조정이 없었다 · 변경 내용이 없다」.
# 「기록이 없다 · 보이지 않는다」는 넣지 않는다(자료에 없다는 말이다). 뒤가 「…는지」 ·
# 「…다면」 · 「…다는 뜻이 아니다」 · 「…다고 단정할 수 없다」면 단정이 아니다
_DENIAL = re.compile(
    r"(?:조정|변경|너프|버프)\s*(?:사항|내용)?\s*[이은도가는을를]?\s*"
    r"(?:(?:되지|이루어지지|받지|하지)\s*않았|없(?:었|습니다|으며|고|다))"
    r"(?!는지|다?면|다?는\s*(?:뜻|것|말)[이은]\s*아[니닙닌]|다?고\s*(?:단정|말)할\s*수\s*없)"
)
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

    @property
    def balance(self) -> bool:
        """**균형선 「50%」인가.** 기준으로 쓰는 말이지 잰 값이 아니라 출처를 묻지 않는다.

        고정 질문 27개에서 「도구 결과에 없는 숫자」로 걸린 2건이 둘 다 「50% 선 근처」 ·
        「평균 수준(약 50%)」이었다(2026-10-01). `50.0%` · `+50%` · `50%p` 는 잰 값이다.
        """
        return self.core == "50" and self.unit == "%" and not self.sign

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


def note_blocks(steps: Sequence[Step]) -> list[str]:
    """**근거로 본 노트 블록** — 노트 도구가 돌려준 머리 줄을 준 그대로, 준 순서로.

    화면이 답 아래에 붙인다(ADR 0017). 모델이 적는 것이 아니라 도구 출력에서 꺼내므로
    없는 블록이 나올 수 없다. 같은 블록은 한 번만 낸다.
    """
    out: list[str] = []
    for step in steps:
        if step.tool != "search_patch_notes":
            continue
        for patch, section in _BLOCK.findall(step.output):
            block = f"[{patch}] {section}"
            if block not in out:
                out.append(block)
    return out


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _lines_in(steps: Sequence[Step]) -> set[str]:
    """도구가 돌려준 줄 전부(띄어쓰기를 뺀 것). **노트 블록의 머리 줄은 뺀다** — 그것은
    `blocks_in` 이 패치까지 맞춰 본다.

    `패치: 이유` 꼴의 줄은 이유만도 넣는다 — 모델이 그 줄을 패치와 절로 쪼개 적는다.
    """
    out: set[str] = set()
    for step in steps:
        for line in step.output.splitlines():
            if not line.strip() or _BLOCK.match(line):
                continue
            out.add(_squeeze(line))
            head, colon, rest = line.partition(":")
            if colon and _PATCH.fullmatch(head.strip()):
                out.add(_squeeze(rest))
    return out


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


def _beyond(steps: Sequence[Step]) -> set[str]:
    """도구가 「경계 밖」이라고 한 패치 — 그 줄의 첫 패치 이름이다."""
    return {
        f"{m.group(1)}_{m.group(2)}"
        for step in steps
        for line in step.output.splitlines()
        if "경계 밖" in line and (m := _PATCH.search(line))
    }


def _denied(prose: str, beyond: set[str], known: set[str]) -> list[str]:
    """**볼 수 없는 패치를 두고 「없었다」고 한 말.**

    그 말 **앞에서 가장 가까운 패치 이름**이 가리키는 패치를 본다. 앞에 패치 이름이 없으면
    지금 묻는 패치(경계 밖)를 두고 한 말로 본다. `known` 은 출처에 나온 패치를 점으로 쓴
    것이다 — `15.14` 를 패치로 읽을지 숫자로 읽을지를 가른다.
    """
    if not beyond:
        return []
    named = [
        (m.start(), f"{m.group(1)}_{m.group(3)}")
        for m in _MENTION.finditer(prose)
        if m.group(2) == "_" or f"{m.group(1)}.{m.group(3)}" in known
    ]
    out: list[str] = []
    for said in _DENIAL.finditer(prose):
        before = [patch for at, patch in named if at < said.start()]
        if not before or before[-1] in beyond:
            out.append(said.group(0))
    return out


@dataclass(frozen=True)
class Check:
    """대조 결과. `numbers` 는 **실제로 확인한 개수**다.

    `notes` · `absent` · `misfiled` 는 **옛 형식의 기록**(모델이 인용을 적던 때)을 다시 채점할
    때만 찬다.
    """

    mark: Mark
    problems: tuple[str, ...] = ()
    numbers: int = 0
    notes: int = 0
    why: str = ""  # ⚪ 일 때 — 왜 대조할 것이 없나
    # ⚠ 의 내용을 종류별로 — 고정 질문 세트를 셀 때 쓴다
    missing: tuple[str, ...] = ()  # 도구 결과에 없는 숫자
    absent: tuple[str, ...] = ()  # 도구가 준 적 없는 노트 블록
    # 인용 칸에 적은, 노트 블록이 아닌 도구 줄 — 걸지 않는다
    misfiled: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()  # 답에서 뺀 주의
    denied: tuple[str, ...] = ()  # 볼 수 없는 것을 없었다고 한 말

    def line(self) -> str:
        """화면에 보일 한 줄. ✅ 는 확인한 것만 말한다."""
        if self.mark == "⚠":
            return "⚠ 코드 대조 — " + " · ".join(self.problems)
        if self.mark == "⚪":
            return f"⚪ 대조할 것이 없다 — {self.why}"
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
    cited: Sequence[Cited] = (),
) -> Check:
    """답 하나를 출처와 맞춰 본다. **모델을 부르지 않는다.**

    steps      이 대화에서 지금까지 부른 도구와 그 출력 — 숫자와 인용의 출처다
    context    코드가 만든 맥락(기준 패치 지표 · 통계 모델 점수 · 경고)
    questions  사람이 한 질문들
    asked      **이번 질문에서** 부른 도구. 답이 옮겨야 할 주의는 여기서만 본다 —
               앞 질문의 주의를 뒤 질문의 답에 요구하지 않는다. 안 주면 `steps` 전부
    text       구조화된 답이 없을 때(`answer` 가 None) 모델이 글로 쓴 답. **글도
               똑같이 본다** — 숫자를 확인했으면 ✅, 없는 숫자나 빠뜨린 주의가 있으면 ⚠ 다
    cited      **옛 형식의 기록에서만** — 모델이 적은 노트 인용. 도구가 준 블록인지 본다.
               지금은 모델이 인용을 적지 않아 화면은 이것을 넘기지 않는다(ADR 0017)
    """
    prose = answer.answer if answer is not None else text
    fields = answer.numbers if answer is not None else []

    sources = [context, *questions, *(s.output for s in steps)]
    allowed = [n for text in sources for n in numbers_in(text)]
    patches = {f"{a}.{b}" for text in sources for a, b in _PATCH.findall(text)}

    def patch_name(n: Number) -> bool:
        # `14.7` 은 패치 `14_7` 을 점으로 쓴 것이다 — 측정값이 아니다
        return not n.unit and not n.sign and n.core in patches

    # 숫자 칸에 숫자가 아닌 것(패치 이름 · 스킬 이름)을 적은 것은 그냥 지나간다
    used = [n for entry in fields for n in numbers_in(entry)]
    used += [n for n in numbers_in(prose) if n.measured]
    # 균형선과 패치 이름은 잰 값이 아니다 — 출처를 묻지 않고, 확인한 숫자로 세지도 않는다
    used = [n for n in used if not n.balance and not patch_name(n)]

    checked: dict[tuple[str, Decimal, str], Number] = {}
    for n in used:
        checked.setdefault((n.sign, n.value, n.unit), n)
    missing = [n.raw for n in checked.values() if not any(n.same(a) for a in allowed)]

    # ── 옛 형식의 인용 — 지금은 `cited` 가 비어 있어 아래가 전부 빈다(ADR 0017) ──
    returned = blocks_in(steps)
    # 패치는 `14.7` · `[14_7]` 로 적어도 같은 패치다 — 표기는 봐주고 내용은 그대로 본다
    pointed = {
        (c.patch.strip().strip("[]").replace(".", "_"), _squeeze(c.section)): c
        for c in cited
    }
    # 블록이 아닌데 **도구가 준 줄**이면 걸지 않는다(세지도 않는다). 어디에도 없으면 지어냈다
    told = _lines_in(steps)
    unknown = {key: c for key, c in pointed.items() if key not in returned}
    shown = {key: f"[{key[0]}] {c.section}" for key, c in unknown.items()}
    misfiled = [shown[key] for key in unknown if key[1] in told]
    absent = [shown[key] for key in unknown if key[1] not in told]

    # 도구가 낸 주의를 답이 옮겼나. 띄어쓰기는 안 본다(「볼수없다」도 옮긴 것이다)
    said = _squeeze(prose)
    this = steps if asked is None else asked
    raised = {c.kind for c in cautions(this)}
    if not checked:
        # 표본 · 출처 주의는 숫자를 쓴 답에만 요구한다. 「경계 밖」은 늘 요구한다 —
        # 못 본 것을 없었다고 말하는 것은 숫자가 없어도 틀린 답이다
        raised &= {"경계"}
    dropped = [
        label
        for kind, _, words, label in _CARRY
        if kind in raised and not any(_squeeze(w) in said for w in words)
    ]
    # 주의하는 말을 했어도 「없었다」고 단정했으면 틀린 답이다
    denied = _denied(prose, _beyond(this), patches)

    problems = []
    if missing:
        problems.append("도구 결과에 없는 숫자: " + ", ".join(missing))
    if absent:
        problems.append("도구가 준 적 없는 노트 블록: " + ", ".join(absent))
    if dropped:
        problems.append("도구가 낸 주의를 답에서 뺐다: " + ", ".join(dropped))
    if denied:
        problems.append("볼 수 없는 것을 없었다고 말했다: " + ", ".join(denied))
    if problems:
        return Check(
            "⚠",
            tuple(problems),
            missing=tuple(missing),
            absent=tuple(absent),
            misfiled=tuple(misfiled),
            dropped=tuple(dropped),
            denied=tuple(denied),
        )
    verified = len(pointed) - len(unknown)
    if not checked and not verified:
        return Check("⚪", why="답에 숫자가 없다", misfiled=tuple(misfiled))
    return Check("✅", numbers=len(checked), notes=verified, misfiled=tuple(misfiled))
