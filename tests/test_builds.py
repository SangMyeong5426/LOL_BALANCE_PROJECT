"""챔피언별 아이템 사용 테스트 — ADR 0011 의 정의를 그대로 본다."""

from __future__ import annotations

from typing import Any

from lol_balance.builds import (
    CORE_SHARE,
    MIN_GAMES,
    Usage,
    core_item_changes,
    evolved_forms,
    finished_items,
    item_usage,
)
from lol_balance.riot import Match, Origin, Pick

ORIGIN = Origin("EMERALD", "I", 0)
REAVER, BLADE, BOOTS, DAGGER = 3508, 3031, 3006, 1042
MANAMUNE, MURAMANA = 3004, 3042

ITEMS: dict[str, Any] = {
    str(REAVER): {"name": "Essence Reaver", "gold": {"total": 2900}},
    str(BLADE): {"name": "Infinity Edge", "gold": {"total": 3450}},
    str(BOOTS): {"name": "Berserker's Greaves", "gold": {"total": 1100}},
    str(DAGGER): {"name": "Dagger", "gold": {"total": 250}},
}
FINISHED = finished_items(ITEMS)


def game(n: int, builds: dict[int, tuple[int, ...]], start_ms: int = 1_000) -> Match:
    """챔피언 → 최종 아이템 일곱 칸."""
    picks = tuple(
        Pick(champion_id=c, team_id=100, position="", win=True, items=items)
        for c, items in builds.items()
    )
    return Match(
        match_id=f"KR_{n}",
        platform="kr",
        version="16.18.1.1",
        start_ms=start_ms,
        duration_s=1800,
        player=n,
        origin=ORIGIN,
        picks=picks,
        bans=(),
    )


def test_finished_items_use_the_gold_line() -> None:
    # 신발과 부품은 2,000 골드 아래라 빠진다
    assert FINISHED == frozenset({REAVER, BLADE})


def test_usage_counts_games_not_copies_and_skips_the_trinket() -> None:
    matches = [
        game(1, {236: (REAVER, REAVER, BOOTS, 0, 0, 0, 3340)}),
        game(2, {236: (BLADE, 0, 0, 0, 0, 0, REAVER)}),  # 장신구 칸의 것은 안 센다
    ]

    usage = item_usage(matches, FINISHED)[236]

    assert usage.games == 2
    assert usage.counts == {REAVER: 1, BLADE: 1}
    assert usage.share(REAVER) == 0.5
    assert usage.share(DAGGER) == 0.0


def test_usage_respects_the_release_boundary() -> None:
    matches = [
        game(1, {236: (REAVER, 0, 0, 0, 0, 0, 0)}, start_ms=1_000),
        game(2, {236: (BLADE, 0, 0, 0, 0, 0, 0)}, start_ms=5_000),
    ]

    usage = item_usage(matches, FINISHED, before_ms=5_000)[236]

    # 경계 시각에 시작한 경기는 이미 다음 패치가 공개된 뒤다
    assert (usage.games, usage.counts) == (1, {REAVER: 1})


def test_core_items_need_enough_games_and_share() -> None:
    few = Usage(236, MIN_GAMES - 1, {REAVER: MIN_GAMES - 1})
    assert not few.enough
    assert few.core() == ()  # 모른다 — 없다는 뜻이 아니다

    n = MIN_GAMES
    counts = {REAVER: n, BLADE: n // 2, 3072: n // 2, 3094: int(n * CORE_SHARE) - 1}
    usage = Usage(236, n, counts)

    # 상위 셋 — 같은 사용률이면 id 순. 넷째는 20% 미만이라 어차피 빠진다
    assert usage.top() == (REAVER, BLADE, 3072)  # BLADE(3031) 이 3072 보다 앞
    assert usage.core() == usage.top()


def test_core_share_line_cuts_rare_items() -> None:
    usage = Usage(236, 200, {REAVER: 150, BLADE: 39})  # 19.5%
    assert usage.core() == (REAVER,)


def test_empty_usage_has_no_share() -> None:
    assert Usage(236, 0, {}).share(REAVER) == 0.0


def test_previous_core_item_that_changed_is_flagged() -> None:
    previous = {
        236: Usage(236, 200, {REAVER: 150, BLADE: 90}),  # 둘 다 주요
        22: Usage(22, 200, {BLADE: 30}),  # 15% — 주요가 아니다
        51: Usage(51, 50, {REAVER: 50}),  # 표본 부족
    }
    after = {
        **ITEMS,
        str(REAVER): {"name": "Essence Reaver", "gold": {"total": 3200}},
    }

    flagged = core_item_changes(previous, ITEMS, after)

    assert [(c.champion_id, c.item_id) for c in flagged] == [(236, REAVER)]
    only = flagged[0]
    assert only.share == 0.75 and only.games == 200
    assert [(c.field, c.before, c.after) for c in only.changes] == [
        ("gold", 2900.0, 3200.0)
    ]


def test_nothing_changed_means_no_warning() -> None:
    previous = {236: Usage(236, 200, {REAVER: 150})}
    assert core_item_changes(previous, ITEMS, ITEMS) == []


def test_evolved_form_counts_as_its_base_item() -> None:
    """경기 끝에는 Muramana 만 남는다 — Manamune 을 쓴 판으로 세야 한다."""
    raw: dict[str, Any] = {
        str(MANAMUNE): {
            "name": "Manamune",
            "gold": {"total": 2900, "purchasable": True},
            "maps": {"11": True},
        },
        str(MURAMANA): {
            "name": "Muramana",
            "gold": {"total": 2900, "purchasable": False},
            "maps": {"11": True},
            "specialRecipe": MANAMUNE,
        },
    }
    back = evolved_forms(raw)
    assert back == {MURAMANA: MANAMUNE}

    finished = finished_items({str(MANAMUNE): raw[str(MANAMUNE)]})
    matches = [game(1, {81: (MURAMANA, 0, 0, 0, 0, 0, 0)})]

    assert item_usage(matches, finished)[81].counts == {}  # 되돌리기 전
    assert item_usage(matches, finished, evolved=back)[81].counts == {MANAMUNE: 1}
