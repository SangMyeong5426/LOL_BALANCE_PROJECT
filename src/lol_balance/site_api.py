"""배포 페이지의 정적 API — `docs/site/api/v1/` 에 빌드 때 미리 만들어 두는 JSON.

공개할 수 있는 u.gg 자료는 `16_13` 에서 멈춰 더 바뀌지 않는다. 그래서 서버 없이
응답을 미리 만든다([ADR 0016](../../docs/adr/0016-static-api-and-local-qa.md)).
이 모듈이 **응답의 모양(pydantic 모델)과 만드는 법을 한곳에 둔다** — JSON Schema
와 계약 테스트가 같은 모델에서 나오고, 배포 페이지의 후보 목록도 같은 함수를 쓴다.

    경로                     내용
    index.json               버전 · 자료 범위 · 출처 · 경로 목록
    patches.json             예측 지점 목록 (기준 패치 → 예측 패치)
    patches/{패치}.json      그 패치의 너프 · 버프 후보 상위 10 · 근거 · 경고 · 실제 결과
    champions.json           챔피언 목록 (ID · 영문 · 한글 · 조정 횟수)
    champions/{ID}.json      패널 전 구간의 이력과 조정마다의 효과
    effects.json             조정 효과 요약 (방향별 · 대조군)
    schema/{이름}.json       위 응답들의 JSON Schema

**점수는 확률이 아니다.** 한 패치 안에서 후보를 줄 세우는 값이라 `score` 로 적는다.
**같은 버전 안에서는 칸을 더하기만 한다** — 빼거나 뜻을 바꾸면 `v2` 로 낸다
([extension 2절](../../docs/extension.md#2-정적-api-와-배포-페이지)).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
from pydantic import BaseModel, ConfigDict

from lol_balance.arms import rank_candidates
from lol_balance.baseline import direction_rows
from lol_balance.effect import Outcome, balance_control, outcomes
from lol_balance.explain import Note, outcome, reasons
from lol_balance.panel import PATCH_SEQUENCE, PanelRow, next_patch, patch_index
from lol_balance.ragjudge import Source
from lol_balance.retrieval import CaseSearch
from lol_balance.rules import Rule

API_DIR = Path("api") / "v1"
# **공개 범위의 끝.** u.gg 패널의 마지막 패치다. 그 뒤는 Riot API 개인 키로 모은
# 직접 집계라 공개 페이지에 싣지 않는다(ADR 0010 보완 · 2026-09-29).
PUBLIC_LAST = "16_13"
SPLIT = "15_13"
TOP = 10
Direction = Literal["nerf", "buff"]
WANTS: tuple[Direction, ...] = ("nerf", "buff")

SOURCES = {
    "solo": "u.gg 아카이브의 솔랭 지표(승률 · 픽률 · 밴율 · 판수) — world · emerald_plus",
    "pro": "Oracle's Elixir 프로 경기(프로 픽 · 밴율)",
    "answers": "공식 패치 노트(League of Legends Wiki)와 Data Dragon 으로 만든 정답지",
}
NOTES = [
    "점수(score)는 확률이 아니다. 한 패치 안에서 후보를 줄 세우는 값이다.",
    f"u.gg 패널 구간({PUBLIC_LAST} 패치까지)만 싣는다. 그 뒤 패치는 Riot API 개인 키로 "
    "모은 직접 집계라 공개하지 않는다(ADR 0010 · 0016).",
    "조정 효과는 같은 패치에서 조정되지 않은 챔피언의 평균 변화를 뺀 승률 변화다"
    "(run-effect 와 같은 계산). 측정이지 판정이 아니다.",
    "같은 버전 안에서는 칸을 더하기만 한다. 빼거나 뜻을 바꾸면 v2 로 낸다.",
]


# ── 모양 ────────────────────────────────────────────────────────────────


class _Model(BaseModel):
    # 모르는 칸이 들어오면 거절한다 — 모델과 파일이 어긋나면 계약 테스트가 걸린다
    model_config = ConfigDict(extra="forbid", frozen=True)


class Evidence(_Model):
    # 출처는 ADR 0007 의 다섯 값뿐이다 — 정해 두지 않은 출처(직접 집계 등)가 끼면 걸린다
    source: Source
    text: str


class Candidate(_Model):
    rank: int
    champion_id: int
    champion: str
    score: float
    matches: int
    evidence: list[Evidence]
    warnings: list[str]
    actual: str
    hit: bool


class Pair(_Model):
    nerf: int
    buff: int


class Hits(_Model):
    top5: Pair
    top10: Pair


class PatchView(_Model):
    id: str
    next: str
    candidates: int
    nerf: list[Candidate]
    buff: list[Candidate]
    hits: Hits


class PatchSummary(_Model):
    id: str
    next: str
    candidates: int


class PatchList(_Model):
    split: str
    patches: list[PatchSummary]


class HistoryRow(_Model):
    patch: str
    win_rate: float
    pick_rate: float
    ban_rate: float | None
    matches: int
    pro_presence: float | None
    adjusted: str


class Effect(_Model):
    patch: str
    next: str
    direction: Direction
    win_rate_before: float
    win_rate_after: float
    control_shift: float
    effect: float
    intended: bool
    closer: bool
    pro_before: float | None
    pro_after: float | None


class ChampionFile(_Model):
    champion_id: int
    champion: str
    ko: str | None
    history: list[HistoryRow]
    effects: list[Effect]


class ChampionEntry(_Model):
    champion_id: int
    champion: str
    ko: str | None
    patches: int
    adjustments: int


class ChampionList(_Model):
    champions: list[ChampionEntry]


class EffectGroup(_Model):
    direction: Direction
    n: int
    intended: int
    closer: int


class ControlGroup(_Model):
    closer: int
    total: int


class EffectSummary(_Model):
    method: str
    pairs: int
    groups: list[EffectGroup]
    control: ControlGroup


class Index(_Model):
    version: Literal["v1"]
    first: str
    last: str
    split: str
    sources: dict[str, str]
    notes: list[str]
    paths: list[str]


SCHEMAS: dict[str, type[_Model]] = {
    "index": Index,
    "patches": PatchList,
    "patch": PatchView,
    "champions": ChampionList,
    "champion": ChampionFile,
    "effects": EffectSummary,
}


def model_for(path: str) -> type[_Model]:
    """경로 → 그 응답의 모델. 모르는 경로면 멈춘다."""
    fixed: dict[str, type[_Model]] = {
        "index.json": Index,
        "patches.json": PatchList,
        "champions.json": ChampionList,
        "effects.json": EffectSummary,
    }
    if path in fixed:
        return fixed[path]
    if re.fullmatch(r"patches/\d{2}_\d{1,2}\.json", path):
        return PatchView
    if re.fullmatch(r"champions/\d+\.json", path):
        return ChampionFile
    raise ValueError(f"모르는 경로다: {path}")


# ── 후보 — 배포 페이지와 정적 API 가 같이 쓴다 ───────────────────────────


@dataclass(frozen=True)
class Pick:
    """후보 하나. 점수와 근거를 **한 번만** 계산해 두 출력이 나눠 쓴다."""

    row: PanelRow
    score: float
    notes: tuple[Note, ...]


def lifetime(rows: Sequence[PanelRow]) -> dict[str, float]:
    """챔피언별 통산 프로 픽 · 밴율. 20패치 넘게 있는 챔피언만."""
    seen: dict[str, list[float]] = {}
    for r in rows:
        if r.pro_presence is not None:
            seen.setdefault(r.champion, []).append(r.pro_presence)
    return {c: sum(v) / len(v) for c, v in seen.items() if len(v) >= 20}


def picks(
    rows: Sequence[PanelRow],
    patch: str,
    rules: Sequence[Rule],
    pro: Mapping[str, float],
    seed: int,
    top: int = TOP,
) -> dict[str, tuple[Pick, ...]]:
    """한 패치의 너프 · 버프 후보 상위 `top`. 학습은 그 패치 **앞**의 행만 쓴다."""
    test = tuple(r for r in rows if r.patch == patch)
    train = tuple(r for r in rows if r.patch_index < patch_index(patch))
    search = CaseSearch(direction_rows(tuple(rows)), patch)
    out: dict[str, tuple[Pick, ...]] = {}
    for want in WANTS:
        score = rank_candidates(train, test, want=want, seed=seed)
        out[want] = tuple(
            Pick(
                row=test[i],
                score=float(score[i]),
                notes=tuple(
                    reasons(
                        test[i],
                        test,
                        search.similar(test[i], k=25),
                        rules,
                        pro.get(test[i].champion),
                        want,
                    )
                ),
            )
            for i in np.argsort(-score)[:top]
        )
    return out


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _candidate(rank: int, pick: Pick, want: str) -> Candidate:
    r = pick.row
    return Candidate(
        rank=rank,
        champion_id=r.champion_id,
        champion=r.champion,
        score=round(pick.score, 4),
        matches=r.matches,
        # 출처 · 방향 값은 pydantic 이 만들 때 검사한다 — 정해 둔 값이 아니면 멈춘다
        evidence=[
            Evidence(source=cast(Source, n.source), text=n.text)
            for n in pick.notes
            if not n.warn
        ],
        warnings=[n.text for n in pick.notes if n.warn],
        actual=outcome(r),
        hit=bool(r.adjusted_next and r.direction_next == want),
    )


def patch_view(
    patch: str, candidates: int, got: Mapping[str, Sequence[Pick]]
) -> PatchView:
    lists = {
        want: [_candidate(i, p, want) for i, p in enumerate(got[want], 1)]
        for want in WANTS
    }

    def hits(k: int) -> Pair:
        return Pair(
            nerf=sum(c.hit for c in lists["nerf"][:k]),
            buff=sum(c.hit for c in lists["buff"][:k]),
        )

    return PatchView(
        id=patch,
        next=next_patch(patch) or "",
        candidates=candidates,
        nerf=lists["nerf"],
        buff=lists["buff"],
        hits=Hits(top5=hits(5), top10=hits(10)),
    )


# ── 챔피언 이력과 조정 효과 ─────────────────────────────────────────────


def _adjacent(
    rows: Sequence[PanelRow],
) -> Iterator[tuple[str, str, tuple[PanelRow, ...], tuple[PanelRow, ...]]]:
    """패치 순서표에서 **바로 붙은** 두 패치가 둘 다 있을 때만. `run-effect` 와 같다."""
    by_patch: dict[str, list[PanelRow]] = {}
    for r in rows:
        by_patch.setdefault(r.patch, []).append(r)
    for a, b in zip(PATCH_SEQUENCE, PATCH_SEQUENCE[1:], strict=False):
        if a in by_patch and b in by_patch:
            yield a, b, tuple(by_patch[a]), tuple(by_patch[b])


def _effect(o: Outcome, applied: str) -> Effect:
    return Effect(
        patch=o.patch,
        next=applied,
        direction=cast(Direction, o.direction),
        win_rate_before=round(o.before, 4),
        win_rate_after=round(o.after, 4),
        control_shift=round(o.baseline_shift, 4),
        effect=round(o.adjusted_shift, 4),
        intended=o.worked,
        closer=o.closer,
        pro_before=_r(o.pro_before),
        pro_after=_r(o.pro_after),
    )


def champion_files(
    rows: Sequence[PanelRow], names: Mapping[str, str]
) -> dict[int, ChampionFile]:
    """챔피언마다 패널 전 구간의 이력과, 조정마다 `effect.outcomes` 가 잰 효과."""
    effects: dict[tuple[str, str], Effect] = {}
    for _, applied, current, following in _adjacent(rows):
        for o in outcomes(current, following):
            effects[(o.patch, o.champion)] = _effect(o, applied)

    by_champion: dict[int, list[PanelRow]] = {}
    for r in rows:
        by_champion.setdefault(r.champion_id, []).append(r)

    out: dict[int, ChampionFile] = {}
    for cid, mine in by_champion.items():
        mine.sort(key=lambda r: r.patch_index)
        name = mine[-1].champion
        out[cid] = ChampionFile(
            champion_id=cid,
            champion=name,
            ko=names.get(name),
            history=[
                HistoryRow(
                    patch=r.patch,
                    win_rate=round(r.win_rate, 4),
                    pick_rate=round(r.pick_rate, 4),
                    ban_rate=_r(r.ban_rate),
                    matches=r.matches,
                    pro_presence=_r(r.pro_presence),
                    adjusted=outcome(r),
                )
                for r in mine
            ],
            effects=[
                effects[(r.patch, r.champion)]
                for r in mine
                if (r.patch, r.champion) in effects
            ],
        )
    return out


def effect_summary(rows: Sequence[PanelRow]) -> EffectSummary:
    """방향별로 의도대로 간 수와 5할에 가까워진 수, 그리고 대조군."""
    found: list[Outcome] = []
    closer = total = pairs = 0
    for _, _, current, following in _adjacent(rows):
        pairs += 1
        found.extend(outcomes(current, following))
        hit, n = balance_control(current, following)
        closer += hit
        total += n
    return EffectSummary(
        method="대조군(같은 패치에서 조정되지 않은 챔피언)의 평균 승률 변화를 뺀 변화. run-effect 와 같은 계산",
        pairs=pairs,
        groups=[
            EffectGroup(
                direction=want,
                n=sum(o.direction == want for o in found),
                intended=sum(o.direction == want and o.worked for o in found),
                closer=sum(o.direction == want and o.closer for o in found),
            )
            for want in WANTS
        ],
        control=ControlGroup(closer=closer, total=total),
    )


# ── 전부 만들기 · 공개 범위 · 쓰기 ─────────────────────────────────────


def build(
    rows: Sequence[PanelRow],
    rules: Sequence[Rule],
    names: Mapping[str, str],
    seed: int,
    split: str = SPLIT,
) -> dict[str, _Model]:
    """정적 API 전부. 경로 → 응답."""
    pro = lifetime(rows)
    order = sorted({r.patch for r in rows}, key=patch_index)
    evals = [p for p in order if patch_index(p) > patch_index(split)]

    files: dict[str, _Model] = {}
    summaries: list[PatchSummary] = []
    for patch in evals:
        count = sum(r.patch == patch for r in rows)
        view = patch_view(patch, count, picks(rows, patch, rules, pro, seed))
        files[f"patches/{patch}.json"] = view
        summaries.append(PatchSummary(id=patch, next=view.next, candidates=count))
    files["patches.json"] = PatchList(split=split, patches=summaries)

    champions = champion_files(rows, names)
    adjusted: dict[int, int] = {}
    for r in rows:
        if r.adjusted_next:
            adjusted[r.champion_id] = adjusted.get(r.champion_id, 0) + 1
    for cid in sorted(champions):
        files[f"champions/{cid}.json"] = champions[cid]
    files["champions.json"] = ChampionList(
        champions=[
            ChampionEntry(
                champion_id=c.champion_id,
                champion=c.champion,
                ko=c.ko,
                patches=len(c.history),
                adjustments=adjusted.get(c.champion_id, 0),
            )
            for c in sorted(champions.values(), key=lambda c: c.champion)
        ]
    )
    files["effects.json"] = effect_summary(rows)
    files["index.json"] = Index(
        version="v1",
        first=order[0],
        last=order[-1],
        split=split,
        sources=SOURCES,
        notes=NOTES,
        paths=sorted(files),
    )
    return files


# 글 속에 섞인 패치 번호도 잡는다(「16_16 패치에서 …」). 앞뒤가 숫자면 패치가 아니다
_PATCH = re.compile(r"(?<!\d)\d{2}_\d{1,2}(?!\d)")


def _key(patch: str) -> tuple[int, ...]:
    return tuple(int(x) for x in patch.split("_"))


def _walk(data: Any, key: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(data, dict):
        for k, v in data.items():
            yield from _walk(v, k)
    elif isinstance(data, list):
        for v in data:
            yield from _walk(v, key)
    else:
        yield key, data


def check_public_range(files: Mapping[str, Any]) -> None:
    """**공개 범위를 넘는 자료가 한 건이라도 있으면 멈춘다.**

    경로와 모든 글자 값에서 패치 번호를 찾는다. 패치는 `PUBLIC_LAST` 까지, 「다음
    패치」(`next`)는 그 정답이 나온 바로 다음 패치까지 허용한다. 직접 집계는 `16_16`
    부터라 섞이면 여기서 걸린다. 출처 칸은 모델이 정해 둔 값만 받는다(`Evidence`).
    """
    last = _key(PUBLIC_LAST)
    answer = _key(next_patch(PUBLIC_LAST) or PUBLIC_LAST)

    def fail(where: str, value: str) -> None:
        raise ValueError(
            f"공개 범위를 넘었다: {where} 의 {value} — u.gg 패널 구간({PUBLIC_LAST})까지만 "
            "싣는다. 직접 집계가 섞였을 수 있다"
        )

    for path, data in files.items():
        for token in _PATCH.findall(path):
            if _key(token) > last:
                fail(path, token)
        for key, value in _walk(data):
            if not isinstance(value, str):
                continue
            limit = answer if key == "next" else last
            for token in _PATCH.findall(value):
                if _key(token) > limit:
                    fail(path, token)


def render(files: Mapping[str, _Model]) -> dict[str, str]:
    """`api/v1/` 아래에 놓일 글 전부 — 경로 → 파일 내용. **공개 범위를 먼저 검사한다.**

    응답과 스키마를 같이 낸다. `write` 는 이것을 쓰고 `stale` 은 이것과 디스크를 견준다 —
    쓰는 쪽과 견주는 쪽이 같은 글을 봐야 「다시 만들어도 같다」가 뜻을 가진다.
    """
    dumped = {path: model.model_dump(mode="json") for path, model in files.items()}
    check_public_range(dumped)

    texts = {
        path: json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
        for path, data in sorted(dumped.items())
    }
    for name, model in SCHEMAS.items():
        schema = json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2)
        texts[f"schema/{name}.json"] = schema + "\n"
    return texts


def write(site: Path, files: Mapping[str, _Model]) -> list[Path]:
    """`site/api/v1/` 에 쓴다. **공개 범위를 먼저 검사한다.** 없어진 응답 · 스키마는 지운다."""
    texts = render(files)

    root = site / API_DIR
    wanted = {root / path for path in texts}
    if root.exists():
        for old in root.rglob("*.json"):
            if old not in wanted:
                old.unlink()

    written: list[Path] = []
    for path, text in texts.items():
        out = root / path
        out.parent.mkdir(parents=True, exist_ok=True)
        # 줄 끝을 바꾸지 않는다 — 어느 기계에서 만들어도 바이트가 같아야 한다
        out.write_bytes(text.encode("utf-8"))
        written.append(out)
    return written


def stale(site: Path, files: Mapping[str, _Model]) -> list[str]:
    """디스크의 `site/api/v1/` 이 지금 만들 것과 **바이트까지 같은지.** 다른 것을 낸다.

    **쓰지 않는다.** 검사가 고쳐 버리면 무엇이 달랐는지 남지 않고, 자료를 읽기 전용으로
    붙인 컨테이너에서도 돌아야 한다([extension 3단계](../../docs/extension.md)).

        다르다  내용이 다르다
        없다    만들어야 하는데 디스크에 없다
        남았다  디스크에 있는데 이제 만들지 않는다
    """
    texts = render(files)
    root = site / API_DIR
    on_disk = (
        {p.relative_to(root).as_posix() for p in root.rglob("*.json")}
        if root.exists()
        else set()
    )
    problems: list[str] = []
    for path, text in texts.items():
        if path not in on_disk:
            problems.append(f"없다: {path}")
        elif (root / path).read_bytes() != text.encode("utf-8"):
            problems.append(f"다르다: {path}")
    problems += [f"남았다: {path}" for path in sorted(on_disk - set(texts))]
    order = {"다르다": 0, "없다": 1, "남았다": 2}
    return sorted(problems, key=lambda line: (order[line.split(":")[0]], line))
