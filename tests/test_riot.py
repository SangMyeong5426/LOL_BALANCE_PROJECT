"""직접 집계 테스트.

**네트워크를 쓰지 않는다.** 요청은 `opener` 를, 시간은 `clock` · `sleep` 을 갈아
끼워 본다. 수집 루프는 엔드포인트를 흉내 내는 가짜 `get` 으로 돈다.

가장 먼저 지키는 것은 둘이다.

1. **키는 헤더로만 간다** — 주소에 들어가면 로그와 오류 문구에 남는다
2. **선수를 가리키는 칸은 저장되지 않는다** — `slim` 이 버린다
"""

from __future__ import annotations

import email.message
import http.client
import io
import random
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from lol_balance import riot
from lol_balance.riot import (
    Match,
    Origin,
    Pick,
    RateLimiter,
    RiotClient,
    RiotError,
    Window,
    agreement,
    binomial_se,
    collect,
    compare,
    count_pages,
    dumps,
    floor_se,
    invalid,
    loads,
    patch_of,
    player_bootstrap,
    ranking,
    rates,
    read_games,
    slim,
    tally,
    ugg_rates,
)
from lol_balance.ugg import (
    ChampionRanking,
    check_games_identity,
    check_win_rate_identity,
    parse_champion_ranking,
)

