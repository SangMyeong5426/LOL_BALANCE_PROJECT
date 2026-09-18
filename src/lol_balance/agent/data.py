"""패널·노트·규칙을 한 번만 싣는다.

`scripts/ask` 가 하는 적재를 그대로 옮겼다 — 같은 입력에서 같은 근거·같은
베이스라인 점수가 나와야 비교가 된다.

시작 비용은 대부분 둘이다. 프로 경기 기록(약 4초)과 패치 노트 파싱(약 6초).
그래서 `load()` 를 한 번만 부르고 결과를 재사용한다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

from lol_balance.assemble import forecast_rows
from lol_balance.config import PROJECT_ROOT, load_settings
from lol_balance.items import Churn, churn_by_patch
from lol_balance.oracle import read_pro
from lol_balance.panel import PanelRow, patch_index
from lol_balance.patchnotes import ChangeBlock, champion_changes
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


@dataclass(frozen=True)
class Corpus:
    rows: tuple[PanelRow, ...]
    blocks: dict[str, list[ChangeBlock]]
    rules: tuple[Rule, ...]
    churn: dict[str, Churn]
    seed: int
    labeled: frozenset[str]
    """답(다음 패치의 조정 여부)이 있는 패치. 없는 것은 예측만 된다."""

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

    return Corpus(
        rows=rows,
        blocks=_note_blocks(),
        rules=read_rules(RULES) if RULES.exists() else (),
        churn=churn_by_patch(ITEMS) if ITEMS.is_dir() else {},
        seed=load_settings().seed,
        labeled=labeled,
    )
