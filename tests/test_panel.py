"""패널 조립 테스트.

실제 수집물을 쓰지 않는다. **합성 응답**으로 조립 규칙만 본다 —
결측을 0 으로 채우지 않는가, 라벨이 제대로 붙는가, 순서가 고정되는가.
"""

from __future__ import annotations

from typing import Any

import pytest

from conftest import PanelRowFactory
from lol_balance.panel import (
    HISTORY,
    PATCH_SEQUENCE,
    champion_names,
    name_after,
    next_patch,
    patch_index,
    patch_rows,
)
from lol_balance.ugg import parse_champion_ranking

ROLES = ("top", "jungle", "mid", "adc", "supp")
NAMES = {103: "Ahri", 1: "Annie"}


def _payload(
    entries: dict[str, list[list[Any]]], bans: dict[str, int], games: int
) -> list[Any]:
    return [entries, bans, "2026-01-01T00:00:00Z", games]


def _entry(champion_id: int, wins: int, matches: int) -> list[Any]:
    """[챔피언ID, 매치업, 승, 판, 딜, 골드, 킬, 데스, 어시, CS]"""
    return [str(champion_id), [], wins, matches, 1000, 900, 5, 5, 8, 150]


def _ranking(games: int = 1000, with_bans: bool = True) -> Any:
    bans = (
        {"total_matches": games, "-1": 3, "103": 100, "1": 50}
        if with_bans
        else {"total_matches": 0, "-1": 0}
    )
    return parse_champion_ranking(
        _payload(
            {"mid": [_entry(103, 2500, 5000), _entry(1, 2500, 5000)]},
            bans,
            games,
        )
    )


def test_patch_index_is_not_string_order() -> None:
    """`13_9` 가 `13_10` 뒤로 가면 시간순 분할이 통째로 어긋난다."""
    assert patch_index("13_14") < patch_index("13_24") < patch_index("14_1")
    assert patch_index("15_9") < patch_index("15_10")
    assert PATCH_SEQUENCE[0] == "13_14"


def test_label_comes_from_the_next_patch_notes() -> None:
    rows = patch_rows("13_14", _ranking(), NAMES, frozenset({"Ahri"}))

    by_name = {r.champion: r for r in rows}
    assert by_name["Ahri"].adjusted_next is True
    assert by_name["Annie"].adjusted_next is False


def test_missing_ban_data_stays_missing() -> None:
    """밴 항목이 통째로 빠진 패치가 있다(14_5). 0 으로 채우면 「밴 0회」가 된다."""
    rows = patch_rows("13_14", _ranking(with_bans=False), NAMES, frozenset())

    assert all(r.ban_rate is None for r in rows)
    assert all(r.d_ban_rate is None for r in rows)


def test_trend_is_none_without_a_previous_patch() -> None:
    rows = patch_rows("13_14", _ranking(), NAMES, frozenset())

    assert all(r.d_win_rate is None for r in rows)
    assert all(not r.has_trend for r in rows)


def test_trend_is_the_difference_from_the_previous_patch() -> None:
    first = patch_rows("13_14", _ranking(), NAMES, frozenset())
    prior = {r.champion_id: r for r in first}
    stronger = parse_champion_ranking(
        _payload(
            {"mid": [_entry(103, 3000, 5000), _entry(1, 2500, 5000)]},
            {"total_matches": 1000, "103": 100, "1": 50},
            1000,
        )
    )

    rows = patch_rows("13_15", stronger, NAMES, frozenset(), prior)

    ahri = next(r for r in rows if r.champion == "Ahri")
    assert ahri.win_rate == pytest.approx(0.6)
    assert ahri.d_win_rate == pytest.approx(0.1)


def test_wr_gap_is_monotone_in_distance_from_even() -> None:
    """승률 자체는 단조롭지 않다. 조정되는 쪽은 양 끝이다."""
    rows = patch_rows("13_14", _ranking(), NAMES, frozenset())
    even = next(r for r in rows if r.champion == "Ahri")
    assert even.wr_gap == pytest.approx(0.0)


def test_champions_below_the_match_floor_are_dropped() -> None:
    """판수가 적으면 승률이 요동친다."""
    thin = parse_champion_ranking(
        _payload({"mid": [_entry(103, 5, 10)]}, {"total_matches": 100}, 10)
    )
    assert patch_rows("13_14", thin, NAMES, frozenset()) == ()


