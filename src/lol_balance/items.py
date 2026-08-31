"""아이템 수치 변경 — **정답지가 못 보는 조정.**

라벨은 패치 노트의 챔피언 절에서 온다. 그래서 **아이템으로 이뤄진 조정은
정답지에 아예 없다.** Lucian–Nami 가 `14_9` 24.0% 에서 `14_10` 6.0% 로 무너졌을
때 두 챔피언 모두 「조정 안 됨」이었고, 무너뜨린 것은 `Essence Reaver` 의 골드가
2900 에서 3200 으로 오른 것이었다.

이 모듈은 그 변경을 세어 **「이 패치는 아이템이 크게 바뀌었다」**를 말할 수 있게
한다. [CLAUDE.md](../../CLAUDE.md)가 도구의 경고 셋 중 하나로 적어 둔 것이다.

## 무엇을 못 하나

**어느 챔피언이 그 아이템을 사는지는 모른다.** 그것까지 있어야 「이 챔피언이
아이템 때문에 흔들렸다」를 말할 수 있는데, 챔피언별 구매율은 공개 아카이브에
커버리지 13.3% 로만 남아 있다([followups](../../docs/followups.md) 13번).

**그래서 경고는 패치 단위다.** 「이 패치의 승률 변화를 챔피언 조정으로만 읽지
마라」까지가 지금 말할 수 있는 전부다.

## 피처로는 안 쓴다 — **재 봤고 안 올랐다**

`A7p` 에 완성템 변경 수를 붙여 봤다. R-정확도가 0.2739 에서 0.2727 로 오히려
내려간다. **패치 단위 상수라 패치 안에서 아무것도 못 가르기 때문**이고, 이력
피처가 같은 자리에서 걸린 것과 같은 이유다.

다음 패치 조정 수와의 상관도 착시였다 — 피어슨 +0.393 인데 **스피어만 +0.017**
이고 시즌 시작 패치 둘을 빼면 +0.098 이다. 자세한 것은
[prediction-signals](../../docs/spec/prediction-signals.md#아이템-변동은-피처로-안-된다-2026-08-27).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lol_balance.panel import PATCH_SEQUENCE

# 소환사의 협곡. **다른 맵 전용 아이템을 섞지 않는다** — 아레나·ARAM 은 수치가
# 따로 놀고, 그것을 협곡 승률 옆에 두면 값이 튄다.
SUMMONERS_RIFT = "11"

# 이 아래는 부품이라 단독으로 빌드를 바꾸지 않는다. 완성템 경계를 정확히 가르는
# 필드가 Data Dragon 에 없어 골드로 근사한다.
FINISHED_GOLD = 2000


@dataclass(frozen=True)
class ItemChange:
    """아이템 하나의 값 하나가 바뀐 것."""

    item_id: str
    name: str
    field: str
    before: float | None
    after: float | None


def read_items(path: Path) -> dict[str, Any]:
    """`item.json` 한 장. 협곡에서 살 수 있는 것만 남긴다."""
    data = json.loads(path.read_bytes())["data"]
    return {
        key: entry
        for key, entry in data.items()
        if entry.get("maps", {}).get(SUMMONERS_RIFT)
        and entry.get("gold", {}).get("purchasable")
    }


def _values(entry: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {"gold": float(entry.get("gold", {}).get("total", 0))}
    for field, value in (entry.get("stats") or {}).items():
        if isinstance(value, int | float):
            out[field] = float(value)
    return out


def diff_items(before: dict[str, Any], after: dict[str, Any]) -> tuple[ItemChange, ...]:
    """두 버전 사이에 바뀐 값 전부.

    **새로 생기거나 사라진 아이템은 세지 않는다.** 그것은 값 변경이 아니라
    출시·삭제이고, 챔피언 쪽에서 신규 출시에 라벨을 안 붙이는 것과 같은 이유다.
    """
    out: list[ItemChange] = []
    for key in sorted(set(before) & set(after)):
        old, new = _values(before[key]), _values(after[key])
        name = after[key].get("name", key)
        for field in sorted(set(old) | set(new)):
            a, b = old.get(field), new.get(field)
            if a != b:
                out.append(ItemChange(key, name, field, a, b))
    return tuple(out)


@dataclass(frozen=True)
class Churn:
    """한 패치의 아이템 변동 규모."""

    items: int
    """값이 바뀐 아이템 수 (협곡 · 구매 가능)."""

    finished: int
    """그중 완성템 수. **빌드를 실제로 흔드는 것은 이쪽이다.**"""

    fields: int
    """바뀐 값의 총 개수."""

    total: int
    """그 패치의 협곡 구매 가능 아이템 수. 비율을 낼 분모다."""

    @property
    def share(self) -> float:
        return self.items / self.total if self.total else 0.0


def churn(before: dict[str, Any], after: dict[str, Any]) -> Churn:
    changes = diff_items(before, after)
    touched = {c.item_id for c in changes}
    finished = {
        c.item_id
        for c in changes
        if after[c.item_id].get("gold", {}).get("total", 0) >= FINISHED_GOLD
    }
    return Churn(len(touched), len(finished), len(changes), len(after))


# 이 이상이면 「크게 바뀌었다」로 본다. **73패치의 90% 분위가 8종**이고
# 중앙값은 1종이다. 문턱을 넘는 것은 11% 뿐이라 경고로서 뜻이 있다.
LOUD = 8


def churn_by_patch(directory: Path) -> dict[str, Churn]:
    """패치별 아이템 변동. 앞 패치 스냅샷이 없으면 그 패치는 빠진다.

    **첫 패치에는 값이 없다.** 비교할 앞이 없어서이고, 0 으로 채우면
    「아무것도 안 바뀌었다」가 되어 조용히 틀린다.
    """
    out: dict[str, Churn] = {}
    for index, patch in enumerate(PATCH_SEQUENCE):
        if index == 0:
            continue
        before = directory / f"{PATCH_SEQUENCE[index - 1].replace('_', '.')}.1.json"
        after = directory / f"{patch.replace('_', '.')}.1.json"
        if before.exists() and after.exists():
            out[patch] = churn(read_items(before), read_items(after))
    return out
