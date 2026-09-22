"""프로 경기 기록 읽기 테스트.

**네트워크도 실제 CSV 도 쓰지 않는다.** 319 MB 를 읽는 대신 같은 모양의 작은
파일을 만들어 판단만 본다 — 패치 표기를 정규화하는가, 픽과 밴을 가르는가,
무엇으로 나누는가.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from lol_balance.oracle import (
    BAN_SLOTS,
    before_release,
    normalise,
    rates,
    read_games,
    read_pro,
    recent,
)

COLUMNS = ["gameid", "patch", "position", "champion"] + [
    f"ban{i}" for i in range(1, BAN_SLOTS + 1)
]


def write(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in COLUMNS})


def player(game: str, patch: str, champion: str) -> dict[str, str]:
    return {"gameid": game, "patch": patch, "position": "mid", "champion": champion}


def team(game: str, patch: str, *bans: str) -> dict[str, str]:
    row = {"gameid": game, "patch": patch, "position": "team", "champion": ""}
    for i, name in enumerate(bans, start=1):
        row[f"ban{i}"] = name
    return row


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("14.01", "14.1"),
        ("15.09", "15.9"),
        ("13.14", "13.14"),
        (" 16.1 ", "16.1"),
        ("preseason", "preseason"),
        ("", ""),
    ],
)
def test_zero_padded_minors_are_normalised(raw: str, expected: str) -> None:
    """**Oracle's 는 한 자리 마이너를 0으로 채운다.**

    정규화하지 않으면 74패치 중 29개가 「프로 경기 없음」으로 잘못 잡힌다.
    숫자로 못 읽으면 원문을 그대로 둔다 — 조용히 바꾸지 않는다.
    """
    assert normalise(raw) == expected


def test_picks_come_from_player_rows_and_bans_from_team_rows(tmp_path: Path) -> None:
    """**한 경기가 12줄이다** — 프로 선수 10 + 팀 2.

    안 가르면 팀 줄의 빈 `champion` 이 픽으로 세어지거나 밴이 두 번 세어진다.
    """
    write(
        tmp_path / "2025.csv",
        [
            player("g1", "15.09", "Ahri"),
            player("g1", "15.09", "Jax"),
            team("g1", "15.09", "Yone", "Ahri"),
        ],
    )

    pro = read_pro(tmp_path)["15.9"]

    assert pro["Ahri"].pick_rate == 1.0
    assert pro["Ahri"].ban_rate == 1.0
    assert pro["Yone"].pick_rate == 0.0
    assert pro["Yone"].ban_rate == 1.0


def test_rates_divide_by_games_not_rows(tmp_path: Path) -> None:
    """**비율로 둔다.** 패치마다 경기 수가 27~800 으로 달라서, 횟수를 그대로
    쓰면 「그 패치에 경기가 많았다」가 신호로 섞인다."""
    write(
        tmp_path / "2025.csv",
        [
            player("g1", "15.9", "Ahri"),
            player("g2", "15.9", "Ahri"),
            player("g3", "15.9", "Jax"),
            player("g4", "15.9", "Jax"),
        ],
    )

    pro = read_pro(tmp_path)["15.9"]

    assert pro["Ahri"].pick_rate == 0.5


def test_presence_adds_the_two(tmp_path: Path) -> None:
    write(
        tmp_path / "2025.csv",
        [player("g1", "15.9", "Ahri"), team("g1", "15.9", "Jax")],
    )

    pro = read_pro(tmp_path)["15.9"]

    assert pro["Ahri"].presence == 1.0
    assert pro["Jax"].presence == 1.0


def test_every_year_file_is_read(tmp_path: Path) -> None:
    write(tmp_path / "2024.csv", [player("g1", "14.01", "Ahri")])
    write(tmp_path / "2025.csv", [player("g2", "15.9", "Jax")])

    pro = read_pro(tmp_path)

    assert set(pro) == {"14.1", "15.9"}


def test_an_empty_directory_is_not_a_crash(tmp_path: Path) -> None:
    assert read_pro(tmp_path) == {}


# ── 경기 단위 · 날짜 경계 ─────────────────────────────────────────────


def dated(
    game: str, patch: str, day: str, picks: list[str], bans: list[str]
) -> list[dict[str, str]]:
    rows = [player(game, patch, c) | {"date": day} for c in picks]
    rows.append(team(game, patch, *bans) | {"date": day})
    return rows


def write_dated(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS + ["date"])
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in COLUMNS + ["date"]})


@pytest.fixture
def season(tmp_path: Path) -> Path:
    """14.1 로 치른 경기 셋 — 둘은 14.2 출시 전, 하나는 출시 뒤. 14.2 경기 하나."""
    rows = (
        dated("g1", "14.01", "2024-01-10 08:00:00", ["Ahri", "Zed"], ["Yone"])
        + dated("g2", "14.01", "2024-01-20 08:00:00", ["Ahri", "Lux"], ["Zed"])
        + dated("g3", "14.01", "2024-01-26 08:00:00", ["Zed", "Lux"], ["Ahri"])
        + dated("g4", "14.02", "2024-01-27 08:00:00", ["Zed", "Yone"], ["Lux"])
    )
    write_dated(tmp_path / "oracle" / "2024.csv", rows)
    return tmp_path / "oracle"


def test_games_count_the_same_as_read_pro(season: Path) -> None:
    """경기 단위로 읽어 다시 세도 **기존 집계와 같아야 한다** — 견줄 기준이다."""
    games = read_games(season)
    assert {g.patch for g in games.values()} == {"14.1", "14.2"}
    whole = read_pro(season)
    for patch in ("14.1", "14.2"):
        mine = rates(g for g in games.values() if g.patch == patch)
        assert mine == whole[patch]


def test_before_release_drops_games_played_after_the_next_patch(season: Path) -> None:
    """14.2 가 1월 24일에 나왔다면 g3(1월 26일)은 14.1 → 14.2 예측에 못 쓴다."""
    games = read_games(season)
    cut = before_release(games, {"14.1": date(2024, 1, 24)})
    assert set(cut) == {"14.1"}  # 경계가 없는 패치는 빠진다
    lux = cut["14.1"]["Lux"]
    assert lux.pick_rate == pytest.approx(1 / 2)  # g2 만 — 분모도 두 경기다
    assert cut["14.1"]["Ahri"].ban_rate == 0  # g3 의 밴은 안 센다
    assert "Ahri" in cut["14.1"] and cut["14.1"]["Ahri"].pick_rate == 1.0


def test_offset_moves_the_boundary_earlier(season: Path) -> None:
    games = read_games(season)
    cut = before_release(games, {"14.1": date(2024, 1, 24)}, offset=7)
    assert list(cut["14.1"]) and cut["14.1"]["Zed"].pick_rate == 1.0  # g1 만 남는다


def test_a_patch_with_no_game_before_the_boundary_has_no_pro_rates(
    season: Path,
) -> None:
    """경계 전 경기가 없으면 **그때는 프로 지표가 없었던** 것이다 — 0 이 아니다."""
    games = read_games(season)
    assert before_release(games, {"14.1": date(2024, 1, 1)}) == {}


def test_recent_crosses_patches(season: Path) -> None:
    """최근 흐름은 패치를 가리지 않는다 — 1월 27일 직전 8일이면 g2 · g3 이 든다."""
    games = read_games(season)
    window = recent(games, {"14.2": date(2024, 1, 28)}, days=8)
    assert window["14.2"]["Lux"].pick_rate == pytest.approx(2 / 3)  # g2 · g3 · g4 중
    assert recent(games, {"14.2": date(2023, 1, 1)}, days=8) == {}


def test_undated_games_are_left_out_of_the_boundary(tmp_path: Path) -> None:
    rows = dated("g1", "14.01", "", ["Ahri"], []) + dated(
        "g2", "14.01", "2024-01-10 08:00:00", ["Zed"], []
    )
    write_dated(tmp_path / "oracle" / "2024.csv", rows)
    games = read_games(tmp_path / "oracle")
    assert games["g1"].day is None
    cut = before_release(games, {"14.1": date(2024, 1, 24)})
    assert cut["14.1"]["Zed"].pick_rate == 1.0 and "Ahri" not in cut["14.1"]
