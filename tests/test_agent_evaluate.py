"""평가 하네스가 결과 문서와 **같은 것을 재는지** 확인한다. 키도 모델도 필요 없다.

**원자료 없이 돈다** — `tiny_corpus`(conftest)로 표본·경계·채점의 배선을 본다.
결과 문서의 실제 수치(229건 · `B5s` 0.850 · `B6` 0.830)와 대조하는 것은
`needs_data` 로 따로 두고, `data/` 가 없으면 건너뛴다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolCall
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult

import lol_balance.agent.evaluate as ev
import lol_balance.agent.judge as judge
from lol_balance.agent.data import Corpus, available, load
from lol_balance.agent.judge import (
    MAX_OUTPUT,
    build_context,
    chat_model,
    misquoted_warning,
    misread_baseline,
)
from lol_balance.agent.schema import Explanation
from lol_balance.panel import patch_index
from lol_balance.ragjudge import Judgment as Saved
from lol_balance.ragjudge import anon_key

needs_data = pytest.mark.skipif(
    not available(), reason="data/ 가 없다 — clone 직후에는 정상이다"
)


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """토큰 수는 공백으로 센다 — tiktoken 은 처음에 파일을 내려받는다. 망 없이 돈다."""
    words: Callable[[str], list[str]] = str.split
    monkeypatch.setattr(ev, "_encoder", lambda: words)
    monkeypatch.setattr(ev, "DRAWS", 300)


@pytest.fixture
def judged(tiny_corpus: Corpus, monkeypatch: pytest.MonkeyPatch) -> list[ev.Case]:
    """평가 표본 — 작은 패널의 평가 구간 행에 `B6` 판단이 있는 것처럼 둔다."""
    rows = [
        r
        for r in tiny_corpus.rows
        if r.patch in tiny_corpus.labeled
        and r.direction_next in ("nerf", "buff")
        and r.patch_index >= patch_index(ev.SPLIT)
    ]
    saved = {
        (anon_key(r), "anon"): Saved(
            anon_key(r), "anon", 70 if r.win_rate > 0.5 else 30, "r"
        )
        for r in rows
    }
    monkeypatch.setattr(ev, "read_judgments", lambda path: saved)
    return ev.cases(tiny_corpus)


def test_cases_are_the_rows_b6_judged(judged: list[ev.Case]) -> None:
    assert len(judged) >= 20
    assert all(c.split == ev.SPLIT for c in judged)
    assert {c.b6 for c in judged} <= {0.7, 0.3}
    assert all(c.truth in (0, 1) and 0 <= c.b5 <= 1 for c in judged)


@needs_data
def test_case_set_matches_the_documented_b5s_and_b6() -> None:
    """문서의 수치 — B6 229건 · B5s 0.850 · B6 0.830 (ADR 0007, 용어집)."""
    real = ev.cases(load())
    truth = [c.truth for c in real]
    assert len(real) == 229
    assert round(ev.score(truth, [c.b5 for c in real])["auc"], 3) == 0.850
    assert round(ev.score(truth, [c.b6 for c in real])["auc"], 3) == 0.830


def test_rule_agent_through_the_loop_reproduces_b5_row_by_row(
    tiny_corpus: Corpus, judged: list[ev.Case]
) -> None:
    """B5 를 LangChain 루프에 태우면 **행마다** B5 점수가 그대로 나와야 한다."""
    for case in judged[:12]:
        line = ev.run_case(tiny_corpus, case, "rule")
        assert line["error"] is None, line["error"]
        assert line["nerf_prob"] / 100 == pytest.approx(case.b5), case.key
        assert line["tools"] == ["find_similar_cases"]
        assert line["model_calls"] == 2 and line["tokens_in"] > 0


@needs_data
def test_rule_agent_reproduces_b5_on_the_real_panel() -> None:
    corpus = load()
    for case in ev.cases(corpus)[:25]:
        line = ev.run_case(corpus, case, "rule")
        assert line["nerf_prob"] / 100 == pytest.approx(case.b5), case.key


def test_variants_hand_out_different_tools(
    tiny_corpus: Corpus, judged: list[ev.Case]
) -> None:
    case = judged[0]

    def names(variant: str) -> set[str]:
        return {t.name for t in ev.variant_tools(tiny_corpus, case, variant)}

    assert names("v1") == {"lookup_stats", "find_similar_cases"}
    assert names("r1") == {"find_similar_cases"}
    assert names("r1r3") == {"lookup_stats", "find_similar_cases"}
    with pytest.raises(ValueError, match="변형"):
        ev.variant_tools(tiny_corpus, case, "r2")
    assert case.key in ev.task(case) and case.row.champion not in ev.task(case)


class _Fake(BaseChatModel):
    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> _Fake:
        return self


class Looper(_Fake):
    """도구만 끝없이 부른다. 끝내지 않는 모델."""

    @property
    def _llm_type(self) -> str:
        return "looper"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        n = sum(1 for m in messages if isinstance(m, AIMessage))
        call: ToolCall = {
            "name": "find_similar_cases",
            "args": {"champion": "대상"},
            "id": f"c{n}",
            "type": "tool_call",
        }
        message = AIMessage("", tool_calls=[call])
        return ChatResult(generations=[ChatGeneration(message=message)])


def test_call_limit_stops_a_runaway_agent(
    tiny_corpus: Corpus, judged: list[ev.Case], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**한 건이 루프에 빠져도 거기서 멈추고 기권으로 센다** — 비용이 새지 않는다."""
    monkeypatch.setattr(ev, "chat_model", lambda *a, **k: Looper())
    line = ev.run_case(tiny_corpus, judged[0], "looper:fake")
    assert line["abstain"] is True and line["nerf_prob"] is None
    assert line["model_calls"] <= 8
    assert len(line["tools"]) <= 12


