"""패널·노트·규칙을 한 번만 싣는다.

`scripts/ask` 가 하는 적재를 그대로 옮겼다 — 같은 입력에서 같은 근거·같은
베이스라인 점수가 나와야 비교가 된다.

시작 비용은 대부분 둘이다. 프로 경기 기록(약 4초)과 패치 노트 파싱(약 6초).
그래서 `load()` 를 한 번만 부르고 결과를 재사용한다.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path

from lol_balance.assemble import (
    adjusted_in,
    directions_in,
    forecast_rows,
    rows_from_ranking,
    version,
)
from lol_balance.config import PROJECT_ROOT, load_settings
from lol_balance.direction import Direction
from lol_balance.items import Churn, churn_by_patch
from lol_balance.oracle import read_pro
from lol_balance.panel import (
    PanelRow,
    champion_names,
    next_patch,
    patch_index,
)
from lol_balance.patchnotes import ChangeBlock, champion_changes
from lol_balance.riot import ranking, read_games
from lol_balance.rules import Rule, read_rules
from lol_balance.store import read_panel

DATA = PROJECT_ROOT / "data"
PANEL = DATA / "panel.sqlite"
NOTES = DATA / "patchnotes"
RANKING = DATA / "ugg" / "champion_ranking"
DDRAGON = DATA / "ddragon"
ORACLE = DATA / "oracle"
ITEMS = DATA / "items"
RULES = PROJECT_ROOT / "rules" / "proposed.jsonl"
RIOT = DATA / "riot"
CDRAGON = DATA / "cdragon"
LABELS = PROJECT_ROOT / "ground_truth" / "directions"


@dataclass(frozen=True)
class Corpus:
    rows: tuple[PanelRow, ...]
    blocks: dict[str, list[ChangeBlock]]
    rules: tuple[Rule, ...]
    churn: dict[str, Churn]
    seed: int
    labeled: frozenset[str]
    """답(다음 패치의 조정 여부)이 있는 패치. 없는 것은 예측만 된다."""
    direct: frozenset[str] = frozenset()
    """**우리가 직접 모은 경기로 만든** 패치. u.gg 가 끊긴 `16_15` 뒤가 여기 온다.

    학습은 u.gg 구간이고 이 패치들만 직접 집계다 — **출처가 섞인다**([ADR 0012](
    ../../../docs/adr/0012-predicting-with-direct-aggregation.md)).
    """

    @property
    def patches(self) -> list[str]:
        """지표가 있는 패치. 최신이 앞이다."""
        return sorted({r.patch for r in self.rows}, key=patch_index, reverse=True)

    def champions(self, patch: str) -> list[str]:
        return sorted(r.champion for r in self.rows if r.patch == patch)

    def row(self, champion: str, patch: str) -> PanelRow | None:
        return next(
            (r for r in self.rows if r.champion == champion and r.patch == patch), None
        )


def lifetime_pro(rows: tuple[PanelRow, ...], champion: str) -> float | None:
    """통산 프로 픽·밴율. `scripts/ask` 와 같은 정의다.

    **이것을 None 으로 넘기면 「버프하면 대회 출전이 크게 오른다」 경고가
    절대 뜨지 않는다.** 자주 나오는 챔피언인지를 여기서 가른다.
    """
    seen = [r.pro_presence for r in rows if r.champion == champion and r.pro_presence]
    return sum(seen) / len(seen) if len(seen) >= 20 else None


def _note_blocks() -> dict[str, list[ChangeBlock]]:
    """`16.15.1.html` → `16_15`. 파일 이름이 곧 그 패치에 들어간 변경이다."""
    blocks: dict[str, list[ChangeBlock]] = defaultdict(list)
    for path in sorted(NOTES.glob("*.html")):
        patch = path.stem.rsplit(".", 1)[0].replace(".", "_")
        blocks[patch].extend(champion_changes(path.read_bytes()))
    return dict(blocks)


def available() -> bool:
    """패널이 있는가. **clone 직후에는 없는 것이 정상이다** — 원자료를 커밋하지 않는다."""
    return PANEL.exists()


def _note_dir(patch: str) -> Path:
    """그 패치 노트가 있는 폴더. 우리 구간 밖은 `live/` 에 있다."""
    return NOTES if (NOTES / f"{version(patch)}.html").exists() else NOTES / "live"


def _answers_for(patch: str) -> tuple[frozenset[str], dict[str, tuple[Direction, str]]]:
    """**다음 패치 노트에서 그 패치의 답을 읽는다.**

    안 읽으면 전 챔피언이 조용히 「조정 안 됨」이 되고, 화면은 「답이 없다」고
    말한다 — `16_17` 은 `16_18` 노트가 있는데도 채점을 못 했다(2026-09-22).
    `run-live-predict` 와 같은 경로다.
    """
    nxt = next_patch(patch)
    if nxt is None or not (_note_dir(nxt) / f"{version(nxt)}.html").is_file():
        return frozenset(), {}
    notes = _note_dir(nxt)
    adjusted = adjusted_in(nxt, notes)
    try:
        directions = directions_in(
            nxt,
            patch,
            ddragon=DDRAGON,
            cdragon=CDRAGON,
            notes=notes,
            labels=LABELS,
            previous_notes=_note_dir(patch),
        )
    except (FileNotFoundError, KeyError):
        directions = {}
    return adjusted, directions


@cache
def _names_for(patch: str) -> dict[int, str]:
    """그 패치의 Data Dragon 이름. 없으면 가장 최근 것."""
    want = DDRAGON / f"{patch.replace('_', '.')}.1.json"
    if not want.is_file():
        have = sorted(
            (x for x in DDRAGON.glob("*.json") if x.stem[:1].isdigit()),
            key=lambda x: tuple(int(n) for n in x.stem.split(".")),
        )
        if not have:
            return {}
        want = have[-1]
    return champion_names(json.loads(want.read_text())["data"])


@lru_cache(maxsize=1)
def load() -> Corpus:
    rows = read_panel(PANEL)
    labeled = frozenset(r.patch for r in rows)

    # **패널의 마지막 패치 다음도 지표는 있다.** 라벨을 못 만들어 패널에서
    # 빠진 것일 뿐이다 — `ask` 와 `predict` 가 같은 처리를 한다.
    covered = {r.patch for r in rows}
    pro = read_pro(ORACLE) if ORACLE.is_dir() else None
    for path in sorted(RANKING.glob("*.json"), key=lambda p: patch_index(p.stem)):
        if path.stem not in covered and patch_index(path.stem) > max(
            patch_index(p) for p in covered
        ):
            rows = rows + forecast_rows(
                path.stem, rows, ranking=RANKING, ddragon=DDRAGON, pro=pro
            )

    # **u.gg 가 끊긴 뒤는 우리가 모은 경기로 만든다.** 화면이 최신 패치를 못 보면
    # 이 저장소의 가장 최근 성과가 화면에 안 나온다(ADR 0010 · 0012).
    direct: set[str] = set()
    if RIOT.is_dir():
        have = {r.patch for r in rows}
        newest = max(patch_index(p) for p in have)
        folders = sorted(
            (x for x in RIOT.iterdir() if x.is_dir() and x.name[:1].isdigit()),
            key=lambda x: patch_index(x.name),
        )
        for folder in folders:
            if patch_index(folder.name) <= newest or not any(folder.glob("*.jsonl")):
                continue
            games = read_games(folder)
            if not games:
                continue
            adjusted, directions = _answers_for(folder.name)
            rows = rows + rows_from_ranking(
                folder.name,
                ranking(games),
                _names_for(folder.name),
                adjusted=adjusted,
                known=rows,
                pro=dict(pro[folder.name.replace("_", ".")])
                if pro and folder.name.replace("_", ".") in pro
                else None,
                directions=directions,
            )
            direct.add(folder.name)
            if adjusted:
                labeled = labeled | {folder.name}
            newest = patch_index(folder.name)

    return Corpus(
        rows=rows,
        direct=frozenset(direct),
        blocks=_note_blocks(),
        rules=read_rules(RULES) if RULES.exists() else (),
        churn=churn_by_patch(ITEMS) if ITEMS.is_dir() else {},
        seed=load_settings().seed,
        labeled=labeled,
    )
