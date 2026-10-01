"""후속 질문의 답을 **코드가 대조한다** — extension 3절 3항.

    ⚠  도구 결과에 없는 숫자를 썼거나, 도구가 낸 주의를 뺐거나, 볼 수 없는 것을 없었다고
       말했다
    ⚪  대조할 것이 없다 — 답에 숫자가 없다
    ✅  대조를 통과했다. 실제로 확인한 것만 말한다

노트 블록은 코드가 붙인다(ADR 0017). 모델이 인용을 적던 때의 기록은 그때의 규칙으로 본다.

모델을 부르지 않는다. 도구 출력과 답을 글로 주고 대조만 본다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from lol_balance.agent.judge import Step
from lol_balance.agent.qa import (
    Check,
    blocks_in,
    cautions,
    check,
    note_blocks,
    numbers_in,
)
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


def ask(
    answer: Answer | None,
    question: str = "14_7 버프 뒤에 뭐가 바뀌었어?",
    cited: Sequence[Cited] = (),
) -> str:
    return check(answer, STEPS, context="", questions=[question], cited=cited).mark


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
    got = check(fine, STEPS, context="", questions=["?"])
    # 패치 이름은 확인한 숫자로 세지 않는다
    assert got.mark == "✅" and got.numbers == 1
    assert ask(Answer(answer="14.7 패치에서 바뀌었다.")) == "⚪"
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


# ── 노트 블록 — 코드가 붙인다 (ADR 0017) ─────────────────────────────────


def test_the_model_no_longer_writes_citations() -> None:
    """**답 형식에 노트 블록 칸이 없다**(ADR 0017). 모델은 답과 숫자만 적는다.

    모델이 적는 인용은 고정 질문을 다섯 번 받아 한 번도 0 이 아니었다(1 · 3 · 2 · 5 · 2) —
    예시를 베끼고, `Q - ` 를 붙이고, `[패치]` 같은 빈말을 적었다. 어느 블록을 도구가
    돌려줬는지는 코드가 이미 안다."""
    form = Answer.model_json_schema()
    assert set(form["properties"]) == {"answer", "numbers"}
    assert "Cited" not in json.dumps(form)
    # 옛 대화에 남은 답(인용 칸이 있다)도 그대로 읽힌다 — 그 칸은 버린다
    old = Answer.model_validate(
        {"answer": "x", "numbers": [], "notes": [{"patch": "14_7", "section": "Q"}]}
    )
    assert old.answer == "x" and not hasattr(old, "notes")


def test_note_blocks_are_what_the_notes_tool_returned() -> None:
    """**근거로 본 노트 블록은 코드가 도구 출력에서 꺼낸다** — 도구가 준 그대로, 준 순서로.
    다른 도구의 줄이나 「찾지 못함」은 블록이 아니다. 같은 블록은 한 번만."""
    assert note_blocks(STEPS) == ["[14_7] Q - Piercing Darkness", "[14_6] Stats"]
    assert note_blocks([Step("effect_of", {}, EFFECT)]) == []
    assert note_blocks([Step("search_patch_notes", {}, BEYOND)]) == []
    twice = [
        Step("search_patch_notes", {}, NOTES),
        Step("search_patch_notes", {}, NOTES),
    ]
    assert note_blocks(twice) == ["[14_7] Q - Piercing Darkness", "[14_6] Stats"]
    # 블록 본문에 대괄호로 시작하는 줄이 있어도 노트 도구의 머리 줄만 꺼낸다
    fake = [Step("lookup_stats", {}, "  [14_7] 이것은 노트가 아니다")]
    assert note_blocks(fake) == []


def test_an_answer_about_notes_without_numbers_is_not_a_pass() -> None:
    """노트 질문에 숫자 없이 답하면 **코드가 확인한 것이 없다 — ⚪ 다.** 전에는 모델이 적은
    인용이 맞으면 ✅ 였다. 이제 블록은 코드가 붙이므로 맞고 틀리고가 없다. 확인한 것이
    없으면 통과라고 하지 않는다(ADR 0017 이 치르는 값이다)."""
    got = check(
        Answer(answer="14_7 에 Q 의 회복량을 올렸다."),
        STEPS,
        context="",
        questions=["뭘 바꿨어?"],
    )
    assert got.mark == "⚪" and "답에 숫자가 없다" in got.line()
    with_numbers = Answer(
        answer="Q 의 회복량을 30 에서 40 으로 올렸다.", numbers=["30", "40"]
    )
    assert ask(with_numbers) == "✅"


# ── 옛 형식 — 모델이 인용을 적던 때의 기록을 다시 채점한다 ────────────────
#
# ADR 0017 전에는 모델이 `notes` 칸에 인용을 적었다. 그때 받아 커밋한 답 다섯 벌
# (`ground_truth/qa/`)은 **그때의 규칙으로 다시 채점할 수 있어야 한다** — 문서에 적은
# 수치가 계속 재현돼야 한다. 그 인용을 `cited=` 로 넘긴다.


def test_blocks_are_read_from_the_note_tool_output() -> None:
    assert blocks_in(STEPS) == {
        ("14_7", "q-piercingdarkness"),
        ("14_6", "stats"),
    }


def test_citing_a_block_the_tool_returned_passes() -> None:
    got = check(
        Answer(answer="14_7 에 Q 의 회복량을 올렸다."),
        STEPS,
        context="",
        questions=["뭘 바꿨어?"],
        cited=[Cited(patch="14.7", section="Q - Piercing Darkness")],
    )
    assert got.mark == "✅" and got.notes == 1 and "노트 블록 1개" in got.line()


def test_brackets_around_the_cited_patch_do_not_matter() -> None:
    """도구는 `[14_7] 절` 로 준다. 모델이 패치를 **대괄호째** 옮겨 적어도 같은 블록이다 —
    칸 설명을 「대괄호 안에 준 그대로」로 고쳐 봤더니 인용 3건이 전부 `[14_9]` 꼴로 와서
    있는 블록이 「없는 블록」으로 걸렸다(2026-10-01). 표기는 봐주고 내용은 그대로 본다."""
    got = check(
        Answer(answer="Q 의 회복량을 올렸다."),
        STEPS,
        context="",
        questions=["?"],
        cited=[Cited(patch="[14_7]", section="Q - Piercing Darkness")],
    )
    assert got.mark == "✅" and got.notes == 1
    # 14_7 에는 Stats 절이 없다 — 14_6 에 있다
    assert (
        ask(Answer(answer="x"), cited=[Cited(patch="[14_7]", section="Stats")]) == "⚠"
    )


def test_citing_a_block_no_tool_returned_is_flagged() -> None:
    """**없는 노트 블록** — 지어낸 절이거나, 도구로 확인하지 않고 인용한 것이다."""
    got = check(
        Answer(answer="W 를 바꿨다."),
        STEPS,
        context="",
        questions=["?"],
        cited=[Cited(patch="14_7", section="W - Last Embrace")],
    )
    assert got.mark == "⚠" and "W - Last Embrace" in got.problems[0]
    assert ask(Answer(answer="x"), cited=[Cited(patch="14_8", section="Stats")]) == "⚠"


def test_a_tool_line_in_the_citation_field_is_not_a_made_up_block() -> None:
    """**도구가 준 줄을 인용 칸에 적은 것은 지어낸 인용이 아니다.** 로컬 모델이
    `effect_of` 의 머리 줄을 노트 블록 칸에 적었다(고정 질문 q01) — 답의 숫자는 전부
    출처에 있는데 ⚠ 가 붙었다. 가리킨 줄은 도구가 실제로 준 것이라 **걸지 않는다.** 노트
    블록으로 세지도 않는다. **어느 도구도 준 적 없는 것만 ⚠ 다.**"""
    header = "Senna — 14_7 에 적용된 조정의 전후 (경계: 16_19 까지의 지표)"
    got = check(
        Answer(answer="효과는 +3.4%p 다.", numbers=["+3.4%p"]),
        STEPS,
        context="",
        questions=["?"],
        cited=[Cited(patch="14_7", section=header)],
    )
    assert got.mark == "✅" and got.numbers == 1 and got.notes == 0
    assert got.absent == () and got.misfiled == (f"[14_7] {header}",)
    assert "노트 블록" not in got.line()  # 확인한 것만 말한다 — 노트는 확인한 것이 없다

    # 「패치: 이유」 줄을 패치와 절로 쪼개 적은 것도 같다(고정 질문 q27)
    refusal = [Step("effect_of", {}, "16_14: 경계 밖 — 16_13 뒤의 자료는 보지 않는다")]
    split = check(
        Answer(answer="16_14 는 기준 패치 뒤라 볼 수 없습니다."),
        refusal,
        context="",
        questions=["?"],
        asked=refusal,
        cited=[Cited(patch="16_14", section="경계 밖 — 16_13 뒤의 자료는 보지 않는다")],
    )
    assert split.mark == "⚪" and "대조할 것이 없다" in split.line()
    assert split.misfiled and split.absent == ()

    # 진짜 블록과 같이 적으면 진짜 블록만 센다
    both = check(
        Answer(answer="Q 의 회복량을 올렸다."),
        STEPS,
        context="",
        questions=["?"],
        cited=[
            Cited(patch="14_7", section="Q - Piercing Darkness"),
            Cited(patch="14_7", section=header),
        ],
    )
    assert both.mark == "✅" and both.notes == 1 and "노트 블록 1개" in both.line()

    # 있는 절 이름을 다른 패치에 붙인 것 · 도구가 준 줄이 아닌 것은 여전히 지어낸 인용이다
    assert ask(Answer(answer="x"), cited=[Cited(patch="14_8", section="Stats")]) == "⚠"
    assert ask(Answer(answer="x"), cited=[Cited(patch="14_7", section="조정")]) == "⚠"


# ── 대조할 것이 없다 ────────────────────────────────────────────────────


def test_nothing_to_check_is_not_a_pass() -> None:
    """숫자가 없으면 **통과가 아니라 「대조할 것이 없다」다** — 확인한 것이 없다."""
    got = check(
        Answer(answer="자료가 없어 알 수 없다."), STEPS, context="", questions=["?"]
    )
    assert got.mark == "⚪" and "대조할 것이 없다" in got.line()


def test_an_empty_answer_has_nothing_to_check() -> None:
    got = check(None, STEPS, context="", questions=["?"])
    assert got.mark == "⚪" and "숫자가 없다" in got.line()


def test_what_was_verified_decides_the_mark_not_the_form() -> None:
    """**표시는 확인한 것을 따른다 — 칸을 어떻게 채웠는지를 따르지 않는다.** 숫자 칸에 숫자가
    아닌 것(패치 이름 · 스킬 이름)을 적어도 그것만으로 표시가 바뀌지 않는다. 인용 칸을 빼자
    로컬 모델이 그 자리에 `15_4` · `Leverage` 를 적었다 — 확인한 숫자가 없으면 ⚪ 고, 있으면
    ✅ 다."""
    only_words = check(
        Answer(answer="올랐다.", numbers=["많이 올랐다"]),
        STEPS,
        context="",
        questions=["?"],
    )
    assert only_words.mark == "⚪" and "숫자가 없다" in only_words.line()
    mixed = check(
        Answer(
            answer="승률은 49.4% 다.", numbers=["49.4%", "14_7", "Piercing Darkness"]
        ),
        STEPS,
        context="",
        questions=["?"],
    )
    assert mixed.mark == "✅" and mixed.numbers == 1
    made_up = Answer(answer="승률은 87.6% 다.", numbers=["87.6%", "Leverage"])
    assert ask(made_up) == "⚠"


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
    ⚠ 다 — 로컬 모델이 칸을 안 채우고 글로만 「조정되지 않았으며」라고 답했는데 ⚪ 로
    지나갔다(2026-10-01)."""
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