def test_every_model_gets_an_output_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """한 번의 응답이 끝없이 길어지지 않게 — 제공자마다 인자 이름이 다르다."""
    assert getattr(chat_model("ollama:qwen3.5:2b"), "num_predict", None) == MAX_OUTPUT

    # 다른 제공자는 통합 패키지를 안 깐다. 만드는 자리에 넘기는 인자만 본다.
    seen: dict[str, Any] = {}
    monkeypatch.setattr(judge, "init_chat_model", lambda model, **kw: seen.update(kw))
    chat_model("openai:gpt-4o-mini")
    assert seen["max_tokens"] == MAX_OUTPUT and "num_predict" not in seen


# ── 개발 표본 · 50 금지 ────────────────────────────────────────────────


def test_dev_set_never_touches_the_evaluation_period(
    tiny_corpus: Corpus, judged: list[ev.Case]
) -> None:
    """개발 표본은 전부 평가 분할점 앞이다. 여기서 골라도 평가 표본이 오염되지 않는다."""
    dev = ev.dev_cases(tiny_corpus, n=None)
    assert dev and all(c.row.patch_index < patch_index(ev.SPLIT) for c in dev)
    assert not {c.key for c in dev} & {c.key for c in judged}
    assert all(c.split == ev.DEV_SPLIT for c in dev)
    assert len(ev.dev_cases(tiny_corpus, n=10)) == 10  # 뽑아도 같은 규칙이다

    for case in dev[:6]:
        line = ev.run_case(tiny_corpus, case, "rule")
        assert line["nerf_prob"] / 100 == pytest.approx(case.b5), case.key
        assert line["as_of"] == ev.DEV_SPLIT


class FiftyThenSeventy(_Fake):
    """처음엔 50 을 적고, 되돌려 받으면 70 으로 고친다."""

    @property
    def _llm_type(self) -> str:
        return "fifty-then-seventy"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        tries = sum(1 for m in messages if isinstance(m, AIMessage))
        call: ToolCall = {
            "name": "Judgment",
            "args": {
                "adjust_prob": 100,
                "nerf_prob": 50 if tries == 0 else 70,
                "reason": "r",
                "evidence": [{"source": "R1", "text": "부르지 않았다"}],
            },
            "id": f"j{tries}",
            "type": "tool_call",
        }
        message = AIMessage("", tool_calls=[call])
        return ChatResult(generations=[ChatGeneration(message=message)])


