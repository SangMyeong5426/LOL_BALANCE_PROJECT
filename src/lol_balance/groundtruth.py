"""정답지 — 방향 라벨.

한 챔피언이 한 패치에서 **너프됐는지 버프됐는지**를 남긴다. 수치까지 뽑는 것은
다음 단계이고, 방향만으로도 세 가지가 열린다 — 방향 예측, 너프·버프를 나눈
대상 예측, 그리고 조정 효과 측정.

[ADR-0004](../../docs/adr/0004-processed-data-storage-format.md) 대로 **JSONL 로
저장소에 커밋한다.** 다시 만들면 같은 값이 나온다는 보장이 없어서다 — 무엇이
언제 어떻게 바뀌었는지가 이력에 남아야 한다.

## 채점

Data Dragon 이 닿는 범위에서는 방향이 기계적으로 정해진다(`direction.py`).
그것과 대조해 라벨 품질을 잰다. **다만 자동 판정은 「그 diff 가 본 것」의
방향일 뿐이다.** 쿨다운을 줄이면서(버프) 피해량도 줄였으면(너프) 실제로는
`mixed` 인데 자동 판정은 `buff` 로 나온다. 그래서 「어긋남」과 「충돌」을 가른다.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from lol_balance.direction import Direction

# 스키마를 바꾸면 이미 붙인 라벨을 다시 봐야 한다. 그 사실이 파일에 남아야 한다.
SCHEMA_VERSION = 1

Verdict = Literal["agree", "extends", "conflict"]


@dataclass(frozen=True)
class DirectionLabel:
    """한 챔피언 · 한 패치의 방향."""

    patch: str
    champion: str
    direction: Direction
    # 근거가 된 문장. 나중에 이 라벨을 의심하게 됐을 때 되짚을 수 있어야 한다.
    evidence: tuple[str, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


def write_labels(path: Path, labels: tuple[DirectionLabel, ...]) -> None:
    """정렬해서 쓴다. 순서가 흔들리면 diff 가 못 읽는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(labels, key=lambda x: (x.patch, x.champion))
    path.write_text("\n".join(x.to_json() for x in ordered) + "\n")


def read_labels(path: Path) -> tuple[DirectionLabel, ...]:
    if not path.exists():
        return ()
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            raw = json.loads(line)
            out.append(DirectionLabel(**{**raw, "evidence": tuple(raw["evidence"])}))
    return tuple(out)


def compare(label: Direction, automatic: Direction) -> Verdict:
    """손으로 붙인 라벨을 자동 판정과 맞춘다.

    `extends` 는 어긋남이 아니다. 자동 판정이 `buff` 인데 라벨이 `mixed` 라면,
    노트에서 Data Dragon 이 못 보는 너프를 함께 찾았다는 뜻이라 **오히려 맞다.**

    `conflict` 만 문제다 — 자동이 `buff` 인데 라벨이 `nerf` 면 둘 중 하나가 틀렸다.
    """
    if label == automatic:
        return "agree"
    if label == "mixed":
        return "extends"
    if automatic == "mixed":
        return "conflict"
    return "conflict"