def test_a_plain_text_answer_with_verified_numbers_passes() -> None:
    """**글로만 답해도 숫자를 확인했으면 ✅ 다**(주인 승인 2026-10-01 · ADR 0017 덧붙임).

    한때는 칸에 넣지 않은 답을 「형식이 맞지 않는다」며 ⚪ 로 뒀다. 그런데 코드는 글의 숫자를
    이미 맞춰 본다 — 확인하고도 「대조할 수 없다」고 적는 셈이었다. 인용 칸을 빼자 로컬
    모델이 글로만 답한 것이 27건 중 6 → 16건으로 늘었고(Ollama 는 `tool_choice` 를 무시해
    칸을 강제할 수 없다), 숫자가 전부 맞는데 ✅ 가 15 → 5개로 줄었다. 표시는 **확인한
    것**을 따른다 — 칸에 넣었는지를 따르지 않는다."""
    fine = check(
        None, STEPS, context="", questions=["?"], text="승률이 49.4% 로 올랐다."
    )
    assert fine.mark == "✅" and fine.numbers == 1
    assert "숫자 1개가 도구 결과에 있다" in fine.line()
    # 숫자가 없으면 확인한 것이 없다 — 칸에 넣었을 때와 같다
    bare = check(None, STEPS, context="", questions=["?"], text="자료가 없습니다.")
    assert bare.mark == "⚪" and "숫자가 없다" in bare.line()
    # 글로 답해도 주의를 빼면 ⚠ 다
    thin = [Step("effect_of", {}, THIN)]
    dropped = check(
        None, thin, context="", questions=["?"], asked=thin, text="효과는 +3.4%p 다."
    )
    assert dropped.mark == "⚠" and "표본이 얇다" in dropped.line()


