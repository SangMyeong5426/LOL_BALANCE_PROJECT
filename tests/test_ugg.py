"""u.gg 응답 해독 테스트.

실제 수집물을 고정물로 커밋하지 않는다 — 원자료를 저장소에 두지 않는다는
규칙 때문이고, 응답이 100 KB 를 넘기도 한다. 대신 **같은 항등식을 만족하는
작은 합성 응답**을 만들어 쓴다.
"""

from __future__ import annotations

from typing import Any

import pytest

from patchlens.ugg import (
    check_games_identity,
    check_win_rate_identity,
    parse_champion_ranking,
)

ROLES = ("top", "jungle", "mid", "adc", "supp")
GAMES = 100


def _entry(champion_id: int, wins: int, matches: int) -> list[Any]:
    """[챔피언ID, 매치업, 승, 판, 딜, 골드, 킬, 데스, 어시, CS]"""
    return [str(champion_id), [[999, 0, 1]], wins, matches, 20000, 11000, 5, 5, 6, 200]


def make_payload() -> list[Any]:
    """역할마다 두 챔피언. 역할 합이 200판·100승이라 전체 승률이 정확히 50%다."""
    roles = {
        role: [_entry(i * 10 + 1, 66, 120), _entry(i * 10 + 2, 34, 80)]
        for i, role in enumerate(ROLES)
    }
    bans = {"11": 30, "12": 10, "-1": 5, "total_matches": 80}
    return [roles, bans, "2026-04-29T19:33:42Z", float(GAMES)]


def test_parses_every_row() -> None:
    r = parse_champion_ranking(make_payload())

    assert len(r.rows) == len(ROLES) * 2
    assert {row.role for row in r.rows} == set(ROLES)
    assert r.games == GAMES
    assert r.updated_at == "2026-04-29T19:33:42Z"


def test_win_rate_uses_wins_over_matches() -> None:
    r = parse_champion_ranking(make_payload())
    row = next(x for x in r.rows if x.champion_id == 1)

    assert row.wins == 66
    assert row.matches == 120
    assert row.win_rate == pytest.approx(0.55)


def test_ban_dictionary_drops_bookkeeping_keys() -> None:
    """`total_matches` 와 `-1`(밴 안 함)은 챔피언이 아니므로 밴 표에서 뺀다."""
    r = parse_champion_ranking(make_payload())

    assert set(r.bans) == {11, 12}
    assert r.ban_denominator == 80
    assert r.ban_rate(11) == pytest.approx(30 / 80)
    assert r.ban_rate(9999) == 0.0


def test_pick_rate_sums_to_one_within_a_role() -> None:
    r = parse_champion_ranking(make_payload())
    mid = [row for row in r.rows if row.role == "mid"]

    assert sum(r.pick_rate(row) for row in mid) == pytest.approx(1.0)


def test_identities_hold_on_a_well_formed_payload() -> None:
    r = parse_champion_ranking(make_payload())

    check_win_rate_identity(r)
    check_games_identity(r)


def test_win_rate_identity_catches_a_shifted_field() -> None:
    """자리를 하나 밀면 승/판 해석이 깨지고 50% 항등식이 먼저 잡아낸다."""
    payload = make_payload()
    for entry in payload[0]["mid"]:
        entry[2] = entry[3]  # 승 자리에 판수를 넣는다

    with pytest.raises(ValueError, match="50%"):
        check_win_rate_identity(parse_champion_ranking(payload))


def test_games_identity_catches_a_missing_role() -> None:
    payload = make_payload()
    del payload[0]["supp"]

    with pytest.raises(ValueError, match="10배"):
        check_games_identity(parse_champion_ranking(payload))


def test_short_payload_is_rejected() -> None:
    with pytest.raises(ValueError, match="짧다"):
        parse_champion_ranking([{}, {}])
