"""미리 적어 둔 예측을 채점한다 — **모델을 다시 돌리지 않는다.**

`run-live-predict --score` 는 목록을 **지금 코드로 다시 낸다.** 코드를 고치면 같은
묶음에서도 다른 목록이 나오므로, 그것으로는 적어 둔 예측을 채점할 수 없다. 실제로
두 번 걸렸다 — `16_20` 두 벌은 `runs/before-kda-fix/` 의 순위를 손으로 맞춰 셌고,
`16_19` 를 다시 셀 때는 대상 48 · 80 · 167종이 아예 되살아나지 않았다.

그래서 **적어 둔 목록과 그때의 대상 전체를 텍스트로 커밋해 두고**
(`ground_truth/predictions/`), 채점은 그 파일과 방향 라벨만 읽는다. 모델도,
원자료도, 시드도 필요 없다.

## 두 기준을 같이 낸다

버그 수정은 밸런스 의도가 아니다([ADR 0015](../../docs/adr/0015-bug-fixes-are-not-balance-intent.md)).
`16_20` 은 그것을 뺀 수를 따로 적었고 `16_19` 는 안 적어서 두 지점을 나란히 읽을 수
없었다. **같은 함수가 둘 다 내도록 해서 그 일이 다시 안 생기게 한다.**

    전부        노트에 이름이 오른 것 전부를 정답으로 센다
    의도만      `adjust` 라벨을 뺀다 — 버그 수정 · 표시기 · VO
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lol_balance.direction import Direction


@dataclass(frozen=True)
class Registered:
    """미리 적어 둔 예측 한 벌.

    `targets` 를 통째로 들고 있는 것이 핵심이다. **기준선이 대상 수에 달려 있어서**,
    그것이 없으면 「상위 5 중 1종」이 기준선 위인지 아래인지 말할 수 없다.
    """

    name: str
    base: str
    target: str
    snapshot: str
    recorded: str
    targets: tuple[str, ...]
    nerf: tuple[str, ...]
    buff: tuple[str, ...]

    def side(self, want: Direction) -> tuple[str, ...]:
        return self.nerf if want == "nerf" else self.buff


@dataclass(frozen=True)
class SideScore:
    """한쪽(너프 또는 버프) 성적."""

    want: Direction
    top: int
    hits: tuple[str, ...]
    """상위 `top` 중 실제로 조정된 것."""
    intended: tuple[str, ...]
    """그중 버그 수정(`adjust`)이 아닌 것."""
    matched: tuple[str, ...]
    """그중 방향까지 `want` 와 같은 것."""
    unlabelled: tuple[str, ...]
    """조정은 됐는데 방향 라벨이 없는 것. **틀린 것이 아니라 못 잰 것이다.**"""
    expected: float
    """기준선이면 몇 종이 걸리나 — 대상 중 조정된 비율 × `top`."""
    expected_intended: float


def positives(
    registered: Registered,
    answer: Sequence[str] | set[str],
    directions: Mapping[str, Direction],
) -> tuple[frozenset[str], frozenset[str]]:
    """대상 안에서 실제로 조정된 것과, 그중 밸런스 의도가 있는 것.

    **대상 밖은 안 센다.** 모델이 고를 수 없었던 챔피언을 기준선에 넣으면 기준선만
    낮아진다 — `16_20` 6,000판에서 정답 여섯이 200판 문턱 아래였다.
    """
    pool = frozenset(registered.targets)
    touched = frozenset(c for c in answer if c in pool)
    intended = frozenset(c for c in touched if directions.get(c) != "adjust")
    return touched, intended


def score_side(
    registered: Registered,
    want: Direction,
    answer: Sequence[str] | set[str],
    directions: Mapping[str, Direction],
    top: int,
) -> SideScore:
    touched, intended_pool = positives(registered, answer, directions)
    picked = registered.side(want)[:top]
    hits = tuple(c for c in picked if c in touched)
    intended = tuple(c for c in hits if c in intended_pool)
    matched = tuple(c for c in hits if directions.get(c) == want)
    unlabelled = tuple(c for c in hits if c not in directions)
    size = len(registered.targets)
    return SideScore(
        want=want,
        top=top,
        hits=hits,
        intended=intended,
        matched=matched,
        unlabelled=unlabelled,
        expected=len(touched) / size * top if size else 0.0,
        expected_intended=len(intended_pool) / size * top if size else 0.0,
    )
