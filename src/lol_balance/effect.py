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
    # 프로 경기 픽·밴율. **한쪽이라도 없으면 None** — 모르는 것을 0 으로 적지 않는다.
    pro_before: float | None = None
    pro_after: float | None = None

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

    @property
    def closer(self) -> bool:
        """**균형(5할)에 가까워졌나.** 「의도대로 갔나」와 다른 질문이다.

        너프해서 승률이 내려갔어도 이미 5할 아래였으면 균형에서는 멀어진다.
        둘이 다른 답을 내는 것이 [results](../../docs/results/README.md)의 ③′ 다.
        """
        return abs(self.after - 0.5) < abs(self.before - 0.5)

    @property
    def pro_change(self) -> float | None:
        """프로 픽·밴율이 자기 값 대비 얼마나 움직였나.

        **%p 가 아니라 비율이다.** 챔피언마다 자릿수가 달라(0.55 와 0.04) %p 로는
        비교가 안 된다.

        조정 전이 0 인 경우를 둘로 가른다.

            0 → 0        변화 0 으로 센다. 프로 경기에 안 나오던 챔피언이
                         조정 뒤에도 안 나온 것은 **관측된 사실**이다
            0 → 양수      `None`. 비율이 무한이라 중앙값에 못 넣는다

        **앞엣것을 빼면 「그 외」 무리가 통째로 사라진다** — 프로 경기에 거의
        안 나오는 챔피언이 그 무리의 대부분이라, 빼면 남는 것이 편향된다.
        """
        if self.pro_before is None or self.pro_after is None:
            return None
        if self.pro_before == 0:
            return 0.0 if self.pro_after == 0 else None
        return self.pro_after / self.pro_before - 1


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
            pro_before=a.pro_presence,
            pro_after=b.pro_presence,
        )
        for a, b in paired
        if a.direction_next in _INTENDED
    )


def balance_control(
    current: tuple[PanelRow, ...], following: tuple[PanelRow, ...]
) -> tuple[int, int]:
    """**조정 안 된 챔피언도 5할에 가까워진다.** 그 비율을 세어 돌려준다.

    승률은 가만 둬도 5할 쪽으로 되돌아간다(평균 회귀). 그래서 「조정 뒤 균형에
    가까워졌다」를 성과로 읽으려면 **아무것도 안 했을 때의 비율을 빼야 한다.**

    `(가까워진 수, 전체)` 를 준다 — 패치마다 세어 합쳐 쓰라고 비율이 아니라
    개수로 돌려준다.
    """
    after = {r.champion_id: r for r in following}
    pairs = [
        (a, after[a.champion_id])
        for a in current
        if a.champion_id in after and not a.adjusted_next
    ]
    closer = sum(abs(b.win_rate - 0.5) < abs(a.win_rate - 0.5) for a, b in pairs)
    return closer, len(pairs)
