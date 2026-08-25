"""조정의 효과 — **먹혔나.**

사슬의 세 번째 칸이다. 앞 두 칸은 「라이엇이 무엇을 보고 움직이나」였고,
이것은 「그 조정이 의도한 방향으로 갔나」를 묻는다.

    통계 상태  ──→  조정 행위  ──→  조정 후 결과
                                    ← 여기

**최대 교란은 「같은 패치에 다른 것도 바뀐다」다.** 아이템·룬·정글 몬스터가
함께 바뀌면 승률이 그것 때문에 움직인다. 그래서 **조정 안 된 챔피언을
대조군으로 쓴다** — 그들이 공통 변화를 흡수한다. 두 번 빼는 설계다.

    효과 = (조정된 챔피언의 승률 변화) − (같은 패치 대조군의 평균 변화)

이 문서의 어떤 숫자도 「라이엇이 잘했다·못했다」를 말하지 않는다. **측정이지
판정이 아니다** — `README.md` 의 「증명하지 않는 것」에 적혀 있다.
"""

from __future__ import annotations

from dataclasses import dataclass

from lol_balance.panel import PanelRow

# 의도한 방향. 너프는 승률이 내려가야 하고 버프는 올라가야 한다.
_INTENDED = {"nerf": -1.0, "buff": +1.0}


@dataclass(frozen=True)
class Outcome:
    """한 챔피언의 한 조정이 낸 결과."""

    patch: str
    champion: str
    direction: str
    source: str
    before: float
    after: float
    baseline_shift: float

    @property
    def raw_shift(self) -> float:
        """승률이 실제로 얼마나 움직였나."""
        return self.after - self.before

    @property
    def adjusted_shift(self) -> float:
        """대조군의 공통 변화를 뺀 뒤의 움직임. **이것이 효과다.**"""
        return self.raw_shift - self.baseline_shift

    @property
    def worked(self) -> bool:
        """의도한 방향으로 갔나. 0 이면 안 간 것으로 센다."""
        return self.adjusted_shift * _INTENDED[self.direction] > 0


def outcomes(
    current: tuple[PanelRow, ...], following: tuple[PanelRow, ...]
) -> tuple[Outcome, ...]:
    """인접한 두 패치로 효과를 잰다.

    `current` 는 조정 **직전** 패치, `following` 은 조정이 적용된 패치다.
    양쪽에 다 있는 챔피언만 본다 — 한쪽이 없으면 변화를 정의할 수 없다.
    """
    after = {r.champion_id: r for r in following}
    paired = [(r, after[r.champion_id]) for r in current if r.champion_id in after]
    if not paired:
        return ()

    control = [b.win_rate - a.win_rate for a, b in paired if not a.adjusted_next]
    if not control:
        return ()
    baseline = sum(control) / len(control)

    return tuple(
        Outcome(
            patch=a.patch,
            champion=a.champion,
            direction=a.direction_next,
            source=a.direction_source or "",
            before=a.win_rate,
            after=b.win_rate,
            baseline_shift=baseline,
        )
        for a, b in paired
        if a.direction_next in _INTENDED
    )