KEY = "RGAPI-00000000-0000-0000-0000-000000000000"
POSITIONS = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
START_MS = 1_785_400_000_000  # 2026-07-30 무렵
TEN = tuple(range(1, 11))
ORIGIN = Origin("EMERALD", "II", 1_789_700_000)
PERKS = {
    "statPerks": {"defense": 5011, "flex": 5008, "offense": 5005},
    "styles": [
        {
            "description": "primaryStyle",
            "style": 8100,
            "selections": [
                {"perk": 8112},
                {"perk": 8139},
                {"perk": 8138},
                {"perk": 8135},
            ],
        },
        {
            "description": "subStyle",
            "style": 8300,
            "selections": [{"perk": 8345}, {"perk": 8347}],
        },
    ],
}
WINDOW = Window("16_15", START_MS // 1000 - 86_400, START_MS // 1000 + 86_400)


def payload(
    match_id: str = "KR_1",
    version: str = "16.15.702.4052",
    queue: int = 420,
    champions: Sequence[int] = TEN,
    blue_wins: bool = True,
    bans: Sequence[int] = (11, 12, -1, 13, 14, 15, 16, 17, 18, -1),
    remake: bool = False,
    start_ms: int = START_MS,
) -> dict[str, Any]:
    """매치 응답 흉내. **선수 식별 칸을 일부러 넣어 둔다** — 버려지는지 본다."""
    participants = [
        {
            "puuid": f"secret-puuid-{i}",
            "riotIdGameName": f"secret-name-{i}",
            "summonerId": f"secret-summoner-{i}",
            "championId": champion,
            "teamId": 100 if i < 5 else 200,
            "teamPosition": POSITIONS[i % 5],
            "win": (i < 5) == blue_wins,
            "gameEndedInEarlySurrender": remake,
            **{f"item{k}": 3000 + 10 * i + k for k in range(6)},
            "item6": 3340,
            "summoner1Id": 4,
            "summoner2Id": 14,
            "perks": PERKS,
            "goldEarned": 12_000 + i,
            "totalMinionsKilled": 180,
            "neutralMinionsKilled": 12,
            "totalDamageDealtToChampions": 25_000,
            "kills": 5,
            "deaths": 3,
            "assists": 7,
        }
        for i, champion in enumerate(champions)
    ]
    return {
        "metadata": {
            "matchId": match_id,
            "participants": [f"secret-puuid-{i}" for i in range(10)],
        },
        "info": {
            "queueId": queue,
            "gameVersion": version,
            "gameStartTimestamp": start_ms,
            "gameDuration": 1800,
            "participants": participants,
            "teams": [
                {"teamId": 100, "bans": [{"championId": b} for b in bans[:5]]},
                {"teamId": 200, "bans": [{"championId": b} for b in bans[5:]]},
            ],
        },
    }


# ── 한 판 ──────────────────────────────────────────────────────────────


def test_slim_keeps_only_what_is_counted() -> None:
    m = slim(payload(), "kr", 7, ORIGIN)

    assert m is not None
    assert (m.match_id, m.platform, m.player, m.patch) == ("KR_1", "kr", 7, "16_15")
    assert m.origin == ORIGIN
    assert [p.champion_id for p in m.picks] == list(range(1, 11))
    assert [p.win for p in m.picks] == [True] * 5 + [False] * 5
    assert m.picks[0].position == "TOP"
    assert m.bans == (11, 12, 13, 14, 15, 16, 17, 18)  # -1 은 밴을 안 한 칸
    assert "secret" not in dumps(m)


def test_slim_keeps_the_build_and_the_numbers() -> None:
    m = slim(payload(), "kr", 7, ORIGIN)
    assert m is not None
    p = m.picks[1]

    assert p.items == (3010, 3011, 3012, 3013, 3014, 3015, 3340)  # 마지막 칸이 장신구
    assert p.spells == (4, 14)
    # 주 계열 · 주 계열 넷 · 보조 계열 · 보조 둘 · 능력치(공격 · 유연 · 방어)
    assert p.runes == (8100, 8112, 8139, 8138, 8135, 8300, 8345, 8347, 5005, 5008, 5011)
    assert (p.gold, p.minions + p.monsters, p.damage) == (12_001, 192, 25_000)
    assert (p.kills, p.deaths, p.assists) == (5, 3, 7)


def test_missing_runes_are_empty_not_a_crash() -> None:
    game = payload()
    for p in game["info"]["participants"]:
        p["perks"] = {"styles": []}

    m = slim(game, "kr", 1, ORIGIN)

    assert m is not None
    assert all(p.runes == () for p in m.picks)


def test_saved_line_round_trips() -> None:
    m = slim(payload(), "kr", 3, ORIGIN)
    assert m is not None
    assert loads(dumps(m)) == m


def test_old_narrow_lines_are_refused() -> None:
    """형식 1 은 아이템 · 룬 칸이 없다. 섞이면 빈 칸이 0 으로 읽힌다."""
    old = '{"id":"KR_1","platform":"kr","version":"16.15.1.1","start":1,"duration":1,"player":1,"picks":[],"bans":[]}'
    with pytest.raises(ValueError, match="저장 형식"):
        loads(old)


@pytest.mark.parametrize(
    "game",
    [
        payload(queue=400),  # 일반 게임
        payload(remake=True),
        payload(champions=tuple(range(1, 10))),  # 아홉 명
    ],
    ids=["not-solo-queue", "remake", "nine-players"],
)
def test_slim_rejects_games_we_do_not_count(game: dict[str, Any]) -> None:
    assert slim(game, "kr", 1, ORIGIN) is None


def test_slim_rejects_a_game_nobody_won() -> None:
    """`EUW1_7950894381` 처럼 열 명 전부 win=false 인 기록. 열 패배로 세면 안 된다."""
    game = payload()
    for p in game["info"]["participants"]:
        p["win"] = False

    assert slim(game, "kr", 1, ORIGIN) is None


def test_patch_of_reads_the_game_version() -> None:
    assert patch_of("16.15.702.4052") == "16_15"
    assert patch_of("15.9.1.1") == "15_9"
    with pytest.raises(ValueError):
        patch_of("latest")


# ── 요청 ───────────────────────────────────────────────────────────────


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_rate_limiter_waits_for_the_oldest_to_leave() -> None:
    t = FakeTime()
    limiter = RateLimiter(limits=((2, 1.0), (3, 10.0)), clock=t.clock, sleep=t.sleep)

    for _ in range(4):
        limiter.wait()

    # 셋째는 1초 창이 비기를, 넷째는 10초 창이 비기를 기다린다
    assert t.slept == [pytest.approx(1.0), pytest.approx(9.0)]


class FakeOpener:
    def __init__(self, *responses: riot.Response) -> None:
        self.responses = list(responses)
        self.requests: list[urllib.request.Request] = []

    def __call__(
        self, request: urllib.request.Request, timeout: float
    ) -> riot.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def client(opener: FakeOpener, t: FakeTime | None = None) -> RiotClient:
    t = t or FakeTime()
    return RiotClient(KEY, opener=opener, sleep=t.sleep, clock=t.clock, attempts=3)


def test_key_goes_in_the_header_not_the_address() -> None:
    opener = FakeOpener((200, {}, b'{"ok": true}'))

    assert client(opener).get("kr", "/lol/x", {"page": 2}) == {"ok": True}

    sent = opener.requests[0]
    assert sent.get_header("X-riot-token") == KEY
    assert KEY not in sent.full_url
    assert sent.full_url == "https://kr.api.riotgames.com/lol/x?page=2"
    # 파이썬 기본 이름은 Cloudflare 가 거절한다 — 이 프로젝트를 밝힌다
    assert sent.get_header("User-agent") == riot.USER_AGENT
    assert "Mozilla" not in riot.USER_AGENT


def test_missing_thing_is_none() -> None:
    assert client(FakeOpener((404, {}, b""))).get("asia", "/lol/m") is None


def test_rate_limited_response_waits_as_told() -> None:
    t = FakeTime()
    opener = FakeOpener((429, {"Retry-After": "7"}, b""), (200, {}, b"[]"))
    c = client(opener, t)

    assert c.get("asia", "/lol/m") == []
    assert 7.0 in t.slept
    assert (c.requests, c.throttled) == (2, 1)


def test_server_error_is_retried() -> None:
    opener = FakeOpener((503, {}, b""), (0, {}, b""), (200, {}, b"1"))
    assert client(opener).get("asia", "/lol/m") == 1


def test_gives_up_after_the_retry_budget() -> None:
    opener = FakeOpener(*[(503, {}, b"")] * 3)
    with pytest.raises(RiotError, match="3회 시도 실패"):
        client(opener).get("asia", "/lol/m")


def test_rejected_key_stops_without_leaking_the_player() -> None:
    opener = FakeOpener((403, {}, b'{"status":{"message":"Forbidden"}}'))

    with pytest.raises(RiotError) as caught:
        client(opener).get("asia", "/lol/match/v5/matches/by-puuid/secret-puuid/ids")

    assert "거절" in str(caught.value)
    assert "secret-puuid" not in str(caught.value)
    assert KEY not in str(caught.value)


def test_unexpected_status_stops() -> None:
    with pytest.raises(RiotError, match="HTTP 400"):
        client(FakeOpener((400, {}, b"bad"))).get("kr", "/lol/x")


def test_blank_key_is_refused() -> None:
    with pytest.raises(ValueError, match="RIOT_API_KEY"):
        RiotClient("  ")


@pytest.mark.parametrize(
    ("headers", "expected"),
    [({"retry-after": "3"}, 3.0), ({"Retry-After": "0"}, 1.0), ({}, 10.0)],
)
def test_retry_after(headers: Mapping[str, str], expected: float) -> None:
    assert riot._retry_after(headers) == expected


class _Body:
    status = 200
    headers = {"Content-Type": "application/json"}

    def read(self) -> bytes:
        return b"{}"

    def __enter__(self) -> _Body:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def test_urlopen_returns_status_headers_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: _Body())
    request = urllib.request.Request("https://kr.api.riotgames.com/x")

    assert riot._urlopen(request, 1.0) == (
        200,
        {"Content-Type": "application/json"},
        b"{}",
    )


