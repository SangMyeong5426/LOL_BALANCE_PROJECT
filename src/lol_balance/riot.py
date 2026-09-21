"""Riot API 로 경기를 받아 솔랭 지표와 같은 식으로 센다 — **직접 집계**.

**키가 필요한 선택 경로다**(`docs/adr/0010-riot-api-direct-aggregation.md`).
기존 결과(① · ② · ③)는 이것 없이 그대로 나온다.

세 층이다.

1. `RiotClient` — 요청 한 건. 키는 **주소가 아니라 헤더**로 보내고, 호스트마다
   요청 한도를 지킨다
2. `collect` · `slim` — 지금 에메랄드 이상인 선수를 무작위로 뽑아 그 경기를 받고,
   필요한 칸만 남긴다. **선수를 가리키는 칸은 받자마자 버린다**
3. `tally` · `rates` · `compare` — 솔랭 지표와 같은 식으로 세고 u.gg 와 견준다

**u.gg 와 똑같이 셀 수는 없다.** u.gg 가 지역을 어떤 비율로 섞는지, 「에메랄드
이상」을 무엇으로 가르는지 모른다. 그래서 같은 패치에서 두 값을 먼저 견주고,
판정 기준은 재기 전에 ADR 0010 에 적었다.
"""

from __future__ import annotations

import http.client
import json
import math
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from collections import Counter, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from lol_balance.ugg import ChampionRanking, ChampionRow

# 플랫폼 → 매치 API 권역. **요청 한도는 권역마다 따로 걸린다**(포털 문서) — 그래서
# 권역이 서로 다른 셋을 골랐다. 같은 권역의 플랫폼을 더하면 한도를 나눠 쓴다.
ROUTES: dict[str, str] = {"kr": "asia", "euw1": "europe", "na1": "americas"}

QUEUE_ID = 420  # 솔로 랭크 — u.gg 의 `ranked_solo_5x5`
QUEUE = "RANKED_SOLO_5x5"

# Riot 의 포지션 이름 → u.gg 의 역할 이름. 패널이 u.gg 이름으로 돌아가므로 맞춘다.
ROLES: dict[str, str] = {
    "TOP": "top",
    "JUNGLE": "jungle",
    "MIDDLE": "mid",
    "BOTTOM": "adc",
    "UTILITY": "supp",
}

# 「에메랄드 이상」. 단계가 있는 두 티어와, 리그 하나로 통째로 오는 셋.
DIVISIONS: tuple[tuple[str, str], ...] = tuple(
    (tier, division)
    for tier in ("EMERALD", "DIAMOND")
    for division in ("IV", "III", "II", "I")
)
APEX: tuple[str, ...] = ("master", "grandmaster", "challenger")

# 랭킹 한 쪽의 크기. 마지막 쪽만 모자란다.
PAGE_SIZE = 205

# 한 선수에게서 가져올 최대 경기 수 — 한 선수가 표본을 채우지 않게.
PER_PLAYER = 20

# 창 안에 경기가 없는 선수가 이만큼 이어지면 멈춘다. 창을 잘못 줬거나 과거가
# 너무 멀어 지금 랭커가 그때 안 뛰었다는 뜻이라, 계속 돌리면 한도만 쓴다.
MAX_IDLE_PLAYERS = 400

# 받은 경기가 **전부** 버려지는 선수가 이만큼 이어져도 멈춘다. 패치가 바뀌는 날
# Data Dragon 이 라이브보다 앞서거나 뒤처지면 창 안 경기가 전부 다른 패치라서,
# 이것이 없으면 사다리 전체를 돌며 한도만 쓴다 — 매일 수집이 그렇게 며칠씩 막힐
# 수 있었다(2026-09-20 감사). 선수 40명이면 요청 800회 남짓, 15분이다.
MAX_WASTED_PLAYERS = 40

# 요청에 밝히는 이름. **파이썬 기본 이름(`Python-urllib`)은 Cloudflare 가
# 거절한다**(오류 1010, 2026-09-18). 브라우저로 위장하지 않고 이 프로젝트를 밝힌다.
USER_AGENT = "lol-balance-project/0.1 (personal research)"

# 개인 키 한도는 1초 20회 · 2분 100회다. 조금 남겨 둔다 — 넘겨서 429 를 받으면
# 그만큼 기다려야 해서 오히려 느려진다.
LIMITS: tuple[tuple[int, float], ...] = ((18, 1.0), (95, 120.0))

# 저장 형식의 판. **1 은 칸이 좁았다** — 챔피언 · 승패 · 밴만 남기고 아이템 · 룬 ·
# 성과를 버렸다. 같은 응답에 들어 있어 남기는 데 요청이 안 든다는 것을 뒤늦게 보고
# 2 로 넓혔다(2026-09-18). 1 로 받은 1,625판은 `data/riot/v1/` 에 따로 둔다.
SCHEMA = 2