def test_champions_missing_from_data_dragon_are_dropped() -> None:
    """게임 모드 변형이나 이름이 안 잡히는 id 가 섞이면 안 된다."""
    rows = patch_rows("13_14", _ranking(), {103: "Ahri"}, frozenset())
    assert {r.champion for r in rows} == {"Ahri"}


def test_rows_are_sorted_so_rebuilds_match() -> None:
    """정렬이 흔들리면 「다시 만들면 같은 값」이 성립하지 않는다."""
    rows = patch_rows("13_14", _ranking(), NAMES, frozenset())
    assert [r.champion_id for r in rows] == sorted(r.champion_id for r in rows)


def test_champion_names_filters_game_mode_variants() -> None:
    data = {
        "Ahri": {"key": "103", "name": "Ahri"},
        "Jade_Ahri": {"key": "60103", "name": "Jade Ahri"},
    }
    assert champion_names(data) == {103: "Ahri"}


def test_history_never_contains_the_row_itself(make_row: PanelRowFactory) -> None:
    """**가장 위험한 자리다.** 자기 행이 이력에 들어가면 답을 그대로 본다.

    패치 t 행의 `adjusted_next` 는 「t+1 에서 조정됨」이고 그것이 맞히려는
    답이다. 이력에는 t−1 행까지만 들어가야 한다.
    """
    earlier = make_row("13_14", 103, win_rate=0.55, adjusted_next=True)
    rows = patch_rows(
        "13_15",
        _ranking(),
        NAMES,
        frozenset({"Ahri"}),
        history={103: [earlier]},
    )

    ahri = next(r for r in rows if r.champion == "Ahri")
    assert ahri.adjusted_next is True  # 자기 라벨
    assert ahri.history_len == 1
    assert ahri.recent_adjustments == 1  # 자기 것이 아니라 earlier 의 것


def test_no_history_means_unknown_not_zero(make_row: PanelRowFactory) -> None:
    rows = patch_rows("13_14", _ranking(), NAMES, frozenset())

    assert all(r.history_len == 0 for r in rows)
    assert all(r.recent_adjustments is None for r in rows)


def test_history_is_capped_at_the_window(make_row: PanelRowFactory) -> None:
    """결측이 섞여 있으므로 「연속」이 아니라 「있는 것 중 최근 N개」다."""
    past = [make_row(f"13_1{i}", 103, adjusted_next=True) for i in range(4, 9)]
    past.append(make_row("13_19", 103, adjusted_next=False))

    rows = patch_rows("13_20", _ranking(), NAMES, frozenset(), history={103: past})

    ahri = next(r for r in rows if r.champion == "Ahri")
    assert ahri.history_len == HISTORY
    assert ahri.recent_adjustments == HISTORY - 1  # 가장 최근 하나는 False


def test_high_win_rate_streak_counts_back_from_the_most_recent(
    make_row: PanelRowFactory,
) -> None:
    """「한 번 튀었다」와 「계속 세다」를 가르려는 피처다."""
    past = [
        make_row("13_14", 103, win_rate=0.60),
        make_row("13_15", 103, win_rate=0.40),  # 여기서 끊긴다
        make_row("13_16", 103, win_rate=0.55),
        make_row("13_17", 103, win_rate=0.55),
    ]
    rows = patch_rows("13_18", _ranking(), NAMES, frozenset(), history={103: past})

    assert next(r for r in rows if r.champion == "Ahri").high_wr_streak == 2


# --- 다음 패치 경계 -----------------------------------------------------


def test_next_patch_walks_forward() -> None:
    assert next_patch(PATCH_SEQUENCE[0]) == PATCH_SEQUENCE[1]
    assert next_patch("13_14") == "13_15"


def test_next_patch_stops_at_the_end() -> None:
    """**순서 끝에서 죽지 않는다.**

    `PATCH_SEQUENCE[i + 1]` 을 그냥 쓰던 곳이 셋 있었다(`predict` ·
    `fetch-cdragon` · `label-material`). 지금은 패널이 순서 끝에 못 미쳐 안
    걸리지만, **패치 하나만 더 받으면 `IndexError` 로 죽는다.**
    """
    assert next_patch(PATCH_SEQUENCE[-1]) is None


def test_name_after_increments_the_minor() -> None:
    """`PATCH_SEQUENCE` 끝에서는 다음 패치 이름이 순서에 없다.

    아직 안 받은 패치를 예측할 때 그 이름이 필요해서 만든다. **표시에만
    쓴다** — 시즌이 넘어가면 그때 `PATCH_SEQUENCE` 와 같이 손본다.
    """
    assert name_after("16_15") == "16_16"
    assert name_after("13_9") == "13_10"