def test_a_thin_sample_only_matters_when_numbers_are_used() -> None:
    """표본 · 출처 주의는 **숫자를 쓴 답**에만 요구한다. 숫자 없이 「볼 수 없다」고 답한
    데까지 요구하면 경고가 흔해져 아무도 안 본다 — 로컬 모델이 쓸데없이 부른 지표 도구의
    주의 때문에 멀쩡한 답에 ⚠ 가 붙었다(2026-10-01). 화면은 어느 쪽이든 그 주의를 답
    아래에 붙인다. **「경계 밖」은 숫자와 상관없이 요구한다** — 없었다고 말하는 것을 막는다."""
    assert carried("그 패치는 자료가 없습니다.", THIN).mark == "⚪"
    assert carried("효과는 +3.4%p 다.", THIN).mark == "⚠"
    assert carried("조정되지 않았습니다.", BEYOND, "search_patch_notes").mark == "⚠"


# ── 고정 질문 세트에서 찾은 것 (2026-10-01) ─────────────────────────────


def test_the_balance_line_is_not_a_measurement() -> None:
    """**「50%」는 균형선이다** — 측정값이 아니라 기준이라 출처를 묻지 않는다. 고정 질문
    27개에서 「도구 결과에 없는 숫자」로 걸린 2건이 둘 다 「50% 선 근처」였다."""
    assert ask(Answer(answer="승률은 50% 선 근처를 유지했다.")) == "⚪"
    assert ask(Answer(answer="승률은 49.4% 로 50% 에 가까워졌다.")) == "✅"
    assert ask(Answer(answer="승률은 50.3% 였다.")) == "⚠"  # 균형선이 아니라 지어낸 값