# 판정 기준 — **재기 전에 ADR 0010 에 적은 값이다.** 결과를 보고 바꾸지 않는다.
MEAN_Z_LIMIT = 0.2
SD_Z_LIMIT = 1.2
# 승률은 두 쪽 다 이만큼 판수가 있는 챔피언만 본다.
MIN_GAMES = 30
# 이보다 적은 챔피언으로는 z 의 평균과 표준편차를 말할 수 없다.
MIN_CHAMPIONS = 3

Response = tuple[int, Mapping[str, str], bytes]
Opener = Callable[[urllib.request.Request, float], Response]


class RiotError(RuntimeError):
    """수집을 멈춰야 하는 응답 — 키 거절, 알 수 없는 오류, 재시도 소진."""


class RateLimiter:
    """창 여러 개(「1초 18회」「2분 95회」)를 동시에 지킨다."""

    def __init__(
        self,
        limits: tuple[tuple[int, float], ...] = LIMITS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._limits = limits
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()

    def wait(self) -> None:
        """보내도 될 때까지 기다린 뒤 한 건을 적는다."""
        longest = max(window for _, window in self._limits)
        while True:
            now = self._clock()
            while self._sent and now - self._sent[0] >= longest:
                self._sent.popleft()
            delay = 0.0
            for count, window in self._limits:
                recent = [t for t in self._sent if now - t < window]
                if len(recent) >= count:
                    # 이 건이 창을 빠져나가야 자리가 난다
                    delay = max(delay, recent[-count] + window - now)
            if delay <= 0:
                self._sent.append(now)
                return
            self._sleep(delay)


def _urlopen(request: urllib.request.Request, timeout: float) -> Response:
    """요청 한 건. **받다가 끊긴 것도 상태 0 으로 돌려 재시도에 태운다.**

    처음에는 연결 실패(`URLError`)만 잡았다. 그런데 맥이 자는 동안 전송이 끊기면
    `http.client.IncompleteRead` 가 올라와 수집 스레드가 통째로 죽었다 — `16_15`
    수집이 1,612판에서 그렇게 멈췄다(2026-09-18). HTTP 층의 예외와 소켓 오류
    (`OSError`)를 함께 잡는다.
    """
    try:
        with urllib.request.urlopen(request, timeout=timeout) as r:
            return int(r.status), dict(r.headers.items()), r.read()
    except urllib.error.HTTPError as exc:
        headers = dict(exc.headers.items()) if exc.headers else {}
        try:
            body = exc.read()
        except (OSError, http.client.HTTPException):
            body = b""
        return exc.code, headers, body
    except (OSError, http.client.HTTPException):
        # URLError · TimeoutError · ConnectionError 는 모두 OSError 다
        return 0, {}, b""


def _redact(path: str) -> str:
    """오류 문구에 선수 식별자가 남지 않게 한다."""
    return re.sub(r"/by-puuid/[^/?]+", "/by-puuid/…", path)


def _retry_after(headers: Mapping[str, str]) -> float:
    lower = {k.lower(): v for k, v in headers.items()}
    try:
        return max(1.0, float(lower.get("retry-after", "")))
    except ValueError:
        return 10.0


class Getter(Protocol):
    def get(
        self, host: str, path: str, params: Mapping[str, str | int] | None = None
    ) -> Any: ...


class RiotClient:
    """Riot API 요청. **키는 헤더로만 보낸다** — 주소에 넣으면 로그에 남는다."""

    def __init__(
        self,
        key: str,
        opener: Opener = _urlopen,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        attempts: int = 6,
        timeout: float = 30.0,
    ) -> None:
        if not key.strip():
            raise ValueError("RIOT_API_KEY 가 비어 있다 — .env 에 개인 키를 넣는다")
        self._key = key.strip()
        self._opener = opener
        self._sleep = sleep
        self._clock = clock
        self._attempts = attempts
        self._timeout = timeout
        self._limiters: dict[str, RateLimiter] = {}
        self.requests = 0
        self.throttled = 0

    def get(
        self, host: str, path: str, params: Mapping[str, str | int] | None = None
    ) -> Any:
        """JSON 하나를 받는다. 없는 것(404)은 None 이다.

        `host` 는 플랫폼(`kr`)이나 권역(`asia`)이다. 한도를 호스트마다 따로 센다.
        """
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"https://{host}.api.riotgames.com{path}{query}"
        limiter = self._limiters.setdefault(
            host, RateLimiter(clock=self._clock, sleep=self._sleep)
        )
        last = ""
        for attempt in range(self._attempts):
            limiter.wait()
            request = urllib.request.Request(
                url,
                headers={
                    "X-Riot-Token": self._key,
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
            status, headers, body = self._opener(request, self._timeout)
            self.requests += 1
            if status == 200:
                try:
                    return json.loads(body)
                except ValueError:
                    # 잘린 응답은 HTTP 로는 성공이다. 다시 받아 본다
                    self._sleep(min(2.0**attempt, 60.0))
                    last = "잘린 응답"
                    continue
            if status == 404:
                return None
            if status in (401, 403):
                raise RiotError(
                    f"거절됐다(HTTP {status}) — 개발 키라면 24시간이 지나 꺼졌을 수"
                    f" 있다: {_redact(path)} · {body[:80]!r}"
                )
            if status == 429:
                self.throttled += 1
                self._sleep(_retry_after(headers))
            elif status == 0 or status >= 500:
                self._sleep(min(2.0**attempt, 60.0))
            else:
                raise RiotError(f"HTTP {status}: {_redact(path)} · {body[:80]!r}")
            last = f"HTTP {status}"
        raise RiotError(f"{self._attempts}회 시도 실패({last}): {_redact(path)}")


# ── 경기 한 판 ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Pick:
    """한 선수의 한 판. **선수가 누구인지는 남기지 않는다.**

    `items` 는 경기가 끝났을 때의 일곱 칸이고 마지막이 장신구, 0 은 빈 칸이다 —
    **최종 아이템이지 구매 이력이 아니다.** 판 것 · 다 쓴 것은 남지 않는다.
    `runes` 는 (주 계열, 주 계열 룬 넷, 보조 계열, 보조 룬 둘, 능력치 셋) 순서다.
    `minions` 와 `monsters` 를 더한 것이 CS 다.
    """

    champion_id: int
    team_id: int
    position: str
    win: bool
    items: tuple[int, ...] = ()
    spells: tuple[int, ...] = ()
    runes: tuple[int, ...] = ()
    gold: int = 0
    minions: int = 0
    monsters: int = 0
    damage: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0


@dataclass(frozen=True)
class Origin:
    """이 경기를 가져온 선수를 **어디서 · 언제** 뽑았나.

    랭크는 **조회한 시각의** 것이다 — 그 경기를 치른 때의 랭크가 아니다. 그래서
    이 표본은 「지금 에메랄드 이상인 선수에서 출발해 모은 경기」이지 「그때
    에메랄드 이상이던 경기」가 아니다. 마스터 이상은 단계가 없어 `division` 이 빈다.
    """

    tier: str
    division: str
    looked_up: int  # epoch 초


@dataclass(frozen=True)
class Match:
    """경기 한 판에서 필요한 칸만.

    `player` 는 이 경기를 가져온 선수의 **일련번호**다 — 선수 단위 부트스트랩에
    필요한 것은 「같은 선수에게서 왔는가」뿐이라 식별자 대신 번호를 둔다.
    번호는 플랫폼마다 따로 센다.
    """

    match_id: str
    platform: str
    version: str
    start_ms: int
    duration_s: int
    player: int
    origin: Origin
    picks: tuple[Pick, ...]
    bans: tuple[int, ...]

    @property
    def patch(self) -> str:
        return patch_of(self.version)


def patch_of(game_version: str) -> str:
    """`gameVersion` 을 우리 패치 표기로 바꾼다.

    >>> patch_of("16.15.702.4052")
    '16_15'
    """
    m = re.match(r"^(\d+)\.(\d+)\.", game_version)
    if not m:
        raise ValueError(f"gameVersion 형식이 아니다: {game_version!r}")
    return f"{m.group(1)}_{m.group(2)}"


def _runes(perks: Mapping[str, Any] | None) -> tuple[int, ...]:
    """(주 계열, 주 계열 룬 넷, 보조 계열, 보조 룬 둘, 능력치 셋). 모양이 다르면 빈 값."""
    styles = {s.get("description"): s for s in (perks or {}).get("styles") or []}
    primary, sub = styles.get("primaryStyle"), styles.get("subStyle")
    if not primary or not sub:
        return ()
    shards = (perks or {}).get("statPerks") or {}
    return (
        int(primary["style"]),
        *(int(s["perk"]) for s in primary.get("selections") or []),
        int(sub["style"]),
        *(int(s["perk"]) for s in sub.get("selections") or []),
        *(int(shards.get(k, 0)) for k in ("offense", "flex", "defense")),
    )


def _pick(p: Mapping[str, Any]) -> Pick:
    return Pick(
        champion_id=int(p["championId"]),
        team_id=int(p["teamId"]),
        position=str(p.get("teamPosition") or ""),
        win=bool(p["win"]),
        items=tuple(int(p.get(f"item{i}", 0)) for i in range(7)),
        spells=(int(p.get("summoner1Id", 0)), int(p.get("summoner2Id", 0))),
        runes=_runes(p.get("perks")),
        gold=int(p.get("goldEarned", 0)),
        minions=int(p.get("totalMinionsKilled", 0)),
        monsters=int(p.get("neutralMinionsKilled", 0)),
        damage=int(p.get("totalDamageDealtToChampions", 0)),
        kills=int(p.get("kills", 0)),
        deaths=int(p.get("deaths", 0)),
        assists=int(p.get("assists", 0)),
    )


def slim(
    payload: Mapping[str, Any], platform: str, player: int, origin: Origin
) -> Match | None:
    """매치 응답에서 필요한 칸만 남긴다. 셀 수 없는 판이면 None.

    **선수를 가리키는 칸(`puuid` · 이름 · 소환사 id)은 여기서 버린다.** 저장하는
    것은 이 함수가 돌려준 것뿐이다.

    None 이 되는 판: 솔로 랭크가 아닌 것, 열 명이 아닌 것, 리메이크.
    리메이크는 승패가 대칭이 아니어서 승률 합이 50% 에서 어긋난다.
    """
    info = payload["info"]
    if info.get("queueId") != QUEUE_ID:
        return None
    participants = info.get("participants") or []
    if len(participants) != 10:
        return None
    if any(p.get("gameEndedInEarlySurrender") for p in participants):
        return None
    picks = tuple(_pick(p) for p in participants)
    # **이긴 쪽이 정확히 다섯이어야 한다.** `EUW1_7950894381`(16_16 · 822초)은
    # 열 명 전부 `win: false` 로 왔다 — 조기 항복 표시도 없는 무승부 기록이다.
    # 그대로 세면 열 패배가 들어가 전체 승률이 50% 에서 어긋난다(2026-09-20 발견).
    if sum(p.win for p in picks) != 5:
        return None
    bans = tuple(
        int(b["championId"])
        for team in info.get("teams") or []
        for b in team.get("bans") or []
        if int(b["championId"]) > 0  # -1 은 밴을 안 한 칸
    )
    return Match(
        match_id=str(payload["metadata"]["matchId"]),
        platform=platform,
        version=str(info["gameVersion"]),
        start_ms=int(info["gameStartTimestamp"]),
        duration_s=int(info["gameDuration"]),
        player=player,
        origin=origin,
        picks=picks,
        bans=bans,
    )


def dumps(match: Match) -> str:
    """한 줄 JSON. 칸 이름을 적는다 — 위치로만 읽는 형식은 u.gg 에서 해독에 애먹었다."""
    return json.dumps(
        {
            "v": SCHEMA,
            "id": match.match_id,
            "platform": match.platform,
            "queue": QUEUE_ID,
            "version": match.version,
            "start": match.start_ms,
            "duration": match.duration_s,
            "player": match.player,
            "origin": {
                "tier": match.origin.tier,
                "division": match.origin.division,
                "looked_up": match.origin.looked_up,
            },
            "picks": [
                {
                    "champion": p.champion_id,
                    "team": p.team_id,
                    "position": p.position,
                    "win": p.win,
                    "items": list(p.items),
                    "spells": list(p.spells),
                    "runes": list(p.runes),
                    "gold": p.gold,
                    "minions": p.minions,
                    "monsters": p.monsters,
                    "damage": p.damage,
                    "kills": p.kills,
                    "deaths": p.deaths,
                    "assists": p.assists,
                }
                for p in match.picks
            ],
            "bans": list(match.bans),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def invalid(match: Match) -> str | None:
    """저장된 경기가 셀 수 없는 것이면 그 이유. **형식 2 초기 파일에 둘이 섞여 있었다.**

    `slim` 이 나중에 거르게 된 것을 읽는 쪽에서도 거른다 — 파일을 다시 받지 않아도
    같은 규칙이 서게. 승자가 다섯이 아닌 경기 1판, 다른 플랫폼에서 치른 경기 88판
    (2026-09-20 감사).
    """
    if sum(p.win for p in match.picks) != 5:
        return "이긴 쪽이 다섯이 아니다"
    if match.match_id.split("_", 1)[0] != match.platform.upper():
        return "다른 플랫폼의 경기다"
    return None


def read_games(folder: Path) -> list[Match]:
    """한 패치 폴더의 경기 전부. 셀 수 없는 것은 조용히 빼지 않고 `dropped` 로 센다."""
    out: list[Match] = []
    for path in sorted(folder.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            m = loads(line)
            if invalid(m) is None:
                out.append(m)
    return out


def loads(line: str) -> Match:
    """`dumps` 의 역. **다른 판의 형식은 거절한다** — 칸이 모자란 채로 섞이지 않게."""
    raw = json.loads(line)
    if raw.get("v") != SCHEMA:
        raise ValueError(
            f"저장 형식 {raw.get('v')!r} — 지금은 {SCHEMA} 이다. "
            "옛 형식(data/riot/v1)은 아이템 · 룬 칸이 없다"
        )
    origin = raw["origin"]
    return Match(
        match_id=raw["id"],
        platform=raw["platform"],
        version=raw["version"],
        start_ms=raw["start"],
        duration_s=raw["duration"],
        player=raw["player"],
        origin=Origin(origin["tier"], origin["division"], origin["looked_up"]),
        picks=tuple(
            Pick(
                champion_id=p["champion"],
                team_id=p["team"],
                position=p["position"],
                win=p["win"],
                items=tuple(p["items"]),
                spells=tuple(p["spells"]),
                runes=tuple(p["runes"]),
                gold=p["gold"],
                minions=p["minions"],
                monsters=p["monsters"],
                damage=p["damage"],
                kills=p["kills"],
                deaths=p["deaths"],
                assists=p["assists"],
            )
            for p in raw["picks"]
        ),
        bans=tuple(int(c) for c in raw["bans"]),
    )


# ── 받기 ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Window:
    """패치 하나를 받는 창. 시각은 epoch 초다."""

    patch: str
    start: int
    end: int


def count_pages(size_of: Callable[[int], int]) -> int:
    """비지 않은 마지막 쪽 번호. 한 쪽도 없으면 0.

    두 배씩 늘려 빈 쪽을 찾고, 그 사이를 반씩 좁힌다. 쪽 수가 수백이어도 요청이
    스무 번 안쪽이다.
    """
    if size_of(1) == 0:
        return 0
    low, high = 1, 2
    while size_of(high) > 0:
        low, high = high, high * 2
    while high - low > 1:
        mid = (low + high) // 2
        if size_of(mid) > 0:
            low = mid
        else:
            high = mid
    return low


@dataclass
class Ladder:
    """한 플랫폼의 「지금 에메랄드 이상」 목록. 받은 쪽은 다시 쓴다.

    `puuid` 는 **메모리에만** 둔다. 파일에 적지 않는다.
    """

    client: Getter
    platform: str
    clock: Callable[[], float] = time.time
    _pages: dict[tuple[str, str, int], list[str]] = field(default_factory=dict)
    _looked_up: dict[tuple[str, str, int], int] = field(default_factory=dict)

    def page(self, tier: str, division: str, number: int) -> list[str]:
        key = (tier, division, number)
        if key not in self._pages:
            entries = self.client.get(
                self.platform,
                f"/lol/league/v4/entries/{QUEUE}/{tier}/{division}",
                {"page": number},
            )
            self._keep(key, _puuids(entries or []))
        return self._pages[key]

    def apex(self, tier: str) -> list[str]:
        key = (tier.upper(), "", 1)
        if key not in self._pages:
            league = self.client.get(
                self.platform, f"/lol/league/v4/{tier}leagues/by-queue/{QUEUE}"
            )
            self._keep(key, _puuids((league or {}).get("entries") or []))
        return self._pages[key]

    def looked_up(self, key: tuple[str, str, int]) -> int:
        """그 쪽을 받은 시각 — 거기 적힌 랭크가 언제의 것인가."""
        return self._looked_up[key]

    def _keep(self, key: tuple[str, str, int], puuids: list[str]) -> None:
        self._pages[key] = puuids
        self._looked_up[key] = int(self.clock())


def _puuids(entries: Iterable[Mapping[str, Any]]) -> list[str]:
    return [str(e["puuid"]) for e in entries if e.get("puuid")]


@dataclass(frozen=True)
class Stratum:
    """뽑기 단위 하나 — 단계 하나 또는 리그 하나."""

    tier: str
    division: str
    pages: int
    size: int


def strata(ladder: Ladder) -> list[Stratum]:
    """단계마다 인원을 센다. 뽑을 때 이 인원에 비례해 고른다."""
    out: list[Stratum] = []
    for tier, division in DIVISIONS:

        def size_of(n: int, tier: str = tier, division: str = division) -> int:
            return len(ladder.page(tier, division, n))

        pages = count_pages(size_of)
        if pages:
            size = (pages - 1) * PAGE_SIZE + size_of(pages)
            out.append(Stratum(tier, division, pages, size))
    for tier in APEX:
        size = len(ladder.apex(tier))
        if size:
            out.append(Stratum(tier.upper(), "", 1, size))
    return out


@dataclass(frozen=True)
class Drawn:
    """뽑은 선수. `puuid` 는 여기까지만 간다 — 저장되는 것은 `origin` 이다."""

    puuid: str
    origin: Origin


def draw_player(
    ladder: Ladder, groups: Sequence[Stratum], rng: random.Random
) -> Drawn | None:
    """지금 에메랄드 이상인 선수 한 명을 **인원에 비례해** 무작위로 뽑는다.

    단계마다 같은 수를 뽑지 않는다 — 그러면 사람이 적은 위쪽 단계가 실제보다 많이
    뽑혀 가중치로 되돌려야 한다. 인원에 비례해 뽑으면 그대로 합쳐도 된다.
    """
    stratum = rng.choices(groups, weights=[s.size for s in groups])[0]
    if stratum.division:
        key = (stratum.tier, stratum.division, rng.randint(1, stratum.pages))
        page = ladder.page(*key)
    else:
        key = (stratum.tier, "", 1)
        page = ladder.apex(stratum.tier.lower())
    if not page:
        return None
    origin = Origin(stratum.tier, stratum.division, ladder.looked_up(key))
    return Drawn(rng.choice(page), origin)


def collect(
    client: Getter,
    platform: str,
    window: Window,
    target: int,
    seen: set[str],
    first_player: int,
    rng: random.Random,
    write: Callable[[Match], None],
    skip: Callable[[str], None],
    log: Callable[[str], None] = print,
) -> int:
    """`target` 판을 채울 때까지 받는다. 받은 판 수를 돌려준다.

    `seen` 에 있는 경기는 다시 받지 않는다 — 이어받기와 플랫폼 안 중복을 함께
    막는다. 쓸 수 없는 경기(다른 패치 · 리메이크 · 창 밖)는 `skip` 으로 넘겨
    다음에도 안 받게 한다.
    """
    ladder = Ladder(client, platform)
    groups = strata(ladder)
    if not groups:
        raise RiotError(f"{platform}: 에메랄드 이상 목록이 비었다")
    log(
        f"[{platform}] 에메랄드 이상 "
        + " · ".join(f"{s.tier} {s.division}".strip() + f" {s.size:,}" for s in groups)
    )
    route = ROUTES[platform]
    population = sum(s.size for s in groups)
    drawn: set[str] = set()
    player = first_player
    collected = 0
    idle = 0
    misses = 0
    wasted = 0
    while collected < target:
        pick = draw_player(ladder, groups, rng)
        if pick is None or pick.puuid in drawn:
            misses += 1
            if misses > 1000 or len(drawn) >= population:
                log(f"[{platform}] 더 뽑을 선수가 없다 — 멈춘다")
                break
            continue
        misses = 0
        drawn.add(pick.puuid)
        ids = client.get(
            route,
            f"/lol/match/v5/matches/by-puuid/{pick.puuid}/ids",
            {
                "queue": QUEUE_ID,
                "startTime": window.start,
                "endTime": window.end,
                "start": 0,
                "count": 100,
            },
        )
        # 권역 목록에는 다른 플랫폼에서 치른 경기도 섞여 온다(`EUN1_…` 이 euw1
        # 사다리에). 경기 id 의 앞머리가 이 플랫폼이 아니면 받지 않는다 —
        # 플랫폼별 비교가 그 플랫폼의 경기여야 하기 때문이다(2026-09-20 감사).
        fresh = [
            str(i)
            for i in (ids or [])
            if str(i) not in seen and str(i).split("_", 1)[0] == platform.upper()
        ]
        if not fresh:
            idle += 1
            if idle >= MAX_IDLE_PLAYERS:
                log(f"[{platform}] 창 안에 경기가 있는 선수가 {idle}명째 없다 — 멈춘다")
                break
            continue
        idle = 0
        player += 1
        written_here = 0
        for match_id in rng.sample(fresh, min(PER_PLAYER, len(fresh))):
            seen.add(match_id)
            payload = client.get(route, f"/lol/match/v5/matches/{match_id}")
            match = slim(payload, platform, player, pick.origin) if payload else None
            if (
                match is None
                or match.patch != window.patch
                or not window.start * 1000 <= match.start_ms <= window.end * 1000
            ):
                skip(match_id)
                continue
            write(match)
            collected += 1
            written_here += 1
            if collected % 100 == 0:
                log(
                    f"[{platform}] {collected:,}/{target:,}판 · 선수 {player - first_player}"
                )
            if collected >= target:
                break
        wasted = 0 if written_here else wasted + 1
        if wasted >= MAX_WASTED_PLAYERS:
            log(
                f"[{platform}] 받은 경기가 전부 버려지는 선수가 {wasted}명째다"
                " — 창 안 경기가 다른 패치다. 멈춘다"
            )
            break
    return collected


# ── 세기 ───────────────────────────────────────────────────────────────


@dataclass
class Tally:
    games: int = 0
    picks: Counter[int] = field(default_factory=Counter)
    wins: Counter[int] = field(default_factory=Counter)
    bans: Counter[int] = field(default_factory=Counter)


def tally(matches: Iterable[Match]) -> Tally:
    """판수를 센다. **밴은 경기 단위다** — 한 경기에서 두 팀이 같은 챔피언을 밴해도 한 번.

    처음에는 밴 칸을 그대로 셌다. 그런데 `16_15` 를 u.gg 와 견주니 밴율만
    계통으로 높았다. 세어 보니 **경기의 45.2% 에서 같은 챔피언이 두 번 밴**됐고,
    칸으로 세면 9.635개/판인데 서로 다른 챔피언은 9.084종/판, u.gg 는 8.939 였다.
    u.gg 가 경기 단위로 센다는 뜻이라 정의를 맞췄다(2026-09-20).
    """
    t = Tally()
    for m in matches:
        t.games += 1
        for p in m.picks:
            t.picks[p.champion_id] += 1
            if p.win:
                t.wins[p.champion_id] += 1
        for c in set(m.bans):
            t.bans[c] += 1
    return t


@dataclass(frozen=True)
class Rates:
    """챔피언 id → 비율. 분모도 함께 둔다 — 이항 오차에 쓴다."""

    win: dict[int, float]
    pick: dict[int, float]
    ban: dict[int, float]
    win_n: dict[int, int]
    games: int
    ban_games: int


def rates(t: Tally) -> Rates:
    """패널이 u.gg 를 읽는 식과 같다 — 승/판 · 판/게임 · 밴/게임."""
    champions = set(t.picks) | set(t.bans)
    return Rates(
        win={c: t.wins[c] / t.picks[c] for c in t.picks if t.picks[c]},
        pick={c: t.picks[c] / t.games for c in champions},
        ban={c: t.bans[c] / t.games for c in champions},
        win_n=dict(t.picks),
        games=t.games,
        ban_games=t.games,
    )


def ranking(matches: Iterable[Match]) -> ChampionRanking:
    """직접 집계를 u.gg 응답과 **같은 모양**으로 바꾼다 — 패널이 그대로 읽게.

    같은 모양이지 **같은 출처가 아니다.** 한 시계열에 이어 붙이지 않는다(ADR 0010).

    포지션이 빈 칸인 경기는 뺀다. 역할별 합이 어긋나기 때문이고, 드물다 —
    `16_15` 4,500판 중 6판이었다.
    """
    totals: dict[tuple[int, str], list[int]] = {}
    bans: Counter[int] = Counter()
    games = 0
    last = 0
    for m in matches:
        if any(p.position not in ROLES for p in m.picks):
            continue
        games += 1
        last = max(last, m.start_ms)
        for p in m.picks:
            slot = totals.setdefault((p.champion_id, ROLES[p.position]), [0] * 8)
            slot[0] += int(p.win)
            slot[1] += 1
            slot[2] += p.damage
            slot[3] += p.gold
            slot[4] += p.kills
            slot[5] += p.deaths
            slot[6] += p.assists
            slot[7] += p.minions + p.monsters
        for champion in set(m.bans):  # 경기 단위 — 같은 챔피언을 두 팀이 밴해도 한 번
            bans[champion] += 1
    rows = tuple(
        ChampionRow(
            champion_id=champion,
            role=role,
            wins=v[0],
            matches=v[1],
            damage=v[2],
            gold=v[3],
            kills=v[4],
            deaths=v[5],
            assists=v[6],
            cs=v[7],
        )
        for (champion, role), v in sorted(totals.items())
    )
    return ChampionRanking(
        rows=rows,
        bans=dict(bans),
        # u.gg 는 밴 표에 자기 분모를 따로 두지만, 우리는 모든 경기에 밴 단계가 있다
        ban_denominator=games,
        games=games,
        updated_at=(
            datetime.fromtimestamp(last / 1000, UTC).isoformat(timespec="seconds")
            if last
            else ""
        ),
    )


def ugg_rates(ranking: ChampionRanking) -> Rates:
    """솔랭 지표를 같은 모양으로. 식은 `panel.build_rows` 와 같다."""
    matches: Counter[int] = Counter()
    wins: Counter[int] = Counter()
    for row in ranking.rows:
        matches[row.champion_id] += row.matches
        wins[row.champion_id] += row.wins
    return Rates(
        win={c: wins[c] / n for c, n in matches.items() if n},
        pick={c: n / ranking.games for c, n in matches.items()},
        ban={c: ranking.bans.get(c, 0) / ranking.ban_denominator for c in matches},
        win_n=dict(matches),
        games=ranking.games,
        ban_games=ranking.ban_denominator,
    )


def binomial_se(r: Rates) -> dict[str, dict[int, float]]:
    """u.gg 쪽 표준오차. 판수가 커서 작지만, 드문 챔피언에서는 무시할 수 없다."""

    def se(p: float, n: int) -> float:
        return math.sqrt(p * (1 - p) / n) if n else 0.0

    return {
        "win": {c: se(p, r.win_n[c]) for c, p in r.win.items()},
        "pick": {c: se(p, r.games) for c, p in r.pick.items()},
        "ban": {c: se(p, r.ban_games) for c, p in r.ban.items()},
    }


def player_bootstrap(
    matches: Sequence[Match], draws: int, seed: int
) -> dict[str, dict[int, float]]:
    """**뽑은 선수 단위로** 다시 뽑아 비율마다 표준오차를 낸다.

    경기를 한 판씩 독립으로 보면 표준오차가 작게 나온다 — 같은 선수의 경기는
    챔피언 폭과 실력이 닮았다. 그러면 표본 잡음이 「두 출처가 다르다」로 읽힌다.
    """
    players = sorted({(m.platform, m.player) for m in matches})
    row = {p: i for i, p in enumerate(players)}
    champions = sorted(
        {p.champion_id for m in matches for p in m.picks}
        | {c for m in matches for c in m.bans}
    )
    col = {c: j for j, c in enumerate(champions)}
    games = np.zeros(len(players))
    picks = np.zeros((len(players), len(champions)))
    wins = np.zeros_like(picks)
    bans = np.zeros_like(picks)
    for m in matches:
        i = row[(m.platform, m.player)]
        games[i] += 1
        for p in m.picks:
            picks[i, col[p.champion_id]] += 1
            if p.win:
                wins[i, col[p.champion_id]] += 1
        for c in set(m.bans):
            bans[i, col[c]] += 1

    if not players:
        raise ValueError("경기가 없다")
    rng = np.random.default_rng(seed)
    k = len(players)
    weight = rng.multinomial(k, np.full(k, 1.0 / k), size=draws).astype(float)
    g = weight @ games
    pk = weight @ picks
    with np.errstate(divide="ignore", invalid="ignore"), warnings.catch_warnings():
        # 어떤 표본에서 한 번도 안 뽑힌 챔피언은 승률이 정의되지 않는다(NaN)
        warnings.simplefilter("ignore", RuntimeWarning)
        win = np.nanstd((weight @ wins) / pk, axis=0, ddof=1)
    pick = (pk / g[:, None]).std(axis=0, ddof=1)
    ban = ((weight @ bans) / g[:, None]).std(axis=0, ddof=1)
    return {
        name: {c: float(values[j]) for c, j in col.items()}
        for name, values in (("win", win), ("pick", pick), ("ban", ban))
    }


@dataclass(frozen=True)
class Agreement:
    """한 비율에서 직접 집계와 솔랭 지표가 얼마나 같은가."""

    metric: str
    champions: int
    r: float
    mean_diff: float
    mean_abs_diff: float
    mean_z: float
    sd_z: float
    over_two: float
    systematic: float

    @property
    def within_noise(self) -> bool:
        """ADR 0010 의 판정 — 재기 전에 정한 기준이다."""
        return abs(self.mean_z) <= MEAN_Z_LIMIT and self.sd_z <= SD_Z_LIMIT


def agreement(
    metric: str,
    ours: Mapping[int, float],
    ours_se: Mapping[int, float],
    ref: Mapping[int, float],
    ref_se: Mapping[int, float],
    champions: Iterable[int],
) -> Agreement:
    """차이를 표준오차로 나눈 z 로 본다. 두 값이 같다면 z 는 평균 0 · 표준편차 1 근처다.

    **계통 차이**는 차이의 분산에서 표준오차² 의 평균을 뺀 것의 제곱근이다 —
    잡음으로 설명되지 않는 차이의 크기.
    """
    ids = [
        c
        for c in champions
        if ours_se.get(c, 0.0) ** 2 + ref_se.get(c, 0.0) ** 2 > 0
        and not math.isnan(ours_se.get(c, 0.0))
    ]
    if len(ids) < MIN_CHAMPIONS:
        raise ValueError(f"{metric}: 견줄 챔피언이 {len(ids)}종뿐이다")
    a = np.array([ours.get(c, 0.0) for c in ids])
    b = np.array([ref.get(c, 0.0) for c in ids])
    se = np.sqrt(
        np.array([ours_se.get(c, 0.0) ** 2 + ref_se.get(c, 0.0) ** 2 for c in ids])
    )
    diff = a - b
    z = diff / se
    systematic = math.sqrt(max(0.0, float(diff.var(ddof=1) - (se**2).mean())))
    return Agreement(
        metric=metric,
        champions=len(ids),
        r=float(np.corrcoef(a, b)[0, 1]) if a.std() and b.std() else math.nan,
        mean_diff=float(diff.mean()),
        mean_abs_diff=float(np.abs(diff).mean()),
        mean_z=float(z.mean()),
        sd_z=float(z.std(ddof=1)),
        over_two=float((np.abs(z) > 2).mean()),
        systematic=systematic,
    )


def compare(
    matches: Sequence[Match],
    ranking: ChampionRanking,
    draws: int,
    seed: int,
    min_games: int = MIN_GAMES,
) -> tuple[Agreement | None, Agreement, Agreement]:
    """같은 패치의 직접 집계와 솔랭 지표를 승률 · 픽률 · 밴율로 견준다.

    챔피언은 u.gg 에 있는 것으로 잡는다. 직접 집계에 없으면 픽률 · 밴율이 0 이다.
    **승률은 None 일 수 있다** — 두 쪽 다 `min_games` 판을 넘는 챔피언이 모자라면
    (표본이 작거나 플랫폼 하나만 볼 때) 견주지 않는다.
    """
    ours = rates(tally(matches))
    ref = ugg_rates(ranking)
    boot = player_bootstrap(matches, draws, seed)
    ref_se = binomial_se(ref)
    champions = sorted(ref.pick)
    win_ids = [
        c
        for c in champions
        if ours.win_n.get(c, 0) >= min_games and ref.win_n.get(c, 0) >= min_games
    ]

    def win_n(c: int) -> int:
        return ours.win_n.get(c, 0)

    def games(_: int) -> int:
        return ours.games

    win_se = floor_se(boot["win"], ref.win, win_n, win_ids)
    pick_se = floor_se(boot["pick"], ref.pick, games, champions)
    ban_se = floor_se(boot["ban"], ref.ban, games, champions)
    win = (
        agreement("승률", ours.win, win_se, ref.win, ref_se["win"], win_ids)
        if len(win_ids) >= MIN_CHAMPIONS
        else None
    )
    return (
        win,
        agreement("픽률", ours.pick, pick_se, ref.pick, ref_se["pick"], champions),
        agreement("밴율", ours.ban, ban_se, ref.ban, ref_se["ban"], champions),
    )


def floor_se(
    boot: Mapping[int, float],
    ref: Mapping[int, float],
    n: Callable[[int], int],
    champions: Iterable[int],
) -> dict[int, float]:
    """부트스트랩 표준오차에 바닥을 둔다.

    **직접 집계에서 한 번도 안 나온 챔피언은 부트스트랩 표준오차가 0 이다.**
    표본이 작아서 못 본 것인데 z 가 무한대로 튄다. 두 출처가 같다고 놓았을 때의
    이항 오차 — 솔랭 지표의 비율과 직접 집계의 분모 — 를 바닥으로 쓴다.
    """
    out: dict[int, float] = {}
    for c in champions:
        p, m = ref.get(c, 0.0), n(c)
        base = math.sqrt(p * (1 - p) / m) if m else 0.0
        b = boot.get(c, 0.0)
        out[c] = max(base, 0.0 if math.isnan(b) else b)
    return out
