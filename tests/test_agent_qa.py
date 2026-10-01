"""후속 질문의 답을 **코드가 대조한다** — extension 3절 3항.

    ⚠  도구 결과에 없는 숫자를 썼거나, 도구가 준 적 없는 노트 블록을 인용했다
    ⚪  대조할 수 없다 — 숫자도 인용도 없거나 형식이 맞지 않는다
    ✅  대조를 통과했다. 실제로 확인한 것만 말한다

모델을 부르지 않는다. 도구 출력과 답을 글로 주고 대조만 본다.
"""

from __future__ import annotations

from lol_balance.agent.judge import Step
from lol_balance.agent.qa import Check, blocks_in, cautions, check, numbers_in
from lol_balance.agent.schema import Answer, Cited

EFFECT = """Senna — 14_7 에 적용된 조정의 전후 (경계: 16_19 까지의 지표)
조정: 버프 (14_6 → 14_7)
        조정 전(14_6)  조정 후(14_7)  변화
승률    45.9%  49.4%  +3.6%p
픽률    11.7%   7.2%  -4.4%p
밴율    22.4%   8.5%  -13.9%p
판수    3,183  36,293
대조군 — 같은 패치에서 조정되지 않은 130종의 평균 승률 변화 +0.2%p
효과 — 승률 변화에서 대조군 변화를 뺀 값 +3.4%p"""

NOTES = """Senna 패치 노트 (경계: 16_19 노트까지)
  [14_7] Q - Piercing Darkness
      Heal increased to 40 from 30
  [14_6] Stats
      Base health reduced to 530 from 560"""

STEPS = [
    Step("effect_of", {"champion": "Senna", "patch": "14_7"}, EFFECT),
    Step("search_patch_notes", {"champion": "Senna"}, NOTES),
]


def ask(answer: Answer | None, question: str = "14_7 버프 뒤에 뭐가 바뀌었어?") -> str:
    return check(answer, STEPS, context="", questions=[question]).mark


# ── 숫자 ───────────────────────────────────────────────────────────────


def test_numbers_copied_from_the_tools_pass() -> None:
    """도구가 준 숫자를 그대로 쓰면 통과한다 — **무엇을 확인했는지 센다.**"""
    got = check(
        Answer(
            answer="승률이 45.9% 에서 49.4% 로 올랐고(+3.6%p) 효과는 +3.4%p 다. 조정 전 판수는 3,183판이다.",
            numbers=["45.9%", "49.4%", "+3.6%p", "+3.4%p", "3,183"],
        ),
        STEPS,
        context="",
        questions=["14_7 버프 뒤에 뭐가 바뀌었어?"],
    )
    assert got.mark == "✅" and got.problems == ()
    assert got.numbers == 5 and got.notes == 0
    assert "숫자 5개" in got.line()


def test_a_number_the_tools_never_gave_is_flagged() -> None:
    """**도구 결과에 없는 숫자** — 모델이 지어냈거나 스스로 계산한 것이다."""
    got = check(
        Answer(answer="효과는 +3.9%p 다.", numbers=["+3.9%p"]),
        STEPS,
        context="",
        questions=["?"],
    )
    assert got.mark == "⚠" and "+3.9%p" in got.problems[0]


def test_numbers_in_the_prose_are_checked_even_if_not_declared() -> None:
    """숫자 칸을 비워 두고 글에만 숫자를 써도 잡는다 — 칸만 보면 빠져나간다."""
    assert ask(Answer(answer="승률이 51.2% 로 올랐다.")) == "⚠"
    assert ask(Answer(answer="승률이 49.4% 로 올랐다.")) == "✅"


def test_a_flipped_sign_is_not_the_same_number() -> None:
    """부호가 뒤집히면 다른 숫자다 — 오른 것을 내렸다고 쓰면 방향이 반대다."""
    assert ask(Answer(answer="픽률 변화", numbers=["-4.4%p"])) == "✅"
    assert ask(Answer(answer="픽률 변화", numbers=["+4.4%p"])) == "⚠"
    assert (
        ask(Answer(answer="픽률 변화", numbers=["4.4%p"])) == "✅"
    )  # 부호를 안 적었다
    assert ask(Answer(answer="픽률 변화", numbers=["−4.4%p"])) == "✅"  # 유니코드 빼기


def test_percent_and_percent_point_are_different_units() -> None:
    """`%` 와 `%p` 는 다른 것이다 — 52% 가 50% 가 된 것은 −2%p 이지 −2% 가 아니다."""
    assert ask(Answer(answer="x", numbers=["3.6%p"])) == "✅"
    assert ask(Answer(answer="x", numbers=["3.6%"])) == "⚠"
    assert ask(Answer(answer="x", numbers=["49.4%p"])) == "⚠"