def test_truncated_body_is_fetched_again() -> None:
    """잘린 응답은 HTTP 로는 성공이라 상태로는 안 잡힌다."""
    opener = FakeOpener((200, {}, b'{"a": 1'), (200, {}, b'{"a": 1}'))

    assert client(opener).get("asia", "/lol/m") == {"a": 1}


def test_urlopen_turns_errors_into_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    request = urllib.request.Request("https://kr.api.riotgames.com/x")
    headers = email.message.Message()
    headers["Retry-After"] = "5"

    def http_error(*_: object, **__: object) -> None:
        raise urllib.error.HTTPError(
            "https://x", 429, "slow", headers, io.BytesIO(b"later")
        )

    def unreachable(*_: object, **__: object) -> None:
        raise urllib.error.URLError("down")

    monkeypatch.setattr(urllib.request, "urlopen", http_error)
    assert riot._urlopen(request, 1.0) == (429, {"Retry-After": "5"}, b"later")

    monkeypatch.setattr(urllib.request, "urlopen", unreachable)
    assert riot._urlopen(request, 1.0) == (0, {}, b"")

    # 맥이 자는 동안 전송이 끊기면 이것이 올라온다 — 스레드를 죽이면 안 된다
    def cut_off(*_: object, **__: object) -> None:
        raise http.client.IncompleteRead(b"")

    monkeypatch.setattr(urllib.request, "urlopen", cut_off)
    assert riot._urlopen(request, 1.0) == (0, {}, b"")