def test_what_cannot_be_seen_must_not_be_denied() -> None:
    """「볼 수 없다」고 쓰면서 **「조정이 이루어지지 않았다」고 단정한 답**이 통과했다
    (고정 질문 q26). 볼 수 없다는 말이 있어도, 없었다고 말했으면 ⚠ 다."""
    both = carried(
        "16_14 에는 조정이 이루어지지 않았습니다. 16_13 이후 자료를 볼 수 없기 때문입니다.",
        BEYOND,
        "search_patch_notes",
    )
    assert both.mark == "⚠" and "없었다" in both.line()
    assert both.denied == ("조정이 이루어지지 않았",) and both.dropped == ()
    # 패치 이름 없이 말해도 지금 묻는 패치(경계 밖)를 두고 한 말이다
    bare = carried(
        "조정은 없었습니다. 볼 수 없기 때문입니다.", BEYOND, "search_patch_notes"
    )
    assert bare.mark == "⚠" and bare.denied


def test_a_denial_about_a_patch_in_range_is_left_alone() -> None:
    """그 말 **앞에서 가장 가까운 패치 이름**이 가리키는 패치를 본다 — 볼 수 있는 패치를
    두고 한 말은 잡지 않는다. 「기록이 없다」도 잡지 않는다(자료에 없다는 말이다)."""
    mixed = carried(
        "16_13 에는 조정되지 않았고, 16_14 는 기준 패치 뒤라 볼 수 없습니다.",
        BEYOND,
        "search_patch_notes",
    )
    assert mixed.mark == "⚪" and mixed.denied == ()
    dotted = carried(
        "16.14 는 볼 수 없습니다. 16.13 에는 너프를 받지 않았습니다.",
        BEYOND,
        "search_patch_notes",
    )
    assert dotted.mark == "⚪"
    record = carried(
        "16_14 의 조정은 기록되지 않았습니다. 기준 패치 뒤라 볼 수 없기 때문입니다.",
        BEYOND,
        "search_patch_notes",
    )
    assert record.mark == "⚪"
    # 단정이 아닌 말은 잡지 않는다 — 「…았는지는 볼 수 없다」 · 「…았다는 뜻이 아니다」
    for unsure in (
        "16_14 에 조정되지 않았는지는 기준 패치 뒤라 볼 수 없습니다.",
        "볼 수 없다는 것이지, 16_14 에 조정이 없었다는 뜻이 아닙니다.",
        "16_14 에 조정되지 않았다고 단정할 수 없습니다. 볼 수 없기 때문입니다.",
        "16_14 에 조정이 없다는 뜻이 아닙니다. 기준 패치 뒤라 볼 수 없습니다.",
        "16_14 에 버프가 없었다면 승률이 그대로였겠지만, 볼 수 없습니다.",
        "16_14 에 조정이 있었는지 없었는지는 볼 수 없습니다.",
    ):
        assert carried(unsure, BEYOND, "search_patch_notes").denied == (), unsure


