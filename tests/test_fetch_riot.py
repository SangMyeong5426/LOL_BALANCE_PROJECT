"""`scripts/fetch-riot` — 패치가 바뀌는 날 **앞 패치의 끝**을 그 패치 파일에 받는다.

매일 수집은 현재 패치만 받는다. 패치가 바뀐 날 창 안은 앞 패치 경기인데 전부 버렸다 —
2026-10-07 에 세 플랫폼이 0판으로 끝났고, 앞 패치(`16_19`)의 마지막 날이 비었다.
수집 루프가 넘기는 것은 `test_riot.py` 가 본다. 여기서는 넘겨받은 쪽을 본다.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from lol_balance import riot
from lol_balance.riot import Match, Origin, Pick, Window, dumps, loads

ROOT = Path(__file__).resolve().parents[1]
POSITIONS = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
START_MS = 1_791_300_000_000  # 2026-10-07 무렵


@pytest.fixture(scope="module")
def fetch() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader(
        "fetch_riot", str(ROOT / "scripts" / "fetch-riot")
    )
    spec = importlib.util.spec_from_loader("fetch_riot", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_riot"] = module
    loader.exec_module(module)
    return module


def game(match_id: str, version: str, player: int) -> Match:
    return Match(
        match_id=match_id,
        platform="kr",
        version=version,
        start_ms=START_MS,
        duration_s=1800,
        player=player,
        origin=Origin("EMERALD", "II", 1_791_300_000),
        picks=tuple(
            Pick(i + 1, 100 if i < 5 else 200, POSITIONS[i % 5], i < 5)
            for i in range(10)
        ),
        bans=(11, 12),
    )


def previous(root: Path, *games: Match) -> Path:
    """앞 패치(`16_19`)를 받던 파일."""
    path = root / "16_19" / "kr.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(dumps(m) + "\n" for m in games), encoding="utf-8")
    return path


def stored(path: Path) -> list[Match]:
    return [loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_the_tail_keeps_a_previous_patch_game_in_that_patches_file(
    fetch: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch, "OUT", tmp_path)
    path = previous(tmp_path, game("KR_1", "16.19.700.1", player=7))
    tail = fetch.Tail("16_19", "kr", limit=300)

    assert tail(game("KR_9", "16.19.701.2", player=3)) is True

    assert [(m.match_id, m.patch) for m in stored(path)] == [
        ("KR_1", "16_19"),
        ("KR_9", "16_19"),
    ]
    assert tail.kept == 1


def test_the_player_is_numbered_in_that_files_own_count(
    fetch: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """번호는 파일마다 따로 센다. 그대로 적으면 그 파일의 다른 이용자와 겹친다.

    이용자 단위로 오차를 재므로(`player_bootstrap`) 겹치면 두 사람이 한 사람이 된다.
    """
    monkeypatch.setattr(fetch, "OUT", tmp_path)
    path = previous(tmp_path, game("KR_1", "16.19.700.1", player=7))
    tail = fetch.Tail("16_19", "kr", limit=300)

    tail(game("KR_7", "16.19.701.2", player=3))  # 이번 실행의 3번 이용자
    tail(game("KR_8", "16.19.701.2", player=3))  # 같은 이용자
    tail(game("KR_9", "16.19.701.2", player=4))  # 다음 이용자

    assert [m.player for m in stored(path)] == [7, 8, 8, 9]


def test_the_tail_refuses_what_is_not_its_to_keep(
    fetch: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch, "OUT", tmp_path)
    path = previous(tmp_path, game("KR_1", "16.19.700.1", player=7))
    tail = fetch.Tail("16_19", "kr", limit=1)

    assert tail(game("KR_1", "16.19.700.1", player=3)) is False  # 이미 있다
    assert tail(game("KR_2", "16.18.699.9", player=3)) is False  # 그 패치가 아니다
    assert tail(game("KR_3", "16.19.701.2", player=3)) is True
    assert tail(game("KR_4", "16.19.701.2", player=4)) is False  # 하루치를 넘는다

    assert [m.match_id for m in stored(path)] == ["KR_1", "KR_3"]


@pytest.mark.parametrize("left", ["nothing", "an empty file"])
def test_a_patch_never_collected_gets_no_tail(
    fetch: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, left: str
) -> None:
    """받던 패치만 잇는다. 마지막 날만 받으면 그 하루가 패치 전체처럼 읽힌다."""
    monkeypatch.setattr(fetch, "OUT", tmp_path)
    if left == "an empty file":
        previous(tmp_path)
    tail = fetch.Tail("16_19", "kr", limit=300)

    assert tail(game("KR_9", "16.19.701.2", player=3)) is False

    path = tmp_path / "16_19" / "kr.jsonl"
    assert not path.exists() or path.read_text() == ""


class Quiet:
    """요청을 안 보내는 클라이언트. 결과 줄이 읽는 두 칸만 있다."""

    requests = 0
    throttled = 0

    def __init__(self, key: str) -> None:
        self.key = key


def test_run_gives_collect_the_tail_and_reports_what_it_kept(
    fetch: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch, "OUT", tmp_path)
    monkeypatch.setattr(fetch, "RiotClient", Quiet)
    path = previous(tmp_path, game("KR_1", "16.19.700.1", player=7))
    limits: list[int] = []

    def fake_collect(*args: Any, other: Any = None, **kwargs: Any) -> int:
        """창 안이 전부 앞 패치인 날 — 현재 패치는 한 판도 없다."""
        assert other is not None
        limits.append(other.limit)
        assert other(game("KR_9", "16.19.701.2", player=1)) is True
        return 0

    monkeypatch.setattr(fetch, "collect", fake_collect)
    results: dict[str, str] = {}
    fetch.run("kr", Window("16_20", 0, 10), -300, "key", 1, results, tail="16_19")

    assert limits == [300]  # 앞 패치도 하루치까지만
    assert [m.match_id for m in stored(path)] == ["KR_1", "KR_9"]
    assert "앞 패치 16_19 에 1판" in results["kr"]
    assert results["kr"].endswith("분")  # 끝이 이래야 「멈춤」으로 세지 않는다


def test_run_without_a_tail_gives_collect_no_hook(
    fetch: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch, "OUT", tmp_path)
    monkeypatch.setattr(fetch, "RiotClient", Quiet)
    hooks: list[Any] = []

    def fake_collect(*args: Any, other: Any = None, **kwargs: Any) -> int:
        hooks.append(other)
        return 0

    monkeypatch.setattr(fetch, "collect", fake_collect)
    results: dict[str, str] = {}
    fetch.run("kr", Window("16_20", 0, 10), -300, "key", 1, results)

    assert hooks == [None]
    assert "앞 패치" not in results["kr"]


class Morning:
    """패치가 바뀐 날 아침의 서버 흉내 — 이용자 셋의 창 안 경기가 전부 앞 패치다."""

    throttled = 0
    played = {"a": ["KR_11", "KR_12"], "b": ["KR_13"], "c": ["KR_14"]}

    def __init__(self, key: str) -> None:
        self.requests = 0

    def get(
        self, host: str, path: str, params: Mapping[str, str | int] | None = None
    ) -> Any:
        self.requests += 1
        parts = path.split("/")
        if path.startswith("/lol/league/v4/entries/"):
            first = (parts[-2], parts[-1]) == ("EMERALD", "IV") and params == {
                "page": 1
            }
            return [{"puuid": p} for p in self.played] if first else []
        if "leagues/by-queue" in path:
            return {"entries": []}
        if path.endswith("/ids"):
            return self.played[parts[6]]
        return {"id": parts[-1]}


def test_a_patch_change_morning_fills_the_previous_patch_not_the_new_one(
    fetch: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """수집 루프 · 넘겨받는 쪽 · 결과 줄을 이어서 본다 — 2026-10-07 아침이다.

    현재 패치(`16_20`)는 0판이다. 앞 패치(`16_19`) 경기는 하루치(여기서는 2판)까지
    그 패치에 잇고, 더 받아 줄 수 없게 되면 멈춘다.
    """
    monkeypatch.setattr(fetch, "OUT", tmp_path)
    monkeypatch.setattr(fetch, "RiotClient", Morning)
    monkeypatch.setattr(riot, "PAGE_SIZE", 3)
    monkeypatch.setattr(riot, "MAX_WASTED_PLAYERS", 1)
    games = {f"KR_{n}": game(f"KR_{n}", "16.19.701.2", 0) for n in (11, 12, 13, 14)}
    monkeypatch.setattr(
        riot,
        "slim",
        lambda payload, platform, player, origin: replace(
            games[payload["id"]], player=player, origin=origin
        ),
    )
    path = previous(tmp_path, game("KR_1", "16.19.700.1", player=7))
    window = Window("16_20", START_MS // 1000 - 86_400, START_MS // 1000 + 86_400)
    results: dict[str, str] = {}

    fetch.run("kr", window, -2, "key", 1, results, tail="16_19")

    kept = stored(path)[1:]
    assert len(kept) == 2 and {m.patch for m in kept} == {"16_19"}
    assert all(m.player > 7 for m in kept)  # 그 파일의 번호로 다시 매겼다
    assert (tmp_path / "16_20" / "kr.jsonl").read_text() == ""
    # 받은 경기도 새 패치의 `.skip` 에는 적는다 — 새 패치로는 쓸 수 없다
    skipped = (tmp_path / "16_20" / "kr.skip").read_text().split()
    assert {m.match_id for m in kept} <= set(skipped)
    assert results["kr"].startswith("0판 · 앞 패치 16_19 에 2판")
    assert results["kr"].endswith("분")


@pytest.mark.parametrize(
    "argv",
    [
        ["16_20", "--since", "2026-10-06", "--per-region", "300", "--tail", "16_19"],
        ["16_20", "--since", "2026-10-06", "--add", "300", "--tail", "16_20"],
    ],
)
def test_the_tail_goes_with_add_and_names_another_patch(
    fetch: ModuleType, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """`--per-region` 은 옛 패치를 창을 적어 받는 길이다 — 거기서는 섞지 않는다."""
    monkeypatch.setattr(sys, "argv", ["fetch-riot", *argv])
    with pytest.raises(SystemExit) as stop:
        fetch.main()
    assert stop.value.code == 2