# ── 받기 ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("last", [0, 1, 2, 7, 205, 300])
def test_count_pages_finds_the_last_page(last: int) -> None:
    asked: list[int] = []

    def size_of(n: int) -> int:
        asked.append(n)
        return 5 if n <= last else 0

    assert count_pages(size_of) == last
    assert len(asked) <= 2 * max(last, 1).bit_length() + 2


class FakeRiot:
    """엔드포인트 흉내. 부른 호스트와 경로를 적어 둔다."""

    def __init__(
        self,
        ladder: Mapping[tuple[str, str], list[list[str]]],
        apex: Mapping[str, list[str]],
        matches_of: Mapping[str, list[str]],
        games: Mapping[str, dict[str, Any] | None],
    ) -> None:
        self.ladder, self.apex = ladder, apex
        self.matches_of, self.games = matches_of, games
        self.calls: list[tuple[str, str]] = []

    def get(
        self, host: str, path: str, params: Mapping[str, str | int] | None = None
    ) -> Any:
        self.calls.append((host, path))
        parts = path.split("/")
        if path.startswith("/lol/league/v4/entries/"):
            pages = self.ladder.get((parts[-2], parts[-1]), [])
            n = int((params or {})["page"])
            return [{"puuid": p} for p in pages[n - 1]] if n <= len(pages) else []
        if "leagues/by-queue" in path:
            tier = parts[4].removesuffix("leagues")
            return {"entries": [{"puuid": p} for p in self.apex.get(tier, [])]}
        if path.endswith("/ids"):
            return self.matches_of.get(parts[6], [])
        return self.games.get(parts[-1])


def run_collect(
    fake: FakeRiot, target: int, seen: set[str] | None = None
) -> tuple[int, list[Match], list[str], list[str]]:
    written: list[Match] = []
    skipped: list[str] = []
    lines: list[str] = []
    got = collect(
        fake,
        "kr",
        WINDOW,
        target,
        set() if seen is None else seen,
        10,
        random.Random(1),
        written.append,
        skipped.append,
        lines.append,
    )
    return got, written, skipped, lines


@pytest.fixture
def small_ladder(monkeypatch: pytest.MonkeyPatch) -> FakeRiot:
    # 쪽 크기를 줄여 「인원 = (쪽 수 - 1) × 쪽 크기 + 마지막 쪽」이 맞게 한다
    monkeypatch.setattr(riot, "PAGE_SIZE", 2)
    return FakeRiot(
        ladder={("EMERALD", "IV"): [["p1", "p2"], ["p3"]]},
        apex={"challenger": ["c1"]},
        matches_of={"p1": ["KR_1", "KR_2"], "p2": ["KR_3"], "c1": ["KR_2", "KR_4"]},
        games={
            "KR_1": payload("KR_1"),
            "KR_2": payload("KR_2", blue_wins=False),
            "KR_3": payload("KR_3", version="16.14.1.1"),  # 다른 패치
            "KR_4": None,  # 사라진 경기
        },
    )


