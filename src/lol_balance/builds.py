"""챔피언별 아이템 사용 — **최종 아이템으로 센다**(ADR 0011).

패치 단위 아이템 경고(`items.churn`)는 「어느 챔피언이 그 아이템을 사는가」를
몰라서 패치 단위에 머물렀다. 직접 집계(ADR 0010)가 선수마다 최종 아이템을
남기므로 이제 챔피언 단위로 센다.

**구매 이력이 아니다.** 경기가 끝났을 때 인벤토리에 있던 것이라 판 것 · 다 쓴 것은
안 보인다. 그래서 「사용률」이지 「구매율」이 아니다.

정의는 전부 **데이터를 보기 전에** ADR 0011 에 적었다.

| | |
| --- | --- |
| 완성템 | 협곡 · 구매 가능 · 총 골드 2,000 이상 (`items.FINISHED_GOLD`) |
| 썼다 | 최종 아이템 여섯 칸(장신구 칸 제외)에 있다 |
| 주요 아이템 | 사용률 상위 셋 중 20% 이상 |
| 표본 기준 | 그 패치에서 100판 이상인 챔피언만 |

**사용률이 높은 아이템의 승률이 높다고 그 아이템이 이기게 했다고 말하지 않는다.**
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from lol_balance.items import FINISHED_GOLD, ItemChange, diff_items
from lol_balance.riot import Match

# 최종 아이템 일곱 칸 중 마지막이 장신구다. 빌드가 아니라 시야 도구라 뺀다.
TRINKET_SLOT = 6

# 주요 아이템 — 사용률 상위 셋 중 이만큼 이상인 것. 재기 전에 정했다(ADR 0011).
CORE_COUNT = 3
CORE_SHARE = 0.20

# 이보다 적게 나온 챔피언은 주요 아이템을 말하지 않는다. 사용률 20% 근처에서
# 95% 구간이 ±8%p 안쪽이 되는 크기다.
MIN_GAMES = 100


def finished_items(items: Mapping[str, Any]) -> frozenset[int]:
    """`items.read_items` 의 결과(협곡 · 구매 가능)에서 완성템 id 만."""
    return frozenset(
        int(key)
        for key, entry in items.items()
        if entry.get("gold", {}).get("total", 0) >= FINISHED_GOLD
    )


@dataclass(frozen=True)
class Usage:
    """한 패치에서 챔피언 하나의 완성템 사용."""

    champion_id: int
    games: int
    counts: Mapping[int, int]

    @property
    def enough(self) -> bool:
        """주요 아이템을 말할 만큼 나왔나. 아니면 「표본 부족」이다."""
        return self.games >= MIN_GAMES

    def share(self, item_id: int) -> float:
        """아이템 사용률 — 이 챔피언 판 중 그 완성템을 쓴 판의 비율."""
        return self.counts.get(item_id, 0) / self.games if self.games else 0.0

    def top(self, n: int = CORE_COUNT) -> tuple[int, ...]:
        """사용률 순 상위 `n`. 같으면 id 순이라 매번 같은 답이 나온다."""
        ranked = sorted(self.counts, key=lambda i: (-self.counts[i], i))
        return tuple(ranked[:n])

    def core(self) -> tuple[int, ...]:
        """주요 아이템. **표본이 모자라면 빈 값이다** — 모른다는 뜻이지 없다는 뜻이 아니다."""
        if not self.enough:
            return ()
        return tuple(i for i in self.top() if self.share(i) >= CORE_SHARE)


def item_usage(
    matches: Iterable[Match],
    finished: frozenset[int],
    before_ms: int | None = None,
) -> dict[int, Usage]:
    """챔피언마다 완성템 사용을 센다.

    `before_ms` 를 주면 **그 시각 전에 시작한 경기만** 센다 — 다음 패치 출시일
    경계다. 예측하는 날 없던 경기를 쓰지 않으려는 것이다.

    한 선수가 같은 완성템을 두 개 가져도 한 번으로 센다. 사용률은 「그 아이템을 쓴
    판」의 비율이다.
    """
    games: Counter[int] = Counter()
    counts: defaultdict[int, Counter[int]] = defaultdict(Counter)
    for m in matches:
        if before_ms is not None and m.start_ms >= before_ms:
            continue
        for p in m.picks:
            games[p.champion_id] += 1
            for item in set(p.items[:TRINKET_SLOT]) & finished:
                counts[p.champion_id][item] += 1
    return {c: Usage(c, n, dict(counts[c])) for c, n in games.items()}


@dataclass(frozen=True)
class CoreItemChange:
    """앞 패치의 주요 아이템이 이번 패치에 바뀌었다."""

    champion_id: int
    item_id: int
    share: float
    """**앞 패치의** 사용률 — 바뀌기 전에 얼마나 기대고 있었나."""
    games: int
    """앞 패치에서 그 챔피언의 판수."""
    changes: tuple[ItemChange, ...]


def core_item_changes(
    previous: Mapping[int, Usage],
    items_before: Mapping[str, Any],
    items_after: Mapping[str, Any],
) -> list[CoreItemChange]:
    """앞 패치(`t−1`)의 주요 아이템 중 `t−1` → `t` 사이에 바뀐 것.

    **이번 패치의 주요 아이템으로 보지 않는다.** 너프된 아이템은 바로 덜 사게 되어
    주요 아이템에서 빠질 수 있다 — 흔들린 챔피언일수록 경고가 사라진다.

    바뀐 값은 `items.diff_items` 가 잡는 골드와 스탯이다. 효과 문구만 바뀐 조정은
    안 잡힌다.
    """
    by_item: defaultdict[int, list[ItemChange]] = defaultdict(list)
    for change in diff_items(dict(items_before), dict(items_after)):
        by_item[int(change.item_id)].append(change)
    out = [
        CoreItemChange(
            champion_id=usage.champion_id,
            item_id=item,
            share=usage.share(item),
            games=usage.games,
            changes=tuple(by_item[item]),
        )
        for usage in previous.values()
        for item in usage.core()
        if item in by_item
    ]
    return sorted(out, key=lambda c: (-c.share, c.champion_id, c.item_id))