def test_strict_judgment_sends_fifty_back_for_a_rewrite(
    tiny_corpus: Corpus, judged: list[ev.Case], monkeypatch: pytest.MonkeyPatch
) -> None:
    """50 을 적으면 LangChain 이 검증 오류를 돌려주고, 모델이 다시 쓴다."""
    monkeypatch.setattr(ev, "chat_model", lambda *a, **k: FiftyThenSeventy())
    line = ev.run_case(tiny_corpus, judged[0], "fake:x", strict=True)
    assert line["nerf_prob"] == 70 and line["abstain"] is False
    assert line["model_calls"] == 2
    assert line["unverified_sources"] == ["R1"]  # 도구를 안 부르고 R1 을 적었다

    loose = ev.run_case(tiny_corpus, judged[0], "fake:x", strict=False)
    assert loose["nerf_prob"] == 50  # 제약이 없으면 그대로 받는다


@needs_data
def test_explanation_checks_catch_the_azir_mistakes() -> None:
    """로컬 2B 가 실제로 낸 두 오류 — 실제 패널의 Azir 16_15 로 다시 본다."""
    ctx = build_context(load(), "Azir", "16_15")  # 버프 0.74 > 너프 0.19, 「버프 주의」
    wrong = Explanation(
        summary="s",
        stance="반대",
        baseline_side="너프",
        tension="버프 후보인데 너프 주의",
        tension_quote="너프 주의",
    )
    assert misread_baseline(wrong, ctx) == "버프"
    assert misquoted_warning(wrong, ctx) is True


# ── 채점만 — 저장한 기록 ─────────────────────────────────────────────────


def _line(case: ev.Case, prob: int | None, **extra: Any) -> dict[str, Any]:
    return {
        "key": case.key,
        "condition": "anon",
        "variant": "r1",
        "strict": True,
        "model": "ollama:qwen3.5:9b",
        "as_of": case.split,
        "nerf_prob": prob,
        "abstain": prob is None,
        "unverified_sources": [],
        "model_calls": 2,
        "tokens_in": 2800,
        "tokens_out": 220,
        "seconds": 19.0,
        **extra,
    }


def row_of(text: str, arm: str) -> list[str]:
    return next(r for r in text.splitlines() if r.startswith(arm)).split()


def test_report_scores_saved_records_without_a_model(
    tiny_corpus: Corpus, judged: list[ev.Case]
) -> None:
    """**모델 없이 채점된다.** 규칙 기록이면 `B5s` 가 그대로 나온다."""
    rule = [ev.run_case(tiny_corpus, c, "rule") for c in judged]
    text = "\n".join(ev.report(judged, rule))
    assert "(B5)" in text and "B8 " not in text  # 규칙은 LLM 이 아니다
    assert row_of(text, "(B5)")[-2:] == row_of(text, "B5s")[-2:]

    # LLM 기록 — 기권은 빼고, 같은 부분집합의 B5s·B6 과 짝지어 잰다
    lines = [_line(c, round(100 * (1 - c.b5))) for c in judged[:-2]]
    lines += [_line(c, None) for c in judged[-2:]]
    text = "\n".join(ev.report(judged, lines))
    assert f"커버리지 {len(judged) - 2}/{len(judged)}" in text
    assert "B8 − B5s" in text and "B8 − B6" in text
    assert "① 대상 3,433행" in text


def test_report_on_the_dev_set_has_no_b6(tiny_corpus: Corpus) -> None:
    dev = ev.dev_cases(tiny_corpus, n=None)
    lines = [_line(c, round(100 * c.b5)) for c in dev]
    text = "\n".join(ev.report(dev, lines))
    assert "개발 표본" in text and "B8 − B5s" in text and "B8 − B6" not in text


def test_old_records_read_with_the_settings_of_their_time(
    judged: list[ev.Case],
) -> None:
    """`variant`·`strict` 가 생기기 전 기록 — 고치지 않고 그때의 값으로 읽는다."""
    old = [_line(c, 60) for c in judged]
    for line in old:
        del line["variant"], line["strict"]
    head = ev.report(judged, old)[0]
    assert "변형 v1" in head and "50 금지" not in head
    assert ev.report(judged, []) == ["기록이 없다."]


def test_records_round_trip(tmp_path: Path, judged: list[ev.Case]) -> None:
    path = tmp_path / "test-r1.jsonl"
    assert ev.read_records(path) == []  # 아직 없는 파일
    path.write_text("\n".join(json.dumps(_line(c, 40)) for c in judged[:3]) + "\n\n")
    assert [x["key"] for x in ev.read_records(path)] == [c.key for c in judged[:3]]


