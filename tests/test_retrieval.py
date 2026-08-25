"""검색기 테스트.

**가장 중요한 것은 경계다.** 에이전트가 예측 대상 패치 이후를 읽으면 정답을
그냥 본 것이 된다. 프롬프트 지시가 아니라 도구가 물리적으로 막아야 한다.
"""

from __future__ import annotations

import pytest

from conftest import PanelRowFactory
from lol_balance.panel import PanelRow
from lol_balance.patchnotes import ChangeBlock
from lol_balance.retrieval import CaseSearch, NoteSearch, StatLookup


@pytest.fixture
def rows(make_row: PanelRowFactory) -> tuple[PanelRow, ...]:
    return (
        make_row(
            "13_14",
            1,
            champion="Ahri",
            win_rate=0.53,
            pick_rate=0.09,
            adjusted_next=True,
            direction_next="nerf",
        ),
        make_row(
            "13_15",
            2,
            champion="Zed",
            win_rate=0.46,
            pick_rate=0.02,
            adjusted_next=True,
            direction_next="buff",
        ),
        make_row("15_1", 3, champion="Lux", win_rate=0.53, pick_rate=0.09),
        make_row("16_1", 4, champion="Jinx", win_rate=0.50, pick_rate=0.05),
    )


# --- 경계 -------------------------------------------------------------


def test_case_search_cannot_reach_past_the_boundary(rows: tuple[PanelRow, ...]) -> None:
    search = CaseSearch(rows, as_of="15_1")

    assert {r.patch for r in search.pool} == {"13_14", "13_15"}


def test_stat_lookup_cannot_reach_past_the_boundary(rows: tuple[PanelRow, ...]) -> None:
    lookup = StatLookup(rows, as_of="15_1")

    assert lookup.champion("Lux") == ()
    assert lookup.patch("16_1") == ()
    assert len(lookup.champion("Ahri")) == 1


def test_note_search_cannot_reach_past_the_boundary() -> None:
    blocks = {
        "13_14": (ChangeBlock("Ahri", "Q", "Q", ("Base damage reduced.",)),),
        "16_1": (ChangeBlock("Ahri", "Q", "Q", ("Base damage reduced.",)),),
    }
    search = NoteSearch(blocks, as_of="15_1")

    assert {patch for patch, _, _ in search.search("damage", k=10)} == {"13_14"}


def test_the_boundary_is_fixed_at_construction(rows: tuple[PanelRow, ...]) -> None:
    """부르는 쪽이 경계를 넘기지 않는다 — 도구가 태어날 때 박힌다."""
    search = CaseSearch(rows, as_of="13_15")

    target = next(r for r in rows if r.champion == "Lux")
    assert {c.row.patch for c in search.similar(target, k=10)} == {"13_14"}


# --- 사례 검색 --------------------------------------------------------


def test_similar_cases_come_back_nearest_first(
    rows: tuple, make_row: PanelRowFactory
) -> None:
    search = CaseSearch(rows, as_of="16_1")
    target = make_row("16_1", 9, champion="New", win_rate=0.53, pick_rate=0.09)

    found = search.similar(target, k=3)

    assert found[0].row.champion == "Ahri"  # 같은 승률·픽률
    assert found[0].distance <= found[1].distance


def test_a_case_carries_what_happened_next(
    rows: tuple, make_row: PanelRowFactory
) -> None:
    """「비슷했던 챔피언에게 무슨 일이 있었나」가 이 도구의 요점이다."""
    search = CaseSearch(rows, as_of="16_1")
    target = make_row("16_1", 9, champion="New", win_rate=0.53, pick_rate=0.09)

    assert search.similar(target, k=1)[0].outcome == "nerf"


def test_a_champion_never_matches_its_own_row(rows: tuple[PanelRow, ...]) -> None:
    search = CaseSearch(rows, as_of="16_1")
    target = next(r for r in rows if r.champion == "Ahri")

    assert all(c.row.champion != "Ahri" for c in search.similar(target, k=5))


def test_rows_with_no_comparable_feature_are_skipped(make_row: PanelRowFactory) -> None:
    """결측을 0 으로 채우면 「평균값이었다」가 되어 엉뚱한 사례가 가까워진다."""
    pool = (make_row("13_14", 1, ban_rate=None, win_rate=0.5, pick_rate=0.05),)
    search = CaseSearch(pool, as_of="15_1")
    target = make_row("15_1", 2, ban_rate=0.1, win_rate=0.5, pick_rate=0.05)

    # 승률·픽률·wr_gap 은 맞대 볼 수 있으므로 거리가 나온다
    assert len(search.similar(target, k=5)) == 1


# --- 노트 검색 --------------------------------------------------------


def test_note_search_ranks_by_relevance() -> None:
    blocks = {
        "13_14": (
            ChangeBlock("Ahri", "Q", "Q", ("Ahri base damage reduced heavily.",)),
            ChangeBlock("Zed", "W", "W", ("Cooldown increased.",)),
        )
    }
    found = NoteSearch(blocks, as_of="15_1").search("ahri damage", k=2)

    assert found[0][1].champion == "Ahri"


def test_note_search_returns_nothing_when_no_term_matches() -> None:
    blocks = {"13_14": (ChangeBlock("Ahri", "Q", "Q", ("Cooldown increased.",)),)}

    assert NoteSearch(blocks, as_of="15_1").search("mana") == ()


# --- 수치 조회 --------------------------------------------------------


def test_stat_lookup_returns_a_champion_in_time_order(
    rows: tuple, make_row: PanelRowFactory
) -> None:
    extra = (*rows, make_row("14_1", 1, champion="Ahri", win_rate=0.51))
    lookup = StatLookup(extra, as_of="15_1")

    assert [r.patch for r in lookup.champion("Ahri")] == ["13_14", "14_1"]


def test_stat_lookup_orders_a_patch_by_pick_rate(make_row: PanelRowFactory) -> None:
    pool = (
        make_row("13_14", 1, champion="Low", pick_rate=0.01),
        make_row("13_14", 2, champion="High", pick_rate=0.20),
    )
    lookup = StatLookup(pool, as_of="15_1")

    assert [r.champion for r in lookup.patch("13_14")] == ["High", "Low"]
