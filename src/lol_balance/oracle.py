"""Oracle's Elixir 프로 경기 기록에서 픽·밴을 센다.

**솔랭에 없는 신호다.** 프로 경기는 표본이 작지만(패치당 수백 경기) 그 안에서
무엇이 강한지를 팀들이 밴으로 표시한다. 방향 예측에서 실제로 값을 한다 —
회귀가 `AUC 0.833 → 0.879`([results](../../docs/results/README.md)).

**① 대상 예측에는 도움이 안 된다.** 같은 피처를 넣어도 AUC 가 0.596 에서
0.597 로 그대로다. **어느 쪽인지 아는 데는 쓰이고 누구인지 아는 데는 안 쓰인다.**

**본 분석 밖이다.** 이 프로젝트의 질문은 「지금 솔랭 지표로 다음 패치를 예측할
수 있는가」라서, 프로 경기를 쓰는 arm 은 확장 분석으로 따로 둔다. 용어는
[glossary](../../docs/glossary.md) 참조.

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
from dataclasses import dataclass
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