def test_part_of_a_number_is_not_that_number() -> None:
    """`49.4%` 가 있다고 `9.4%` 가 있는 것은 아니다."""
    assert ask(Answer(answer="x", numbers=["9.4%"])) == "⚠"
    assert ask(Answer(answer="x", numbers=["183"])) == "⚠"
    assert (
        ask(Answer(answer="x", numbers=["3183판"])) == "✅"
    )  # 쉼표 · 단위는 달라도 된다


def test_patch_names_and_small_counts_are_not_measurements() -> None:
    """패치 이름과 한두 자리 개수는 재지 않는다 — 측정값이 아니다.

    `14.7` 은 패치 `14_7` 을 점으로 쓴 것이고, 「두 가지」를 `2` 로 쓴 것까지 잡으면
    경고가 흔해져 아무도 안 본다. 단위가 붙었거나 소수거나 세 자리 이상이면 잰다.
    """
    assert [n.core for n in numbers_in("14_7 패치에서 R3 로 봤다. 16_19 까지.")] == []
    fine = Answer(answer="14.7 패치에서 2가지가 바뀌었다. 승률은 49.4% 다.")
    assert ask(fine) == "✅"
    assert ask(Answer(answer="12.5 만큼 올랐다.")) == "⚠"  # 패치가 아닌 소수


def test_numbers_from_the_question_and_the_context_are_allowed() -> None:
    """사람이 물은 숫자와 **코드가 만든 맥락**(기준 패치 지표 · 통계 모델 점수)은 쓸 수
    있다. 모델이 쓴 앞선 답은 출처가 아니다."""
    answer = Answer(answer="물으신 55% 는 넘지 않는다. 기준 패치 승률은 52.6% 다.")
    got = check(
        answer, STEPS, context="승률 52.6% (173종 중 1위)", questions=["55% 넘어?"]
    )
    assert got.mark == "✅"
    assert check(answer, STEPS, context="", questions=["넘어?"]).mark == "⚠"


# ── 노트 블록 ───────────────────────────────────────────────────────────


def test_blocks_are_read_from_the_note_tool_output() -> None:
    assert blocks_in(STEPS) == {
        ("14_7", "q-piercingdarkness"),
        ("14_6", "stats"),
    }


def test_citing_a_block_the_tool_returned_passes() -> None:
    got = check(
        Answer(
            answer="14_7 에 Q 의 회복량을 올렸다.",
            notes=[Cited(patch="14.7", section="Q - Piercing Darkness")],
        ),
        STEPS,
        context="",
        questions=["뭘 바꿨어?"],
    )
    assert got.mark == "✅" and got.notes == 1 and "노트 블록 1개" in got.line()


def test_citing_a_block_no_tool_returned_is_flagged() -> None:
    """**없는 노트 블록** — 지어낸 절이거나, 도구로 확인하지 않고 인용한 것이다."""
    got = check(
        Answer(
            answer="W 를 바꿨다.",
            notes=[Cited(patch="14_7", section="W - Last Embrace")],
        ),
        STEPS,
        context="",
        questions=["?"],
    )
    assert got.mark == "⚠" and "W - Last Embrace" in got.problems[0]
    wrong_patch = Answer(answer="x", notes=[Cited(patch="14_8", section="Stats")])
    assert ask(wrong_patch) == "⚠"


# ── 대조할 수 없다 ──────────────────────────────────────────────────────


def test_nothing_to_check_is_not_a_pass() -> None:
    """숫자도 인용도 없으면 **통과가 아니라 「대조할 수 없다」다** — 확인한 것이 없다."""
    got = check(
        Answer(answer="자료가 없어 알 수 없다."), STEPS, context="", questions=["?"]
    )
    assert got.mark == "⚪" and "대조할 것이 없다" in got.line()


def test_a_missing_structured_answer_cannot_be_checked() -> None:
    got = check(None, STEPS, context="", questions=["?"])
    assert got.mark == "⚪" and "형식" in got.line()


def test_a_number_field_without_a_number_cannot_be_checked() -> None:
    """숫자 칸에 숫자가 아닌 것을 적었다 — 형식이 맞지 않는다."""
    got = check(
        Answer(answer="올랐다.", numbers=["많이 올랐다"]),
        STEPS,
        context="",
        questions=["?"],
    )
    assert got.mark == "⚪"


# ── 도구가 낸 주의 — 답이 옮겨야 한다 ────────────────────────────────────

THIN = EFFECT + (
    "\n⚠ 표본이 얇다 — 14_6 3,183판(기준 20,000판). 승률이 요동칠 수 있어 이 변화를"
    " 조정 효과라고 단정하지 않는다"
)
BEYOND = "Senna 패치 노트 (경계: 16_13 노트까지)\n  16_14: 찾지 못함 (경계 밖)"


def carried(answer: str, output: str, tool: str = "effect_of") -> Check:
    return check(
        Answer(answer=answer),
        [Step(tool, {}, output)],
        context="",
        questions=["?"],
        asked=[Step(tool, {}, output)],
    )