def test_collect_keeps_only_the_patch_and_skips_the_rest(
    small_ladder: FakeRiot,
) -> None:
    got, written, skipped, lines = run_collect(small_ladder, target=10)

    assert got == 2
    assert sorted(m.match_id for m in written) == ["KR_1", "KR_2"]
    assert sorted(skipped) == ["KR_3", "KR_4"]
    assert all(m.player > 10 for m in written)  # 이어받기 번호 뒤부터
    # 어느 단계에서 뽑았는지가 경기마다 남는다 — 조회한 시각의 랭크다
    origins = {(m.origin.tier, m.origin.division) for m in written}
    assert origins <= {("EMERALD", "IV"), ("CHALLENGER", "")}
    assert all(m.origin.looked_up > 0 for m in written)
    assert "더 뽑을 선수가 없다" in lines[-1]
    # 랭킹은 플랫폼에, 경기는 권역에 묻는다
    hosts = {host for host, path in small_ladder.calls if "/league/" in path}
    assert hosts == {"kr"}
    assert {host for host, path in small_ladder.calls if "/match/" in path} == {"asia"}


def test_collect_stops_at_the_target(small_ladder: FakeRiot) -> None:
    got, written, _, _ = run_collect(small_ladder, target=1)
    assert got == len(written) == 1


def test_collect_does_not_fetch_what_it_has(small_ladder: FakeRiot) -> None:
    got, written, _, _ = run_collect(small_ladder, target=10, seen={"KR_1", "KR_2"})

    assert got == 0
    assert written == []
    assert not any(path.endswith(("/KR_1", "/KR_2")) for _, path in small_ladder.calls)