def test_a_denial_in_the_present_tense_is_still_a_denial() -> None:
    """「없었다」뿐 아니라 **「없습니다 · 없으며」**도 같은 단정이다. 고정 질문에서 모델이
    「15_14 패치 노트에서도 변경 내용이 없으며 … 경계 밖으로 판단합니다」라고 답했다 —
    「경계 밖」이라는 낱말이 있어 주의를 옮긴 것으로 지나갔다. 도구는 「볼 수 없다」고 했지
    「변경이 없다」고 하지 않았다."""
    for wrong in (
        "16_14 패치 노트에서도 변경 내용이 없으며, 통계 모델은 경계 밖으로 판단합니다.",
        "16_14 에는 조정이 없습니다. 기준 패치 뒤라 볼 수 없기 때문입니다.",
        "16_14 에는 변경 사항이 없고, 그 뒤는 볼 수 없습니다.",
    ):
        got = carried(wrong, BEYOND, "search_patch_notes")
        assert got.mark == "⚠" and got.denied, wrong
    # 도구가 「경계 밖」이라고 하지 않았으면 「조정되지 않았다」는 말은 그대로 둔다
    inside = [
        Step("effect_of", {}, "Fizz 은 15_5 에 조정되지 않았다 — 조정 전후가 아니다")
    ]
    said = check(
        Answer(answer="Fizz 는 15_5 에 조정되지 않았습니다."),
        inside,
        context="",
        questions=["?"],
        asked=inside,
    )
    assert said.mark == "⚪"
