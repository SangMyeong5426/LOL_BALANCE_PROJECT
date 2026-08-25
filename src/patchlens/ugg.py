"""u.gg `champion_ranking` 응답 해독.

이 응답은 필드 이름이 없는 **위치 기반 배열**이다. 각 숫자가 무엇인지는
문서에 없어서 직접 알아냈고, 근거는 `docs/spec/ugg-format.md` 에 있다.

핵심 근거 하나만 옮기면 — **전체 승/판 비가 정확히 50.00% 로 떨어진다.**
한 게임에 승자와 패자가 다섯 명씩이므로 그래야만 하고, 우연히 맞을 수 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 응답 최상위 배열의 자리
_ROLES, _BANS, _UPDATED_AT, _GAMES = 0, 1, 2, 3

# 챔피언 항목 배열의 자리
_CHAMP_ID, _MATCHUPS, _WINS, _MATCHES = 0, 1, 2, 3
_DAMAGE, _GOLD, _KILLS, _DEATHS, _ASSISTS, _CS = 4, 5, 6, 7, 8, 9

_BAN_TOTAL_KEY = "total_matches"
_BAN_EMPTY_KEY = "-1"  # 밴을 하지 않은 슬롯


@dataclass(frozen=True)
class ChampionRow:
    """한 패치·한 역할에서의 챔피언 한 종."""

    champion_id: int
    role: str
    wins: int
    matches: int
    damage: int
    gold: int
    kills: int
    deaths: int
    assists: int
    cs: int

    @property
    def win_rate(self) -> float:
        """승률. 판수가 0이면 정의되지 않으므로 호출 전에 걸러야 한다."""
        return self.wins / self.matches


@dataclass(frozen=True)
class ChampionRanking:
    """`champion_ranking` 응답 하나."""

    rows: tuple[ChampionRow, ...]
    bans: dict[int, int]
    ban_denominator: int
    games: int
    updated_at: str

    def ban_rate(self, champion_id: int) -> float:
        """밴율. 밴 데이터의 분모는 게임 수가 아니라 응답이 따로 준 값이다."""
        return self.bans.get(champion_id, 0) / self.ban_denominator

    def pick_rate(self, row: ChampionRow) -> float:
        """역할 내 픽률.

        분모를 **그 역할의 총 판수**로 잡는다. 그러면 역할 안에서 합이 정확히
        100% 가 된다. 게임 수로 나누는 정의도 있으나 단조 변환이라 예측에는
        차이가 없고, 합이 1 이 되는 쪽이 해석하기 쉽다.
        """
        role_total = sum(r.matches for r in self.rows if r.role == row.role)
        return row.matches / role_total


def parse_champion_ranking(payload: list[Any]) -> ChampionRanking:
    """`champion_ranking` 응답을 구조화한다."""
    if len(payload) <= _GAMES:
        raise ValueError(f"최상위 배열이 짧다: 길이 {len(payload)}")

    rows: list[ChampionRow] = []
    for role, entries in payload[_ROLES].items():
        for e in entries:
            rows.append(
                ChampionRow(
                    champion_id=int(e[_CHAMP_ID]),
                    role=role,
                    wins=e[_WINS],
                    matches=e[_MATCHES],
                    damage=e[_DAMAGE],
                    gold=e[_GOLD],
                    kills=e[_KILLS],
                    deaths=e[_DEATHS],
                    assists=e[_ASSISTS],
                    cs=e[_CS],
                )
            )

    raw_bans = payload[_BANS]
    bans = {
        int(k): v
        for k, v in raw_bans.items()
        if k not in (_BAN_TOTAL_KEY, _BAN_EMPTY_KEY)
    }
    return ChampionRanking(
        rows=tuple(rows),
        bans=bans,
        ban_denominator=raw_bans[_BAN_TOTAL_KEY],
        games=int(payload[_GAMES]),
        updated_at=payload[_UPDATED_AT],
    )


def check_win_rate_identity(ranking: ChampionRanking, tolerance: float = 2e-3) -> None:
    """전체 승률이 50% 근처인지 확인한다.

    한 게임에 승자와 패자가 다섯 명씩이므로 50% 여야 한다. 어긋나면 자리
    해석이 틀렸거나 응답이 잘린 것이다. 수집할 때마다 돌린다 — 형식이 조용히
    바뀌면 이 검사가 먼저 걸린다.

    **정확히 50% 는 아니다.** 실제 수집물에서 0.01~0.04% 어긋나는 패치가
    나왔다(14_10 49.9899%, 14_14 49.9619%, 16_4 49.9871%). 리메이크나 조기
    종료처럼 승패가 대칭이 아닌 판이 섞이면 그만큼 벌어진다.

    허용오차를 0.2% 로 둔다 — 관측된 최대 어긋남의 다섯 배이고, **자리가 밀리면
    이보다 훨씬 크게 어긋나므로** 잡아내는 능력은 그대로다.
    """
    wins = sum(r.wins for r in ranking.rows)
    matches = sum(r.matches for r in ranking.rows)
    if matches == 0:
        raise ValueError("판수 합계가 0이다")
    rate = wins / matches
    if abs(rate - 0.5) > tolerance:
        raise ValueError(
            f"전체 승률이 50% 가 아니다: {rate:.6%} (승 {wins}, 판 {matches})"
        )


def check_games_identity(ranking: ChampionRanking) -> None:
    """선수-게임 합계가 게임 수의 10배인지 확인한다.

    한 게임에 열 명이 들어가므로 그래야 한다. 역할이 빠졌거나 응답이 잘리면
    여기서 걸린다.
    """
    matches = sum(r.matches for r in ranking.rows)
    if matches != ranking.games * 10:
        raise ValueError(
            f"판수 합계가 게임 수의 10배가 아니다: {matches} vs {ranking.games * 10}"
        )
