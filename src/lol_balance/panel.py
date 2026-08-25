"""패치 × 챔피언 표. **모델이 보는 입력이 여기서 만들어진다.**

한 줄이 「16_9 패치의 아리」다. 승률·픽률·밴율과 직전 패치 대비 변화를 담고,
**다음 패치에서 조정됐는지**를 라벨로 붙인다.

라벨에 LLM 이 필요 없다. 위키가 `data-champion` 속성에 챔피언 이름을 넣어 두므로
「누가 조정됐나」는 파서가 그냥 준다(`patchnotes.champion_changes`). LLM 은 방향과
수치를 뽑을 때 필요하고, 그것은 2단계 이후의 일이다.

**연결 키는 이름이 아니라 숫자 id 다.** u.gg 는 챔피언을 숫자로 부르고 위키는
표시 이름을 쓴다. Data Dragon 의 `key` → `name` 사전으로 잇는다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from lol_balance.ddragon import standard_champions
from lol_balance.ugg import ChampionRanking, ChampionRow

# 판수가 너무 적은 챔피언은 승률이 요동친다. 그 패치·그 챔피언 전체 판수 기준.
MIN_MATCHES = 200

# 확정한 범위. 에메랄드 티어가 13.13 무렵 도입돼 그 전과 모집단이 다르므로
# 13_14 부터다. 근거는 `docs/spec/data-sources.md`.
PATCH_SEQUENCE: tuple[str, ...] = tuple(
    [f"13_{i}" for i in range(14, 25)]
    + [f"14_{i}" for i in range(1, 25)]
    + [f"15_{i}" for i in range(1, 25)]
    + [f"16_{i}" for i in range(1, 16)]
)
_INDEX = {patch: i for i, patch in enumerate(PATCH_SEQUENCE)}


def patch_index(patch: str) -> int:
    """패치의 시간 순서. **문자열 정렬로는 안 된다** — `13_9` 가 `13_10` 뒤로 간다."""
    return _INDEX[patch]


@dataclass(frozen=True)
class PanelRow:
    """한 패치·한 챔피언."""

    patch: str
    patch_index: int
    champion_id: int
    champion: str
    main_role: str
    role_count: int

    # 수준 피처 — 그 패치의 상태
    win_rate: float
    pick_rate: float
    # 밴 데이터가 통째로 빠진 패치가 있다(14_5 — 게임 69,951판인데 밴 항목 0개).
    # **0 으로 채우지 않는다.** 「밴이 없었다」와 「밴 데이터가 없다」는 다르다.
    ban_rate: float | None
    matches: int
    kills: float
    deaths: float
    assists: float
    cs: float
    gold: float
    damage: float

    # 추세 피처 — 직전 패치 대비. 직전이 없으면 None 이고, 0 으로 채우지 않는다.
    # 「변화 없음」과 「모름」이 같아지면 그 사실이 조용히 사라진다.
    d_win_rate: float | None
    d_pick_rate: float | None
    d_ban_rate: float | None

    # 라벨 — 다음 패치에서 조정됐는가
    adjusted_next: bool

    @property
    def wr_gap(self) -> float:
        """승률이 5할에서 얼마나 떨어져 있나.

        **승률 자체는 단조롭지 않다.** 조정되는 쪽은 양 끝이다 — 강하면 너프,
        약하면 버프. 그래서 승률로 줄을 세우면 무작위보다 못하고(AUC 0.451),
        거리로 바꾸면 단조가 된다.

            승률 0.460 미만    36.5% 가 다음 패치에 조정됨
            승률 0.495~0.515   14.8%
            승률 0.540 초과    30.0%
        """
        return abs(self.win_rate - 0.5)

    @property
    def has_trend(self) -> bool:
        return self.d_win_rate is not None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def champion_names(ddragon_data: dict[str, Any]) -> dict[int, str]:
    """Data Dragon `data` → {숫자 key: 표시 이름}.

    게임 모드 변형(`Jade_*`)을 걸러낸다. 안 걸러내면 한 버전에서 60종이 통째로
    끼어든다.
    """
    out: dict[int, str] = {}
    for entry in standard_champions(ddragon_data).values():
        key = entry.get("key", "")
        if isinstance(key, str) and key.isdigit():
            out[int(key)] = entry.get("name", "")
    return out


def _fold(rows: tuple[ChampionRow, ...]) -> tuple[ChampionRow, int, str]:
    """한 챔피언의 역할별 행을 합친다. 주 역할은 판수가 가장 많은 역할이다."""
    main = max(rows, key=lambda r: r.matches)
    return main, len(rows), main.role


def patch_rows(
    patch: str,
    ranking: ChampionRanking,
    names: dict[int, str],
    adjusted: frozenset[str],
    previous: dict[int, PanelRow] | None = None,
    min_matches: int = MIN_MATCHES,
) -> tuple[PanelRow, ...]:
    """한 패치의 표를 만든다.

    `adjusted` 는 **다음 패치** 노트에 나온 챔피언 이름 집합이다. `previous` 는
    직전 패치의 행(챔피언 id 로 색인)이고, 없으면 추세 피처가 None 으로 남는다.
    """
    by_champion: dict[int, list[ChampionRow]] = {}
    for row in ranking.rows:
        by_champion.setdefault(row.champion_id, []).append(row)

    out: list[PanelRow] = []
    for champion_id, rows in by_champion.items():
        name = names.get(champion_id)
        if name is None:
            continue  # Data Dragon 에 없는 id — 게임 모드 변형이거나 신규 직후
        matches = sum(r.matches for r in rows)
        if matches < min_matches:
            continue
        wins = sum(r.wins for r in rows)
        main, role_count, main_role = _fold(tuple(rows))

        win_rate = wins / matches
        pick_rate = matches / ranking.games
        # `ChampionRanking.ban_rate` 는 분모가 0이면 나눗셈에서 죽는다.
        # 이 모듈의 다른 비율들과 같은 규약이다 — 호출 전에 부르는 쪽이 거른다.
        ban_rate = ranking.ban_rate(champion_id) if ranking.ban_denominator else None
        prior = (previous or {}).get(champion_id)

        out.append(
            PanelRow(
                patch=patch,
                patch_index=patch_index(patch),
                champion_id=champion_id,
                champion=name,
                main_role=main_role,
                role_count=role_count,
                win_rate=win_rate,
                pick_rate=pick_rate,
                ban_rate=ban_rate,
                matches=matches,
                kills=sum(r.kills for r in rows) / matches,
                deaths=sum(r.deaths for r in rows) / matches,
                assists=sum(r.assists for r in rows) / matches,
                cs=sum(r.cs for r in rows) / matches,
                gold=sum(r.gold for r in rows) / matches,
                damage=sum(r.damage for r in rows) / matches,
                d_win_rate=None if prior is None else win_rate - prior.win_rate,
                d_pick_rate=None if prior is None else pick_rate - prior.pick_rate,
                d_ban_rate=(
                    None
                    if prior is None or ban_rate is None or prior.ban_rate is None
                    else ban_rate - prior.ban_rate
                ),
                adjusted_next=name in adjusted,
            )
        )

    # 정렬을 고정한다. 순서가 흔들리면 「다시 만들면 같은 값」이 성립하지 않는다.
    return tuple(sorted(out, key=lambda r: r.champion_id))