def test_collect_stops_when_nobody_played_in_the_window(
    small_ladder: FakeRiot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(riot, "MAX_IDLE_PLAYERS", 2)
    small_ladder.matches_of = {}

    got, _, _, lines = run_collect(small_ladder, target=5)

    assert got == 0
    assert "창 안에 경기가 있는 선수가" in lines[-1]


def test_collect_stops_when_every_fetched_game_is_another_patch(
    small_ladder: FakeRiot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """패치가 바뀌는 날 — 창 안 경기가 전부 다른 패치면 사다리를 다 돌지 않는다."""
    monkeypatch.setattr(riot, "MAX_WASTED_PLAYERS", 1)
    for game in small_ladder.games.values():
        if game:
            game["info"]["gameVersion"] = "16.14.1.1"

    got, written, skipped, lines = run_collect(small_ladder, target=5)

    assert got == 0 and written == []
    assert skipped  # 받기는 했고 버렸다
    assert "다른 패치다. 멈춘다" in lines[-1]


def test_collect_leaves_other_platforms_games_alone(small_ladder: FakeRiot) -> None:
    """권역 목록에는 `EUN1_…` 같은 다른 플랫폼 경기가 섞여 온다 — 받지 않는다."""
    small_ladder.matches_of = {"p1": ["EUN1_1", "KR_1"], "p2": [], "c1": []}

    got, written, _, _ = run_collect(small_ladder, target=5)

    assert [m.match_id for m in written] == ["KR_1"]
    assert not any(path.endswith("/EUN1_1") for _, path in small_ladder.calls)


def test_read_games_drops_what_slim_would_now_refuse(tmp_path: Path) -> None:
    """형식 2 초기 파일에 남은 무승부 기록과 다른 플랫폼 경기는 읽을 때 걸러진다."""
    good = slim(payload("KR_1"), "kr", 1, ORIGIN)
    nobody = payload("KR_2")
    for p in nobody["info"]["participants"]:
        p["win"] = False
    stray = slim(payload("EUN1_3"), "kr", 1, ORIGIN)  # 다른 플랫폼 경기가 kr 로 저장됨
    assert good and stray
    # slim 이 거르기 전에 저장된 것처럼 — 직접 줄을 만든다
    lost = Match(
        **{
            **good.__dict__,
            "match_id": "KR_2",
            "picks": tuple(Pick(**{**p.__dict__, "win": False}) for p in good.picks),
        }
    )
    (tmp_path / "kr.jsonl").write_text(
        "\n".join(dumps(m) for m in (good, lost, stray)) + "\n"
    )

    assert invalid(good) is None
    assert invalid(lost) == "이긴 쪽이 다섯이 아니다"
    assert invalid(stray) == "다른 플랫폼의 경기다"
    assert [m.match_id for m in read_games(tmp_path)] == ["KR_1"]


def test_collect_refuses_an_empty_ladder() -> None:
    with pytest.raises(RiotError, match="비었다"):
        run_collect(FakeRiot({}, {}, {}, {}), target=1)


# ── 세기 ───────────────────────────────────────────────────────────────


def random_matches(n: int, players: int, seed: int) -> list[Match]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        # 한 판에 스무 종 — 열은 뽑히고 열은 밴된다. 다른 판에서는 자리가 바뀐다
        chosen = rng.sample(range(1, 31), 20)
        game = payload(
            f"KR_{i}",
            champions=chosen[:10],
            blue_wins=rng.random() < 0.5,
            bans=chosen[10:],
        )
        m = slim(game, "kr", i % players, ORIGIN)
        assert m is not None
        out.append(m)
    return out


def ranking_of(matches: Sequence[Match], wins_bonus: int = 0) -> ChampionRanking:
    """직접 집계와 **같은 값**을 내는 u.gg 응답을 만든다."""
    t = tally(matches)
    entries = [
        [str(c), [], t.wins[c] + wins_bonus, n, 0, 0, 0, 0, 0, 0]
        for c, n in t.picks.items()
    ]
    bans: dict[str, int] = {str(c): n for c, n in t.bans.items()}
    return parse_champion_ranking(
        [{"top": entries}, {**bans, "total_matches": t.games}, "2026-07-31", t.games]
    )


def test_tally_obeys_the_identities() -> None:
    t = tally(random_matches(50, 5, seed=3))

    assert sum(t.picks.values()) == 10 * t.games
    assert sum(t.wins.values()) == 5 * t.games
    r = rates(t)
    assert sum(r.pick.values()) == pytest.approx(10.0)
    assert r.games == r.ban_games == 50


def test_ranking_looks_like_a_ugg_response() -> None:
    """패널이 u.gg 를 읽는 그대로 읽을 수 있어야 한다 — 항등식까지."""
    matches = random_matches(40, 4, seed=23)

    r = ranking(matches)

    check_win_rate_identity(r)  # 전체 승률 50%
    check_games_identity(r)  # 픽 합 = 10 × 판수
    assert r.games == 40
    assert {row.role for row in r.rows} == {"top", "jungle", "mid", "adc", "supp"}
    assert r.ban_denominator == 40
    assert r.updated_at.startswith("2026-")


def test_ranking_drops_games_without_a_position() -> None:
    """포지션이 비면 역할별 합이 어긋난다. 드물어서 빼는 쪽이 낫다."""
    good, odd = random_matches(2, 1, seed=29)
    odd = Match(
        **{
            **odd.__dict__,
            "picks": (Pick(1, 100, "", True), *odd.picks[1:]),
        }
    )

    assert ranking([good, odd]).games == 1


def test_bans_are_counted_once_per_game() -> None:
    """두 팀이 같은 챔피언을 밴해도 한 번이다 — u.gg 가 그렇게 센다."""
    twice = payload("KR_9", bans=(11, 12, 13, 14, 15, 11, 16, 17, 18, 19))
    m = slim(twice, "kr", 1, ORIGIN)
    assert m is not None and m.bans.count(11) == 2  # 저장은 칸 그대로

    t = tally([m])

    assert t.bans[11] == 1
    assert rates(t).ban[11] == 1.0


def test_ugg_rates_use_the_panel_formula() -> None:
    ranking = parse_champion_ranking(
        [
            {
                "top": [["1", [], 30, 60, 0, 0, 0, 0, 0, 0]],
                "mid": [["1", [], 10, 40, 0, 0, 0, 0, 0, 0]],
            },
            {"1": 25, "-1": 5, "total_matches": 50},
            "2026-07-31",
            100,
        ]
    )
    r = ugg_rates(ranking)

    # 역할을 합친 승/판 · 판/게임 · 밴/(밴 표의 분모)
    assert r.win[1] == pytest.approx(40 / 100)
    assert r.pick[1] == pytest.approx(100 / 100)
    assert r.ban[1] == pytest.approx(25 / 50)
    assert (r.games, r.ban_games) == (100, 50)


def test_binomial_se() -> None:
    se = binomial_se(ugg_rates(ranking_of(random_matches(40, 4, seed=5))))
    assert all(v > 0 for v in se["pick"].values())
    assert se["win"].keys() == se["pick"].keys()


def test_player_bootstrap_is_seeded_and_clusters_by_player() -> None:
    matches = random_matches(60, 6, seed=7)

    once = player_bootstrap(matches, draws=200, seed=11)
    again = player_bootstrap(matches, draws=200, seed=11)
    assert once == again
    assert all(v > 0 for v in once["pick"].values())

    # 선수가 하나뿐이면 다시 뽑아도 같은 표본이다 — 퍼짐이 0
    alone = [Match(**{**m.__dict__, "player": 0}) for m in matches]
    spread = player_bootstrap(alone, draws=50, seed=1)["pick"].values()
    assert max(spread) == pytest.approx(0.0, abs=1e-12)


def test_player_bootstrap_needs_games() -> None:
    with pytest.raises(ValueError):
        player_bootstrap([], draws=10, seed=1)


def test_agreement_sees_identical_values_as_noise() -> None:
    values = {c: 0.45 + 0.01 * c for c in range(1, 11)}
    se = dict.fromkeys(values, 0.01)

    a = agreement("승률", values, se, values, se, values)

    assert (a.mean_z, a.sd_z, a.systematic) == (0.0, 0.0, 0.0)
    assert a.r == pytest.approx(1.0)
    assert a.within_noise


def test_agreement_flags_an_offset() -> None:
    ref = {c: 0.45 + 0.01 * c for c in range(1, 11)}
    ours = {c: v + 0.05 for c, v in ref.items()}
    se = dict.fromkeys(ref, 0.01)

    a = agreement("승률", ours, se, ref, se, ref)

    assert a.mean_z == pytest.approx(0.05 / (0.01 * 2**0.5))
    assert a.over_two == 1.0
    assert not a.within_noise


def test_agreement_measures_spread_beyond_noise() -> None:
    ref = {c: 0.45 + 0.01 * c for c in range(1, 9)}
    ours = {c: v + (0.02 if c % 2 else -0.02) for c, v in ref.items()}
    tiny = dict.fromkeys(ref, 1e-6)

    a = agreement("승률", ours, tiny, ref, tiny, ref)

    # 차이 ±0.02 가 번갈아 — 표준편차가 곧 계통 차이다
    assert a.systematic == pytest.approx(0.02 * (8 / 7) ** 0.5, rel=1e-3)
    assert not a.within_noise


def test_agreement_needs_a_few_champions() -> None:
    one = {1: 0.5}
    with pytest.raises(ValueError, match="1종뿐"):
        agreement("승률", one, one, one, one, one)


def test_floor_keeps_unseen_champions_from_exploding() -> None:
    ref = {1: 0.1, 2: 0.2, 3: 0.3}
    boot = {1: 0.05, 2: float("nan")}  # 3 은 한 번도 안 나왔다

    se = floor_se(boot, ref, lambda _: 100, ref)

    assert se[1] == 0.05  # 부트스트랩이 더 크면 그대로
    assert se[2] == pytest.approx((0.2 * 0.8 / 100) ** 0.5)
    assert se[3] == pytest.approx((0.3 * 0.7 / 100) ** 0.5)


def test_compare_same_numbers_are_within_noise() -> None:
    matches = random_matches(200, 10, seed=13)

    results = compare(matches, ranking_of(matches), draws=200, seed=1)

    assert [a.metric for a in results if a] == ["승률", "픽률", "밴율"]
    assert all(a and a.within_noise and a.mean_abs_diff == 0 for a in results)


def test_compare_skips_win_rate_when_games_are_few() -> None:
    matches = random_matches(20, 4, seed=17)  # 챔피언마다 서너 판

    win, pick, ban = compare(matches, ranking_of(matches), draws=100, seed=1)

    assert win is None
    assert pick.champions == ban.champions == 30


def test_compare_sees_a_different_source() -> None:
    matches = random_matches(200, 10, seed=13)
    bonus = min(tally(matches).picks.values()) // 4
    # u.gg 쪽 승수를 챔피언마다 부풀린다 — 승률만 어긋나야 한다
    win, pick, ban = compare(matches, ranking_of(matches, wins_bonus=bonus), 200, 1)

    assert win is not None
    assert not win.within_noise and win.mean_diff < 0
    assert pick.within_noise and ban.within_noise
