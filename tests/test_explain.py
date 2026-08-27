"""근거·경고 테스트.

**여기서 지키는 것은 둘이다.**

    지어내지 않는다   근거는 전부 행에 있는 값에서 온다
    항상 뜨지 않는다  걸릴 때만 뜨는 것이 경고다

둘째가 특히 중요하다 — 버프 후보는 정의상 승률이 5할 아래라, 「5할 아래다」를
거기 띄우면 전부에 붙어 아무 뜻이 없어진다. 실제로 한 번 그렇게 만들었다.
"""

from __future__ import annotations

from conftest import PanelRowFactory
from lol_balance.explain import PRO_REGULAR, reasons, warnings
from lol_balance.retrieval import Case
from lol_balance.rules import Condition, Rule


def texts(notes: list) -> str:
    return " / ".join(n.text for n in notes)


def warns(notes: list) -> str:
    return " / ".join(n.text for n in notes if n.warn)


def test_high_ban_is_reported_with_its_rank(make_row: PanelRowFactory) -> None:
    """**절대값보다 패치 안 순위가 읽힌다.** 「밴율 71%」보다 「173종 중 1위」다."""
    target = make_row("16_13", 1, ban_rate=0.71)
    others = [make_row("16_13", i, ban_rate=0.01) for i in range(2, 6)]

    out = texts(reasons(target, [target, *others]))

    assert "71.0%" in out
    assert "5종 중 1위" in out


def test_a_flat_win_rate_says_so(make_row: PanelRowFactory) -> None:
    """5할에 붙어 있으면 **어느 쪽으로도 안 기운다**는 것이 정보다."""
    row = make_row("16_13", 1, win_rate=0.501)

    assert "안 기운다" in texts(reasons(row, [row]))


def test_a_moving_win_rate_is_reported(make_row: PanelRowFactory) -> None:
    """**추세는 수준과 다른 정보다.** 5할 위인데 내려오는 중이면 판단이 달라진다."""
    up = make_row("16_13", 1, d_win_rate=0.02)
    down = make_row("16_13", 2, d_win_rate=-0.02)
    flat = make_row("16_13", 3, d_win_rate=0.001)

    assert "오르는 중" in texts(reasons(up, [up]))
    assert "내리는 중" in texts(reasons(down, [down]))
    assert "중이다" not in texts(reasons(flat, [flat]))


def test_fired_rules_are_named(make_row: PanelRowFactory) -> None:
    """어떤 규칙이 걸렸는지가 근거다. **규칙 이름을 그대로 보인다.**"""
    row = make_row("16_13", 1, ban_rate=0.30)
    rule = Rule(
        id="A-ban",
        when=(Condition("ban_rate", ">=", 0.2),),
        then="adjusted",
        proposed_by="conversation",
        rationale="밴이 몰리면 건드린다",
    )

    assert "A-ban" in texts(reasons(row, [row], rules=(rule,)))


def test_neighbour_split_is_counted(make_row: PanelRowFactory) -> None:
    """이웃이 어느 쪽으로 갈렸는지. **`B5` 가 투표하는 것과 같은 재료다.**"""
    row = make_row("16_13", 1)
    cases = (
        Case(make_row("14_1", 2, direction_next="nerf"), 0.1),
        Case(make_row("14_2", 3, direction_next="nerf"), 0.2),
        Case(make_row("14_3", 4, direction_next="buff"), 0.3),
        Case(make_row("14_4", 5, direction_next=None), 0.4),
    )

    out = texts(reasons(row, [row], cases=cases))

    assert "3종 중 너프 2 · 버프 1" in out


# --- 경고 -------------------------------------------------------------


def test_a_pro_regular_below_fifty_gets_a_buff_warning(
    make_row: PanelRowFactory,
) -> None:
    """**이것이 이 도구의 값이다.**

    승률이 낮아 버프 후보로 올라오는데, 올려 주면 대회 출전이 크게 뛴다.
    프로 단골 버프 시 +37% · 그 외 ±0% 라는 측정이 근거다.
    """
    row = make_row("16_13", 1, win_rate=0.47, matches=50_000)

    out = warns(warnings(row, lifetime_pro=PRO_REGULAR + 0.2, considering="buff"))

    assert "버프 주의" in out


def test_a_pro_regular_above_fifty_gets_a_nerf_warning(
    make_row: PanelRowFactory,
) -> None:
    row = make_row("16_13", 1, win_rate=0.52, matches=50_000)

    out = warns(warnings(row, lifetime_pro=PRO_REGULAR + 0.2, considering="nerf"))

    assert "너프하면" in out


def test_the_below_fifty_warning_never_shows_on_buff_candidates(
    make_row: PanelRowFactory,
) -> None:
    """**항상 뜨는 경고는 경고가 아니다.**

    버프 후보는 정의상 승률이 5할 아래다. 거기 「5할 아래다」를 띄우면 목록
    전체에 붙어 눈에서 사라진다 — 실제로 한 번 그렇게 만들었다.
    """
    row = make_row("16_13", 1, win_rate=0.45, matches=50_000)

    on_buff = warns(warnings(row, considering="buff"))
    on_nerf = warns(warnings(row, considering="nerf"))

    assert "5할 아래" not in on_buff
    assert "5할 아래" in on_nerf


def test_a_thin_sample_is_flagged_either_way(make_row: PanelRowFactory) -> None:
    """판수 경고는 무엇을 검토하든 뜬다 — **값 자체를 못 믿는다**는 뜻이라서다."""
    row = make_row("16_13", 1, win_rate=0.53, matches=800)

    for considering in ("nerf", "buff", None):
        assert "표본이 얇아" in warns(warnings(row, considering=considering))


def test_a_champion_with_no_pro_data_gets_no_pro_warning(
    make_row: PanelRowFactory,
) -> None:
    """**모르는 것을 경고로 만들지 않는다.** 프로 데이터가 없으면 조용히 넘어간다."""
    row = make_row("16_13", 1, win_rate=0.47, matches=50_000)

    assert "프로 단골" not in warns(warnings(row, lifetime_pro=None))
