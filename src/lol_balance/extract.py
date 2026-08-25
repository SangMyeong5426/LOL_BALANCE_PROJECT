"""패치 노트 문장 → 구조화된 변경 기록. **LLM 이 처음 들어가는 자리다.**

경계를 좁게 잡는다. 어느 챔피언의 어느 스킬인지는 **코드가 이미 확정했다** —
위키가 `data-champion` · `data-ability` 속성에 정확한 이름을 넣어 주기 때문이다
(`patchnotes.champion_changes`). LLM 이 하는 일은 문장 하나를 **「무엇이 몇에서
몇으로, 그리고 그것이 너프인가 버프인가」**로 바꾸는 것뿐이다.

이 일을 규칙으로 못 하는 이유는 표기가 시대마다 다르기 때문이다.

    2023~2024   Third cast base damage reduced to 15 / 37.5 / 60 from 15 / 45 / 75
    2026        Base movement speed 330 ⇒ 335

방향 판정도 규칙이 아니다. 쿨다운은 줄면 버프, 체력은 줄면 너프, 사거리는
항목마다 다르다. 이 판단이 `ddragon.diff_versions` 가 못 주는 바로 그것이다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from lol_balance.patchnotes import ChangeBlock

if TYPE_CHECKING:  # 호출부에서만 필요하다. 수집·집계는 SDK 없이 돈다.
    from anthropic import Anthropic

Direction = Literal["nerf", "buff", "adjust"]

# `output_config.effort`. 추출은 기계적인 축에 가까워 기본을 medium 으로 둔다.
Effort = Literal["low", "medium", "high", "xhigh", "max"]

SYSTEM = """You convert League of Legends patch note sentences into structured records.

The champion and ability are already known — they are given to you. Do not infer them from the text.

For each sentence, emit one record per distinct value that changed. A sentence may
describe several values (e.g. base damage and an AD ratio); emit one record each.

- `field`: what changed, in the note's own words, lowercased ("base damage", "cooldown",
  "base health", "ad ratio", "mana cost"). Do not invent a taxonomy.
- `before` / `after`: the values exactly as written, keeping per-rank slashes
  ("15 / 45 / 75 / 105 / 135"). Keep units such as "%" or "% AD" if present.
- `direction`: the effect on the champion's strength, not the arithmetic direction.
  Lower cooldown is a buff. Lower health is a nerf. Use "adjust" when the sentence
  is not a straightforward power change, or when it is purely descriptive.
- `source`: the sentence you read it from, verbatim.

If a sentence carries no numeric change (a bug fix, a visual tweak), skip it."""


class ChangeRecord(BaseModel):
    """수치 하나가 바뀐 기록. `ddragon.Change` 와 대조할 수 있는 형태다."""

    champion: str
    ability: str | None = Field(
        default=None, description="Ability name, null for stats"
    )
    field: str
    before: str
    after: str
    direction: Direction
    source: str


class PatchExtraction(BaseModel):
    records: list[ChangeRecord]


def build_prompt(blocks: tuple[ChangeBlock, ...]) -> str:
    """묶음들을 한 요청으로 만든다.

    챔피언·스킬을 머리말로 못박고 문장만 나열한다. 이렇게 하면 모델이 이름을
    지어낼 여지가 없고, 같은 챔피언의 문장들이 한자리에 모여 문맥도 유지된다.
    """
    parts: list[str] = []
    for block in blocks:
        head = f"## {block.champion} — {block.section}"
        if block.ability:
            head += f" (ability: {block.ability})"
        parts.append(head + "\n" + "\n".join(f"- {line}" for line in block.lines))
    return "\n\n".join(parts)


def extract_patch(
    client: Anthropic,
    blocks: tuple[ChangeBlock, ...],
    model: str,
    effort: Effort = "medium",
    max_tokens: int = 16000,
) -> tuple[ChangeRecord, ...]:
    """한 패치의 변경 묶음을 구조화 기록으로 바꾼다.

    구조화 출력을 API 층에서 강제한다(`output_format`). 자유 텍스트로 받아
    파싱하면 실패가 조용히 섞이고, 그러면 정답지가 오염된다.
    """
    if not blocks:
        return ()
    parsed = client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM,
        output_format=PatchExtraction,
        output_config={"effort": effort},
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": build_prompt(blocks)}],
    )
    result = parsed.parsed_output
    return tuple(result.records) if result else ()
