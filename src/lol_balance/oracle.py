"""Oracle's Elixir 프로 경기 기록에서 픽·밴을 센다.

**솔랭에 없는 신호다.** 프로 경기는 표본이 작지만(패치당 수백 경기) 그 안에서
무엇이 강한지를 팀들이 밴으로 표시한다. 방향 예측에서 실제로 값을 한다 —
회귀가 `AUC 0.833 → 0.879`([results](../../docs/results/README.md)).

**① 대상에서는 부호가 반대라 상쇄된다.** 너프된 챔피언은 「조정 안 됨」과
AUC 0.658 로 갈리는데 버프된 챔피언은 0.473 이다 — 합치면 0.546 으로 뭉개진다.
그래서 회귀에 그냥 넣으면 0.596 → 0.597 로 안 움직인다.

**부스팅(`A7p` 25.8% → 27.9%)이나 방향을 좁힌 arm(`A7buffp` AUC 0.703 → 0.727)
에서는 살아난다.** 「신호가 없다」가 아니라 「묻는 방식이 신호를 지웠다」였다.

**본 분석에 들어간다.** 한때 확장 분석으로 따로 뒀는데 도구가 이미 프로를 쓰고
있어 문서만 어긋나 있었다([ADR 0008](../../docs/adr/0008-pro-play-in-the-main-analysis.md)).
용어는 [glossary](../../docs/glossary.md) 참조.

## 날짜 — 패치로만 묶으면 절반이 다음 패치 출시 뒤다

`read_pro` 는 패치 번호로 묶고 날짜를 안 본다. 그날 알 수 있었던 경기만 세려면
`read_games` 로 경기 단위로 읽어 `before_release` · `recent` 로 자른다. 머리
숫자는 그렇게 세도 유지됐다(`scripts/run-pro-timing`).

## 표기를 믿지 않는다

이 저장소 규칙대로 패치 이름을 그대로 쓰지 않는다. **Oracle's 는 한 자리
마이너를 0으로 채운다.**

    우리      14_1      15_9
    Oracle    14.01     15.09

정규화하지 않으면 74패치 중 29개가 「프로 경기 없음」으로 잘못 잡힌다. 실제로
겪었다 — 정규화 후 72/74 가 됐고, 남은 둘(13.23 · 14.24)은 12월 비시즌이다.

## 한 경기가 12줄이다

선수 10줄 + 팀 2줄. **픽은 선수 줄에, 밴은 팀 줄에** 있다. 안 가르면 밴이
팀당 5개씩 두 번 세어진다.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

# 팀 줄을 알아보는 표시. 나머지는 선수 줄이다.
TEAM_ROW = "team"

# 한 팀이 거는 밴 수. 열 이름이 `ban1` … `ban5` 다.
BAN_SLOTS = 5

# 큰 필드가 있는 행이 있어 기본 한도로는 읽다가 죽는다.
FIELD_LIMIT = 10**7


@dataclass(frozen=True)
class ProRates:
    """한 패치 · 한 챔피언의 프로 픽·밴율.

    **비율로 둔다.** 패치마다 경기 수가 27~800 으로 크게 달라서 횟수를 그대로
    쓰면 「그 패치에 경기가 많았다」가 신호로 섞인다.
    """

    pick_rate: float
    ban_rate: float

    @property
    def presence(self) -> float:
        """뽑히거나 밴당한 비율. 문서에서 부르는 이름이 **「프로 픽·밴율」**이다.

        **1.0 을 넘을 수 있다.** 데마시아 컵(`DCup`) 같은 형식은 **양 팀이 같은
        챔피언을 뽑는다** — 15.24 의 한 경기에서 Jayce 가 양쪽 정글로 나왔다.
        데이터 오류가 아니라 실제 경기다.

        드물어서 그대로 둔다 — (패치, 챔피언) 짝 11,570 중 **2건(0.02%)**이고
        최대가 1.051 이다. 1.0 으로 자르면 그 형식을 없는 것으로 만든다.
        """
        return self.pick_rate + self.ban_rate


def normalise(patch: str) -> str:
    """`14.01` → `14.1`. **이름을 그대로 믿지 않는다.**

    숫자로 못 읽으면 원문을 그대로 돌려준다 — 조용히 바꾸지 않는다.
    """
    text = (patch or "").strip()
    if "." not in text:
        return text
    major, minor = text.split(".", 1)
    try:
        return f"{int(major)}.{int(minor)}"
    except ValueError:
        return text


def read_pro(root: Path) -> dict[str, dict[str, ProRates]]:
    """`data/oracle/*.csv` → 패치 → 챔피언 → 비율.

    **경기 수로 나눈다.** 분모는 그 패치의 고유 `gameid` 수다.
    """
    picks: Counter[tuple[str, str]] = Counter()
    bans: Counter[tuple[str, str]] = Counter()
    games: defaultdict[str, set[str]] = defaultdict(set)

    csv.field_size_limit(FIELD_LIMIT)
    for path in sorted(root.glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                patch = normalise(row.get("patch", ""))
                if not patch:
                    continue
                games[patch].add(row.get("gameid", ""))
                if (row.get("position") or "") == TEAM_ROW:
                    for slot in range(1, BAN_SLOTS + 1):
                        banned = (row.get(f"ban{slot}") or "").strip()
                        if banned:
                            bans[(patch, banned)] += 1
                else:
                    champion = (row.get("champion") or "").strip()
                    if champion:
                        picks[(patch, champion)] += 1

    out: dict[str, dict[str, ProRates]] = {}
    for patch, ids in games.items():
        total = len(ids)
        if not total:
            continue
        names = {c for (p, c) in picks if p == patch} | {
            c for (p, c) in bans if p == patch
        }
        out[patch] = {
            name: ProRates(picks[(patch, name)] / total, bans[(patch, name)] / total)
            for name in names
        }
    return out


# ── 경기 단위 — 「그때 알 수 있었던 것」만 세려고 ─────────────────────────
#
# `read_pro` 는 경기를 **패치 번호로만** 묶는다. 프로 리그는 라이브 패치를 늦게
# 따라가므로, 패치 t 로 치른 경기의 절반가량이 **t+1 이 이미 나온 뒤**에 열렸다
# (2026-09-18 실측 49.5%). 그 경기들은 t → t+1 예측을 하는 시점에는 아직 없다.
# 아래는 그것을 가려 세는 길이다. `read_pro` 는 그대로 둔다 — 결과를 견주려면
# 기존 집계가 그대로 있어야 한다.


@dataclass(frozen=True)
class Game:
    """프로 경기 하나 — 어느 패치로, 언제, 누가 뽑히고 밴당했나."""

    patch: str
    day: date | None
    picks: tuple[str, ...]
    bans: tuple[str, ...]


def _day(text: str | None) -> date | None:
    try:
        return datetime.strptime((text or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def read_games(root: Path) -> dict[str, Game]:
    """`data/oracle/*.csv` → `gameid` → 경기. **픽은 선수 줄에서, 밴은 팀 줄에서.**"""
    patch_of: dict[str, str] = {}
    day_of: dict[str, date | None] = {}
    picks: defaultdict[str, list[str]] = defaultdict(list)
    bans: defaultdict[str, list[str]] = defaultdict(list)

    csv.field_size_limit(FIELD_LIMIT)
    for path in sorted(root.glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                patch = normalise(row.get("patch", ""))
                game = row.get("gameid", "")
                if not patch:
                    continue
                patch_of.setdefault(game, patch)
                if game not in day_of or day_of[game] is None:
                    day_of[game] = _day(row.get("date"))
                if (row.get("position") or "") == TEAM_ROW:
                    for slot in range(1, BAN_SLOTS + 1):
                        banned = (row.get(f"ban{slot}") or "").strip()
                        if banned:
                            bans[game].append(banned)
                else:
                    champion = (row.get("champion") or "").strip()
                    if champion:
                        picks[game].append(champion)

    return {
        game: Game(patch, day_of.get(game), tuple(picks[game]), tuple(bans[game]))
        for game, patch in patch_of.items()
    }


def rates(games: Iterable[Game]) -> dict[str, ProRates]:
    """경기 묶음 → 챔피언별 비율. **분모는 그 묶음의 경기 수다** — `read_pro` 와 같다."""
    chosen = list(games)
    if not chosen:
        return {}
    picked: Counter[str] = Counter()
    banned: Counter[str] = Counter()
    for game in chosen:
        picked.update(game.picks)
        banned.update(game.bans)
    total = len(chosen)
    return {
        name: ProRates(picked[name] / total, banned[name] / total)
        for name in set(picked) | set(banned)
    }


def before_release(
    games: Mapping[str, Game],
    cutoffs: Mapping[str, date],
    offset: int = 0,
) -> dict[str, dict[str, ProRates]]:
    """패치 → **그 패치로 치른 경기 중 경계 전에 열린 것만**으로 낸 비율.

    경계는 `cutoffs[패치]` — 다음 패치의 출시일이다. `offset` 일만큼 더 앞당길 수
    있다(개발사는 출시 전에 이미 정한다). 경계 전 경기가 하나도 없는 패치는 빠진다
    — 그때는 프로 지표가 **없었던** 것이다. 날짜를 못 읽은 경기도 뺀다.
    """
    kept: defaultdict[str, list[Game]] = defaultdict(list)
    for game in games.values():
        cutoff = cutoffs.get(game.patch)
        if cutoff is None or game.day is None:
            continue
        if game.day < cutoff - timedelta(days=offset):
            kept[game.patch].append(game)
    return {patch: rates(chosen) for patch, chosen in kept.items()}


def recent(
    games: Mapping[str, Game],
    cutoffs: Mapping[str, date],
    days: int,
) -> dict[str, dict[str, ProRates]]:
    """패치 → **경계 직전 `days` 일 동안 열린 경기**로 낸 비율. 패치를 가리지 않는다.

    예측하는 날 볼 수 있던 「최근 프로 흐름」이다. 프로 리그가 앞 패치로 치르고
    있어도 그 경기들이 들어간다 — 그것이 그날 실제로 보이던 것이다.
    """
    dated = [g for g in games.values() if g.day is not None]
    out: dict[str, dict[str, ProRates]] = {}
    for patch, cutoff in cutoffs.items():
        start = cutoff - timedelta(days=days)
        window = [g for g in dated if g.day is not None and start <= g.day < cutoff]
        if window:
            out[patch] = rates(window)
    return out
