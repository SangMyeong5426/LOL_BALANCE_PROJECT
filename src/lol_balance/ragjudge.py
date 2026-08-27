"""검색 맥락을 만들고, 그 위에 내린 판단을 읽는다 — **arm `B6` 의 재료.**

`A5`·`B5` 는 이웃 라벨을 **다수결**할 뿐 생성 단계가 없다. 여기가 그 위에
판단을 올리는 자리다. 근거는 [ADR 0006](../../docs/adr/0006-rag-generation-and-contamination-control.md).

## 판단자가 오염돼 있다

방향 라벨 1,555종을 대화 안의 Claude 가 붙였고, 그 라벨이 곧 채점 정답이다.
**「기억 안 난다」고 주장해도 저장소가 검증할 수 없다.** 그래서 둘로 막는다.

    구조   대상을 익명으로 준다 — 이름을 모르면 기억을 꺼낼 열쇠가 없다
    측정   이름만 주는 조건을 따로 돌려 찍기를 넘는지 본다 (오염 상한)

## 경계는 `B5` 와 같아야 한다

`as_of` 를 **분할점으로 고정**한다. `B5` 가 `expanding=False` 로 그렇게 돌므로
같은 참고 범위여야 짝지어 비교가 된다. 확장 범위(`B5b`)를 쓰면 직전 패치 이웃의
`direction_next` 가 **평가 패치를 가리켜** 다른 조건이 된다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from lol_balance.panel import PATCH_SEQUENCE, PanelRow, patch_index
from lol_balance.retrieval import Case, CaseSearch, NoteSearch

# 판단에 쓰는 조건. `anon` 이 본 조건이고 `named` 는 오염 상한을 잰다.
Condition = Literal["anon", "named"]
CONDITIONS: tuple[Condition, ...] = ("anon", "named")

# 익명 키의 길이. 챔피언과 패치에서 만들되 되돌릴 수 없어야 한다.
KEY_LENGTH = 6

# 뽑는 이웃 수. **`B5` 와 같은 25 다** — 같은 검색 결과 위에서 판단해야
# 「다수결 대신 판단하면 나아지는가」를 재는 것이 된다.
CASE_COUNT = 25

# 그중 방향이 붙은 것만 보인다. `B5` 의 투표도 `nerf`/`buff` 만 센다.
DIRECTED = ("nerf", "buff")

# 이웃 몇 종의 조정 노트를 붙일 것인가. R2(`NoteSearch`)를 쓰는 자리다.
NOTE_CHAMPIONS = 4
NOTE_LINES = 3
# 이름으로 질의한 뒤 **그 이웃이 실제로 조정된 패치**만 남긴다. 안 좁히면
# 스킨 이름 변경 같은 것이 최상위로 올라온다 — 실제로 그랬다.
NOTE_POOL = 40

# 판단 점수의 범위. **라벨이 아니라 점수여야 한다** — AUC 는 줄 세우기 점수다.
PROB_MIN, PROB_MAX = 0, 100


def anon_key(row: PanelRow, condition: Condition = "anon") -> str:
    """챔피언·패치에서 만드는 **되돌릴 수 없는** 키.

    판단 파일만 봐서는 대상을 알 수 없어야 한다. 그래야 판단 기록이 남아도
    다음 회차가 오염되지 않는다.

    **조건마다 키가 달라야 한다.** 처음엔 하나로 썼다가 걸렸다 — `named` 블록이
    `### 834b7a` 를 그대로 보여 주면, 판단자가 **자기가 `anon` 에서 그 키에 매긴
    점수를 꺼내 온다.** 오염 상한을 재려는 검정이 자기 판단에 오염되는 것이다.
    """
    seed = f"{condition}/{row.champion_id}/{row.patch}".encode()
    return hashlib.sha256(seed).hexdigest()[:KEY_LENGTH]


@dataclass(frozen=True)
class Target:
    """판단 대상 하나. `key` 로만 부르고 이름은 담지 않는다."""

    key: str
    patch: str
    champion_id: int


@dataclass(frozen=True)
class Judgment:
    """한 대상에 대한 판단.

    `nerf_prob` 는 **0~100 정수**다. 라벨(`nerf`/`buff`)로 받으면 점수가 두
    종류뿐이라 AUC 가 사실상 정확도가 된다 — `B4` 가 해상도로 겪은 문제다.
    """

    key: str
    condition: Condition
    nerf_prob: int
    reason: str


def _fmt(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _case_line(case: Case) -> str:
    row = case.row
    return (
        f"  {_fmt(row.win_rate)}  {_fmt(row.pick_rate)}  {_fmt(row.ban_rate)}"
        f"  {_fmt(row.wr_gap)}   {row.direction_next}"
    )


def _next_patch(patch: str) -> str | None:
    """`direction_next` 가 가리키는 패치. 순서 끝이면 없다."""
    index = patch_index(patch) + 1
    return PATCH_SEQUENCE[index] if index < len(PATCH_SEQUENCE) else None


def _notes_for(
    notes: NoteSearch | None, cases: Sequence[Case], limit: int = NOTE_CHAMPIONS
) -> list[str]:
    """이웃이 **실제로 조정된 그 패치**에 무엇이 적혔는지 — **R2 를 쓰는 자리.**

    대상이 아니라 **이웃**을 질의하므로 익명이 깨지지 않는다. 그리고 이름만으로
    질의하면 스킨 이름 변경 같은 것이 최상위로 올라오므로, **그 이웃의 조정
    패치로 좁힌다.**

    경계는 `NoteSearch` 가 지킨다 — `as_of` 이후 패치는 색인에 아예 없다.
    """
    if notes is None:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for case in cases:
        row = case.row
        name = row.champion
        target = _next_patch(row.patch)
        if name in seen or target is None:
            continue
        seen.add(name)
        for patch, block, _ in notes.search(name, k=NOTE_POOL):
            if patch != target or block.champion != name:
                continue
            lines = [ln for ln in block.lines[:NOTE_LINES] if ln.strip()]
            if lines:
                out.append(
                    f"  {row.direction_next} · [{block.section}] " + " / ".join(lines)
                )
            break
        if len(out) >= limit:
            break
    return out


def render(
    row: PanelRow,
    condition: Condition,
    cases: Sequence[Case] = (),
    notes: NoteSearch | None = None,
) -> str:
    """판단자에게 줄 맥락 블록 하나.

    **`named` 는 이름과 패치만 준다.** 수치도 사례도 없다 — 맞히면 기억뿐이다.
    """
    key = anon_key(row, condition)
    if condition == "named":
        return f"### {key}\n챔피언: {row.champion}\n패치: {row.patch}\n"

    head = (
        f"### {key}\n"
        f"역할: {row.main_role}  ·  판수: {row.matches:,}\n"
        f"승률 {_fmt(row.win_rate)}  픽률 {_fmt(row.pick_rate)}  "
        f"밴율 {_fmt(row.ban_rate)}  |승률−0.5| {_fmt(row.wr_gap)}\n"
        f"직전 대비  승률 {_fmt(row.d_win_rate, 4)}  픽률 {_fmt(row.d_pick_rate, 4)}\n"
    )
    if not cases:
        return head
    # **방향이 붙은 이웃만 보인다.** `B5` 의 투표도 그것만 센다. 다만 「몇 종
    # 중 몇 종이 조정됐나」는 그 자체가 맥락이므로 한 줄로 남긴다.
    directed = [c for c in cases if c.row.direction_next in DIRECTED]
    body = (
        f"\n가장 가까운 과거 사례 {len(cases)}종 중 방향이 붙은 것 {len(directed)}종\n"
    )
    if directed:
        body += (
            "  승률     픽률     밴율     격차     그 다음에\n"
            + "\n".join(_case_line(c) for c in directed)
            + "\n"
        )
    note_lines = _notes_for(notes, directed)
    if note_lines:
        body += "\n그 사례들이 실제로 조정된 내용\n" + "\n".join(note_lines) + "\n"
    return head + body


def build(
    targets: Sequence[PanelRow],
    pool: Sequence[PanelRow],
    as_of: str,
    condition: Condition,
    blocks: Mapping[str, Sequence[object]] | None = None,
    k: int = CASE_COUNT,
) -> str:
    """대상 여럿의 맥락을 이어 붙인다.

    **검색기는 `as_of` 를 생성자에서 받는다.** 여기서 빠뜨려도 경계 밖 데이터가
    섞이지 않는다.
    """
    search = CaseSearch(pool, as_of) if condition == "anon" else None
    notes = (
        NoteSearch(blocks, as_of)  # type: ignore[arg-type]
        if condition == "anon" and blocks
        else None
    )
    parts = []
    for row in targets:
        cases = search.similar(row, k=k) if search is not None else ()
        parts.append(render(row, condition, cases, notes))
    return "\n".join(parts)


def targets_of(rows: Iterable[PanelRow]) -> tuple[Target, ...]:
    """대상 목록 — 익명 키와 실제 행을 잇는 표. **판단 파일에는 안 들어간다.**"""
    return tuple(Target(anon_key(r), r.patch, r.champion_id) for r in rows)


def read_judgments(path: Path) -> dict[tuple[str, str], Judgment]:
    """`ground_truth/rag/*.jsonl` → (키, 조건) → 판단.

    **형식이 어긋나면 조용히 넘기지 않는다.** 점수가 범위를 벗어나거나 근거가
    비어 있으면 그 자리에서 알린다 — 판단 314건을 다 만든 뒤에 알면 늦다.
    """
    out: dict[tuple[str, str], Judgment] = {}
    if not path.exists():
        return out
    for file in sorted(path.glob("*.jsonl")):
        for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            text = line.strip()
            if not text:
                continue
            where = f"{file.name}:{number}"
            record = json.loads(text)
            key = record.get("key")
            condition = record.get("condition")
            prob = record.get("nerf_prob")
            reason = (record.get("reason") or "").strip()
            if not key or condition not in CONDITIONS:
                raise ValueError(f"{where}: key 나 condition 이 없다 — {record}")
            # **`bool` 을 먼저 걷어낸다.** 파이썬에서 `True` 는 `int` 의
            # 부분형이라 범위 검사만으로는 통과한다.
            if (
                isinstance(prob, bool)
                or not isinstance(prob, int)
                or not PROB_MIN <= prob <= PROB_MAX
            ):
                raise ValueError(f"{where}: nerf_prob 이 0~100 정수가 아니다 — {prob}")
            if not reason:
                raise ValueError(f"{where}: reason 이 비어 있다")
            if (key, condition) in out:
                raise ValueError(f"{where}: 같은 (키, 조건)이 두 번 나왔다 — {key}")
            out[(key, condition)] = Judgment(key, condition, prob, reason)
    return out