def test_cautions_are_read_from_what_the_tools_said() -> None:
    """도구가 붙인 주의를 **그 문장 그대로** 꺼낸다 — 화면이 답 아래에 붙인다. 모델이
    빼먹어도 사람은 본다."""
    found = cautions(
        [Step("effect_of", {}, THIN), Step("search_patch_notes", {}, BEYOND)]
    )
    assert [c.kind for c in found] == ["표본", "경계"]
    assert found[0].text.startswith("표본이 얇다 — 14_6 3,183판")
    assert "16_14" in found[1].text
    assert cautions([Step("effect_of", {}, EFFECT)]) == []


def test_a_thin_sample_must_be_said_in_the_answer() -> None:
    """**표본이 작으면 답에 그 사실을 적는다**(extension 3절 7항). 도구가 얇다고 했는데
    답이 그 말을 빼면 ⚠ 다 — 3,183판으로 잰 변화를 효과처럼 읽게 된다."""
    kept = carried("효과는 +3.4%p 다. 다만 표본이 얇아 단정하기 어렵다.", THIN)
    assert kept.mark == "✅"
    dropped = carried("효과는 +3.4%p 다.", THIN)
    assert dropped.mark == "⚠" and "표본이 얇다" in dropped.line()
    assert carried("효과는 +3.4%p 다.", EFFECT).mark == "✅"  # 도구가 주의를 안 냈다


def test_beyond_the_boundary_is_not_nothing_happened() -> None:
    """**「경계 밖」은 「없었다」가 아니다.** 로컬 모델이 기준 패치 뒤의 노트를 못 본
    것을 「조정되지 않았습니다」로 바꿔 답했다(2026-10-01) — 실제로는 너프됐다."""
    wrong = carried("16_14 에는 조정되지 않았습니다.", BEYOND, "search_patch_notes")
    assert wrong.mark == "⚠" and "볼 수 없다" in wrong.line()
    right = carried(
        "16_14 는 기준 패치 뒤라 볼 수 없습니다.", BEYOND, "search_patch_notes"
    )
    assert right.mark == "⚪"  # 주의는 옮겼고, 대조할 숫자나 인용은 없다


def test_only_this_questions_tools_set_what_must_be_said() -> None:
    """앞 질문에서 나온 주의를 뒤 질문의 답에 요구하지 않는다. 숫자의 출처는 쌓이지만
    주의는 **이번에 부른 도구**의 것만 본다."""
    earlier = [Step("effect_of", {}, THIN)]
    got = check(
        Answer(answer="승률은 49.4% 였다."),
        earlier,
        context="",
        questions=["아까", "그래서 승률은?"],
        asked=[],
    )
    assert got.mark == "✅"


def test_a_plain_text_answer_is_still_scanned() -> None:
    """구조화된 답이 없어도 **글은 훑는다.** 도구 결과에 없는 숫자나 빠뜨린 주의가 있으면
    ⚪ 가 아니라 ⚠ 다 — 로컬 모델이 칸을 안 채우고 글로만 「조정되지 않았으며」라고
    답했는데 ⚪ 로 지나갔다(2026-10-01). 문제를 못 찾았을 때만 「대조할 수 없다」다."""
    beyond = [Step("search_patch_notes", {}, BEYOND)]
    wrong = check(
        None,
        beyond,
        context="",
        questions=["?"],
        asked=beyond,
        text="16_14 에는 조정되지 않았습니다.",
    )
    assert wrong.mark == "⚠" and "볼 수 없다" in wrong.line()

    made_up = check(
        None, STEPS, context="", questions=["?"], text="승률이 87.6% 로 올랐다."
    )
    assert made_up.mark == "⚠" and "87.6%" in made_up.line()

    fine = check(
        None, STEPS, context="", questions=["?"], text="승률이 49.4% 로 올랐다."
    )
    assert fine.mark == "⚪" and "형식" in fine.line()


def test_a_thin_sample_only_matters_when_numbers_are_used() -> None:
    """표본 · 출처 주의는 **숫자를 쓴 답**에만 요구한다. 숫자 없이 「볼 수 없다」고 답한
    데까지 요구하면 경고가 흔해져 아무도 안 본다 — 로컬 모델이 쓸데없이 부른 지표 도구의
    주의 때문에 멀쩡한 답에 ⚠ 가 붙었다(2026-10-01). 화면은 어느 쪽이든 그 주의를 답
    아래에 붙인다. **「경계 밖」은 숫자와 상관없이 요구한다** — 없었다고 말하는 것을 막는다."""
    assert carried("그 패치는 자료가 없습니다.", THIN).mark == "⚪"
    assert carried("효과는 +3.4%p 다.", THIN).mark == "⚠"
    assert carried("조정되지 않았습니다.", BEYOND, "search_patch_notes").mark == "⚠"
