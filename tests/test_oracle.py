"""프로 경기 기록 읽기 테스트.

**네트워크도 실제 CSV 도 쓰지 않는다.** 319 MB 를 읽는 대신 같은 모양의 작은
파일을 만들어 판단만 본다 — 패치 표기를 정규화하는가, 픽과 밴을 가르는가,
무엇으로 나누는가.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from lol_balance.oracle import BAN_SLOTS, normalise, read_pro

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
    """**한 경기가 12줄이다** — 선수 10 + 팀 2.

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