def test_tokens_count_what_was_sent_and_returned() -> None:
    """입력은 호출마다 대화 전체 + 도구 정의, 출력은 응답 한 번. OpenAI 기준 추정이다."""
    counter = ev.Tokens(tool_tokens=10)
    counter.on_chat_model_start({}, [[HumanMessage("너프 인가 버프 인가")]])
    answer = AIMessage(
        "좋다",
        tool_calls=[{"name": "Judgment", "args": {"nerf_prob": 70}, "id": "j"}],
        usage_metadata={"input_tokens": 30, "output_tokens": 5, "total_tokens": 35},
    )
    counter.on_llm_end(LLMResult(generations=[[ChatGeneration(message=answer)]]))
    assert counter.calls == 1
    assert counter.input == 4 + 4 + 10  # 낱말 넷 + 역할 몫 + 도구 정의
    assert counter.output > 4
    assert (counter.local_in, counter.local_out) == (30, 5)


def test_stated_share_reads_the_counts_the_model_copied() -> None:
    """이유가 옮겨 적은 이웃 셈을 뽑는다 — 없으면 None."""
    assert ev.stated_share("유사 사례 25 건 중 버프 18 건, 너프 7 건") == 7 / 25
    assert ev.stated_share("너프 20 건, 버프 5 건이다") == 20 / 25
    assert ev.stated_share("근거가 부족하다") is None
    assert ev.stated_share("") is None


def test_stated_share_reads_both_word_orders() -> None:
    """**어순이 모델마다 다르다.** 한쪽만 잡으면 그 모델만 0 건이 되어 비교가 거짓이 된다.

    로컬은 「버프 18 건」, gpt-4.1-mini 는 「18건이 버프」로 쓴다.
    """
    assert ev.stated_share("과거 25건 사례 중 18건이 버프였고 7건이 너프였다") == 7 / 25
    assert ev.stated_share("25개 사례 중 18건이 버프였고") == 7 / 25
    assert ev.stated_share("비슷한 25개 사례 중 23건이 버프 조정이었다") == 2 / 25


def test_stated_share_refuses_a_count_it_cannot_close() -> None:
    """한쪽만 있고 총합을 모르면 비율을 지어내지 않는다."""
    assert ev.stated_share("18건이 버프였다") is None
    assert ev.stated_share("25개 사례 중 30건이 버프였다") is None


def test_contradictions_catch_a_number_that_fights_its_own_reason() -> None:
    """**옛 형식이 샜던 자리다.** 「버프가 다수」라고 써 놓고 98 을 적는다."""
    lines = [
        {"abstain": False, "nerf_prob": 98, "reason": "버프 24 건, 너프 1 건"},
        {"abstain": False, "nerf_prob": 15, "reason": "버프 23 건, 너프 2 건"},
        {"abstain": False, "nerf_prob": 80, "reason": "버프 5 건, 너프 20 건"},
        {"abstain": True, "nerf_prob": 50, "reason": "버프 24 건, 너프 1 건"},
        {"abstain": False, "nerf_prob": 60, "reason": "셈이 없다"},
    ]
    assert ev.contradictions(lines) == (1, 3)


def test_contradictions_ignore_a_split_that_is_nearly_even() -> None:
    """반반에 가까우면 어느 쪽이라 해도 반대라고 하지 않는다."""
    lines = [{"abstain": False, "nerf_prob": 60, "reason": "버프 13 건, 너프 12 건"}]
    assert ev.contradictions(lines) == (0, 0)


def test_push_shows_which_side_the_scores_get_dragged_toward(
    judged: list[ev.Case],
) -> None:
    """한쪽만 가운데로 밀리면 두 무리가 겹쳐 순위가 무너진다."""
    low = [c for c in judged if c.b5 <= 0.2]
    high = [c for c in judged if c.b5 >= 0.8]
    if not (low and high):
        pytest.skip("이 표본에는 한쪽으로 쏠린 사례가 없다")
    pairs = [(c, c.b5 + 0.3) for c in low] + [(c, c.b5) for c in high]
    buff_push, nerf_push = ev.push(pairs)
    assert buff_push == pytest.approx(0.3)
    assert nerf_push == pytest.approx(0.0)
