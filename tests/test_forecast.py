"""미리 적어 둔 예측의 채점 테스트.

**모델을 안 부른다.** 적어 둔 목록과 라벨만으로 성적이 나오는지, 그리고 두 기준
(전부 · 의도만)이 갈리는지를 본다.
"""

from __future__ import annotations

from datetime import datetime

from lol_balance.forecast import (
    OrderCheck,
    Registered,
    pool_growth,
    positives,
    score_side,
)


def registered(**over: object) -> Registered:
    field: dict[str, object] = {
        "name": "pre-test",
        "base": "16_20",
        "target": "16_21",
        "snapshot": "pre-test",
        "recorded": "2026-10-19",
        "targets": tuple(f"C{i}" for i in range(10)),
        "nerf": ("C0", "C1", "C2"),
        "buff": ("C3", "C4", "C5"),
    }
    field.update(over)
    return Registered(**field)  # type: ignore[arg-type]


def test_a_bug_fix_counts_for_the_target_but_not_for_the_intent() -> None:
    """**이것 때문에 두 지점을 나란히 못 읽었다.**

    `16_19` 는 버그 수정뿐인 Camille · Yone 을 적중으로 셌고 `16_20` 은 뺀 수를
    따로 적었다. 같은 함수가 둘 다 내게 한다.
    """
    got = score_side(
        registered(),
        "nerf",
        answer={"C0", "C1"},
        directions={"C0": "nerf", "C1": "adjust"},
        top=3,
    )

    assert got.hits == ("C0", "C1")
    assert got.intended == ("C0",)
    assert got.matched == ("C0",)


def test_a_hit_without_a_label_is_not_a_miss() -> None:
    """라벨이 없으면 **못 잰 것**이지 틀린 것이 아니다.

    `16_20` 에서 적중한 다섯 자리가 전부 이랬다 — cdragon 이 `15.18` 뒤로 스킬
    수치를 못 보고 있어서다.
    """
    got = score_side(registered(), "nerf", answer={"C2"}, directions={}, top=3)

    assert got.hits == ("C2",)
    assert got.matched == ()
    assert got.unlabelled == ("C2",)


def test_the_baseline_counts_only_what_was_in_the_pool() -> None:
    """**대상 밖은 안 센다.**

    `16_20` 6,000판에서 정답 여섯이 200판 문턱 아래였다. 그것을 기준선에 넣으면
    고를 수 없었던 챔피언 때문에 기준선만 낮아진다.
    """
    touched, intended = positives(
        registered(),
        answer={"C0", "C1", "밖에있는챔피언"},
        directions={"C0": "nerf", "C1": "adjust"},
    )

    assert touched == frozenset({"C0", "C1"})
    assert intended == frozenset({"C0"})

    got = score_side(
        registered(),
        "nerf",
        answer={"C0", "C1", "밖에있는챔피언"},
        directions={"C0": "nerf", "C1": "adjust"},
        top=3,
    )
    # 대상 10종 중 조정 2종 → 상위 3이면 0.6종. 의도만 세면 1종이라 0.3종이다.
    assert round(got.expected, 6) == 0.6
    assert round(got.expected_intended, 6) == 0.3


def test_the_wrong_direction_is_a_hit_for_the_target_only() -> None:
    """Lucian 이 그랬다 — 너프 후보로 올렸는데 실제는 `mixed` 다."""
    got = score_side(
        registered(), "nerf", answer={"C0"}, directions={"C0": "mixed"}, top=3
    )

    assert got.hits == ("C0",)
    assert got.intended == ("C0",)
    assert got.matched == ()


def test_top_cuts_the_list() -> None:
    got = score_side(
        registered(nerf=("C0", "C1", "C2", "C3")),
        "nerf",
        answer={"C3"},
        directions={"C3": "nerf"},
        top=3,
    )

    assert got.hits == ()


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def test_registering_after_the_answer_key_is_not_clean() -> None:
    """**실제로 두 번 놓쳤다.**

    `pre-16_21` 은 위키에 `V26.21` 이 생긴 지 3시간 뒤에, `pre-16_22-big` 은 30시간
    뒤에 굳혔다. 입력은 대상 패치 전에 끊겨 있었지만 그것은 「보지 않았다」의 근거일
    뿐이고, 전향 평가는 「볼 수 없었다」로 서야 한다.
    """
    late = OrderCheck(
        name="pre-16_22-big",
        target="16_23",
        registered=at("2026-11-18T00:42:42+00:00"),
        answer=at("2026-11-16T19:02:46+00:00"),
    )

    assert not late.clean
    assert round(late.late_hours or 0, 1) == 29.7


def test_registering_before_the_answer_key_is_clean() -> None:
    early = OrderCheck(
        name="pre-16_22",
        target="16_23",
        registered=at("2026-11-10T08:14:34+00:00"),
        answer=at("2026-11-16T19:02:46+00:00"),
    )

    assert early.clean
    assert (early.late_hours or 0) < 0


def test_no_answer_key_yet_is_not_a_flaw() -> None:
    """아직 패치가 안 나온 벌은 흠이 아니다 — 그것이 정상이다."""
    pending = OrderCheck(
        name="pre-16_23",
        target="16_24",
        registered=at("2026-11-28T00:00:00+00:00"),
        answer=None,
    )

    assert pending.clean
    assert pending.late_hours is None


def test_the_same_instant_counts_as_late() -> None:
    """**같은 시각은 앞선 것이 아니다.** 경계를 느슨하게 두면 흠이 통과한다."""
    tie = at("2026-11-16T19:02:46+00:00")

    assert not OrderCheck(name="x", target="16_23", registered=tie, answer=tie).clean


def test_the_target_pool_stops_growing_before_the_patch_ends() -> None:
    """**언제 굳혀야 하는가의 근거다.**

    이틀째에 문턱을 넘고, 그 뒤로 판수가 늘어도 종수는 안 늘어난다 — `16_22` 를
    실제로 세 보면 열하루째 158종에서 열나흘째 164종까지 여섯 종이 늘고 「조정된
    15종을 몇 담는가」는 그대로였다.
    """
    day = 86_400_000
    games = [(i, (1, 2)) for i in range(3)]  # 첫날 3판
    games += [(day + i, (1, 2)) for i in range(2)]  # 이틀째 2판
    games += [(2 * day + i, (3,)) for i in range(1)]  # 사흘째 1판

    got = pool_growth(games, min_matches=4)

    # 첫날 3판이면 1·2번이 3픽씩 — 아직 문턱 아래. 이틀째에 5픽으로 넘는다.
    assert got == ((1, 3, 0), (2, 5, 2), (3, 6, 2))


def test_pool_growth_on_nothing() -> None:
    assert pool_growth([]) == ()
