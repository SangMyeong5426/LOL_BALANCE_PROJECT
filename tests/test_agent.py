"""에이전트 루프를 **API 키 없이, 모델 없이** 끝까지 돌린다.

대본대로 도구를 부르는 가짜 모델을 끼운다. 모델만 가짜고 도구·루프·구조화
출력은 진짜다. 배선이 맞는지, 그리고 **경계가 도구 안에서 지켜지는지**를
여기서 확인한다.

**원자료 없이 돈다** — `tiny_corpus`(conftest)가 작은 패널을 만든다. 실제
패널로만 볼 수 있는 것(Senna 16_13 의 답)은 `needs_data` 로 따로 두고,
clone 직후처럼 `data/` 가 없으면 건너뛴다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolCall
from langchain_core.prompt_values import PromptValue
from langchain_core.runnables import RunnableLambda

import lol_balance.agent.data as data
import lol_balance.agent.judge as judge
from lol_balance.agent.data import Corpus, available, lifetime_pro, load
from lol_balance.agent.judge import (
    EXPLAIN,
    LOOKUPS,
    SYSTEM,
    Run,
    Step,
    build_context,
    build_explain_chain,
    candidates,
    explain_task,
    invented_tension,
    misquoted_warning,
    misread_baseline,
    record,
    save,
    stream,
    task,
    unverified,
)
from lol_balance.agent.schema import Evidence, Explanation, Judgment, StrictJudgment
from lol_balance.agent.tools import make_tools
from lol_balance.store import write_panel

needs_data = pytest.mark.skipif(
    not available(), reason="data/ 가 없다 — clone 직후에는 정상이다"
)

AT = "15_14"  # tiny_corpus 에서 답이 있는 마지막 패치


class Scripted(FakeMessagesListChatModel):
    """정해 둔 메시지를 차례로 낸다. 도구를 묶어도 자기 자신을 돌려준다."""

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Scripted:
        return self


def call(name: str, args: dict[str, Any], n: int) -> ToolCall:
    return {"name": name, "args": args, "id": f"call_{n}", "type": "tool_call"}


def patch_rows(text: str) -> list[str]:
    """도구 출력에서 패치 이름으로 시작하는 줄만."""
    return [line for line in text.splitlines() if re.match(r"\s*1[3-6]_\d+", line)]


# ── 루프 ───────────────────────────────────────────────────────────────


def test_loop_runs_real_tools_and_returns_judgment(tiny_corpus: Corpus) -> None:
    ctx = build_context(tiny_corpus, "C3", AT)
    model = Scripted(
        responses=[
            AIMessage(
                "",
                tool_calls=[
                    call("find_similar_cases", {"champion": "C3", "among": "all"}, 1),
                    call(
                        "find_similar_cases", {"champion": "C3", "among": "adjusted"}, 2
                    ),
                    call("lookup_stats", {"champion": "C3"}, 3),
                ],
            ),
            AIMessage(
                "", tool_calls=[call("search_patch_notes", {"champion": "C3"}, 4)]
            ),
            AIMessage(
                "",
                tool_calls=[
                    call(
                        "Judgment",
                        {
                            "adjust_prob": 60,
                            "nerf_prob": 85,
                            "reason": "사례 대부분이 너프다",
                            "evidence": [
                                {"source": "R1", "text": "닮은 사례 중 너프 다수"}
                            ],
                        },
                        5,
                    )
                ],
            ),
        ]
    )
    agent = create_agent(
        model=model,
        tools=make_tools(tiny_corpus, ctx.at),
        system_prompt=SYSTEM.format(at=ctx.at, nxt=ctx.nxt, champion="C3", base=15),
        response_format=Judgment,
    )
    runs = list(stream(agent, task(tiny_corpus, ctx)))
    last = runs[-1]

    assert last.error is None, last.error
    assert isinstance(last.judgment, Judgment)
    assert last.judgment.nerf_prob == 85
    # 네 번 부른 도구가 전부 기록되고, 구조화 출력은 호출 기록에 안 섞인다
    assert [s.tool for s in last.steps] == [
        "find_similar_cases",
        "find_similar_cases",
        "lookup_stats",
        "search_patch_notes",
    ]
    assert all(s.output for s in last.steps), "도구 출력이 비었다"
    assert len(runs) > 2  # 스트리밍이 중간 상태를 여러 번 냈다

    line = record(ctx, last)
    assert line["condition"] == "named"  # 이름을 줬으므로 평가에 못 쓴다
    assert line["nerf_prob"] == 85 and line["unverified_sources"] == []
    assert line["tools"].count("find_similar_cases") == 2


def test_a_failing_model_is_reported_not_raised(tiny_corpus: Corpus) -> None:
    """모델명 오류·Ollama 미기동 같은 실패가 화면에 보여야 한다 — 죽지 않는다."""
    ctx = build_context(tiny_corpus, "C3", AT)
    agent = create_agent(
        model=Scripted(responses=[]), tools=make_tools(tiny_corpus, ctx.at)
    )
    last = list(stream(agent, "질문"))[-1]
    assert last.error is not None and last.judgment is None
    assert record(ctx, last)["abstain"] is True


def test_target_label_never_reaches_the_model(tiny_corpus: Corpus) -> None:
    """기준 행의 라벨이 곧 정답이다. 모델 입력 어디에도 없어야 한다."""
    row = next(
        r for r in tiny_corpus.rows if r.patch == AT and r.direction_next == "nerf"
    )
    ctx = build_context(tiny_corpus, row.champion, AT)
    prompt = task(tiny_corpus, ctx)
    assert ctx.answer == "너프"
    assert "너프" not in prompt and "nerf" not in prompt.lower()


@needs_data
def test_target_label_never_reaches_the_model_on_the_real_panel() -> None:
    ctx = build_context(load(), "Senna", "16_13")
    assert ctx.answer == "너프"
    assert "너프" not in task(load(), ctx)


def test_unknown_patch_or_champion_is_refused(tiny_corpus: Corpus) -> None:
    with pytest.raises(ValueError, match="없다"):
        build_context(tiny_corpus, "C99", AT)


# ── 도구 — 경계는 도구가 지킨다 ─────────────────────────────────────────


@pytest.mark.parametrize("tool", ["lookup_stats", "find_similar_cases"])
def test_tools_never_show_the_as_of_patch(tiny_corpus: Corpus, tool: str) -> None:
    """기준 패치 행(=정답을 품은 행)이 출력에 없어야 한다."""
    tools = {t.name: t for t in make_tools(tiny_corpus, AT)}
    out = tools[tool].invoke({"champion": "C3"})
    rows = patch_rows(out)
    assert rows, out
    assert not any(line.strip().startswith(AT) for line in rows), out


def test_patch_notes_cannot_reach_the_future(tiny_corpus: Corpus) -> None:
    tools = {t.name: t for t in make_tools(tiny_corpus, "15_13")}
    out = tools["search_patch_notes"].invoke({"champion": "C3", "patch": "15_15"})
    assert "경계 밖" in out and "Cooldown" not in out, out


def test_patch_notes_read_the_right_patch_and_skip_skins(tiny_corpus: Corpus) -> None:
    notes = {t.name: t for t in make_tools(tiny_corpus, AT)}["search_patch_notes"]
    blade = notes.invoke({"champion": "C3", "patch": "15_12"})
    assert "Damage reduced to 50 from 60" in blade
    stats = notes.invoke({"champion": "C3", "patch": "15.11"})  # 점 표기도 받는다
    assert "Base health" in stats and "Skin name" not in stats
    # 노트는 있는데 이 챔피언 절이 없다 — 다른 패치의 블록을 대신 내지 않는다
    assert "해당 절 없음" in notes.invoke({"champion": "C3", "patch": "15_10"})
    assert "노트가 없다" in notes.invoke({"champion": "C3", "patch": "15_13"})
    # 패치를 비우면 이 챔피언이 조정된 패치들로 좁혀 묻는다
    assert "C3" in notes.invoke({"champion": "C3"})


def test_unknown_champion_is_a_message_not_a_crash(tiny_corpus: Corpus) -> None:
    tools = {t.name: t for t in make_tools(tiny_corpus, AT)}
    assert "없다" in tools["lookup_stats"].invoke({"champion": "C99"})
    assert "비슷한 이름: C10" in tools["lookup_stats"].invoke({"champion": "C10x"})
    out = tools["find_similar_cases"].invoke({"champion": "C3", "among": "모두"})
    assert "among" in out


def test_anonymous_tools_hide_the_target(tiny_corpus: Corpus) -> None:
    """익명 조건 — 대상 이름도, 이웃의 이름·패치도 도구 출력에 없어야 한다."""
    row = next(r for r in tiny_corpus.rows if r.patch == AT and r.champion == "C3")
    tools = {
        t.name: t
        for t in make_tools(
            tiny_corpus, "15_13", target=row, alias="abc123", notes=False
        )
    }
    assert set(tools) == {"lookup_stats", "find_similar_cases"}
    stats = tools["lookup_stats"].invoke({"champion": "abc123"})
    assert "패치 전" in stats and "C3" not in stats
    cases = tools["find_similar_cases"].invoke(
        {"champion": "대상", "among": "adjusted", "k": 25}
    )
    body = cases.replace("(경계: 15_13 이전 기록만)", "")
    assert "C3" not in body and not re.search(r"\b1[3-6]_\d{1,2}\b", body)
    assert "익명 조건" in tools["lookup_stats"].invoke({"champion": "C5"})


def test_fixed_cases_give_the_same_evidence_as_b5(tiny_corpus: Corpus) -> None:
    """고정형 R1 은 인자가 대상 하나뿐이다 — 증거의 양을 모델이 못 정한다."""
    tools = make_tools(tiny_corpus, AT, stats=False, notes=False, fixed_cases=25)
    assert [t.name for t in tools] == ["find_similar_cases"]
    assert set(tools[0].args) == {"champion"}
    out = tools[0].invoke({"champion": "C3"})
    assert re.search(r"너프 \d+ · 버프 \d+", out) and "25종" in out


def test_citing_an_uncalled_tool_is_flagged() -> None:
    """로컬 모델이 도구를 하나도 안 부르고 R1·R3 를 출처로 적은 적이 있다."""
    run = Run(
        steps=[Step("lookup_stats", {"champion": "Senna"}, "…")],
        judgment=Judgment(
            adjust_prob=30,
            nerf_prob=70,
            reason="r",
            evidence=[
                Evidence(source="R3", text="부른 것"),
                Evidence(source="R1", text="안 부른 것"),
                Evidence(source="수치", text="처음 받은 지표"),
            ],
        ),
    )
    assert unverified(run) == ["R1"]
    assert unverified(Run()) == []


def test_fifty_is_refused_only_under_strict() -> None:
    """50 금지 — 「반반이다」와 「모른다」를 가른다. 기권이면 50 도 받는다."""
    assert Judgment(adjust_prob=100, nerf_prob=50, reason="r").nerf_prob == 50
    with pytest.raises(ValueError, match="50"):
        StrictJudgment(adjust_prob=100, nerf_prob=50, reason="r")
    assert StrictJudgment(adjust_prob=100, nerf_prob=50, reason="r", abstain=True)
    assert StrictJudgment.__name__ == "Judgment"  # 도구 이름이 여기서 나온다


def test_the_score_keeps_its_full_range() -> None:
    """**0~100 정수 그대로다.** 방향+확신으로 바꿔 봤다가 되돌린 자리다.

    한쪽을 고르라고 하면 모델이 틀린 쪽에 그냥 선다 — 세 비교 모두에서 더
    나빴다(ADR 0007 · 2026-09-21). 숫자 칸은 50 언저리로 망설일 수 있다.
    """
    assert Judgment(adjust_prob=100, nerf_prob=0, reason="r").nerf_prob == 0
    assert Judgment(adjust_prob=100, nerf_prob=100, reason="r").nerf_prob == 100
    for bad in (-1, 101):
        with pytest.raises(ValueError):
            Judgment(adjust_prob=100, nerf_prob=bad, reason="r")


# ── 해설자 ─────────────────────────────────────────────────────────────


def test_explainer_sees_the_baseline_but_never_the_answer(tiny_corpus: Corpus) -> None:
    """해설자는 베이스라인·경고를 받는다. **답은 절대 안 받는다.**"""
    ctx = replace(
        build_context(tiny_corpus, "C3", AT),
        answer="__정답__",  # 새어 나가면 바로 보이게
        warnings=["버프 주의 — 대회 출전이 크게 오른다"],
    )
    prompt = explain_task(tiny_corpus, ctx)
    assert "__정답__" not in prompt
    assert "베이스라인" in prompt and f"{ctx.baseline['버프'][1]}위" in prompt
    assert all(w in prompt for w in ctx.warnings)


def test_explainer_loop_returns_an_explanation(tiny_corpus: Corpus) -> None:
    ctx = build_context(tiny_corpus, "C3", AT)
    model = Scripted(
        responses=[
            AIMessage(
                "",
                tool_calls=[
                    call(
                        "find_similar_cases",
                        {"champion": "C3", "among": "adjusted", "k": 25},
                        1,
                    ),
                    call("lookup_stats", {"champion": "C3"}, 2),
                ],
            ),
            AIMessage(
                "",
                tool_calls=[
                    call(
                        "Explanation",
                        {
                            "summary": "승률이 높아 너프 후보로 올랐다",
                            "supporting": [
                                {"source": "R1", "text": "닮은 사례 대부분이 너프"},
                                {"source": "베이스라인", "text": "너프 3위"},
                            ],
                            "against": [{"source": "R2", "text": "부르지 않은 도구"}],
                            "stance": "부분 동의",
                            "baseline_side": "너프",
                        },
                        3,
                    )
                ],
            ),
        ]
    )
    agent = create_agent(
        model=model,
        tools=make_tools(tiny_corpus, ctx.at),
        system_prompt=EXPLAIN.format(at=ctx.at, nxt=ctx.nxt, champion=ctx.champion),
        response_format=ToolStrategy(Explanation),
    )
    last = list(stream(agent, explain_task(tiny_corpus, ctx)))[-1]
    assert last.error is None, last.error
    assert isinstance(last.judgment, Explanation)
    assert last.judgment.stance == "부분 동의"
    assert [s.tool for s in last.steps] == ["find_similar_cases", "lookup_stats"]
    # 베이스라인은 도구가 아니라 괜찮고, 부르지 않은 R2 만 잡힌다
    assert unverified(last) == ["R2"]


def test_explain_chain_fetches_all_evidence_and_hides_the_answer(
    tiny_corpus: Corpus,
) -> None:
    """체인은 조회 넷을 **반드시** 한다. 모델이 받은 프롬프트에 답이 없어야 한다."""
    ctx = replace(build_context(tiny_corpus, "C3", AT), answer="__정답__")
    seen: dict[str, str] = {}

    def fake_llm(prompt_value: PromptValue) -> Explanation:
        messages: list[BaseMessage] = prompt_value.to_messages()
        seen["text"] = "\n".join(str(m.content) for m in messages)
        return Explanation(summary="s", stance="동의", baseline_side="너프")

    out = build_explain_chain(tiny_corpus, ctx, llm=RunnableLambda(fake_llm)).invoke({})
    assert set(out["found"]) == set(LOOKUPS)
    assert all(v.strip() for v in out["found"].values())
    assert isinstance(out["answer"], Explanation)
    text = seen["text"]
    assert "__정답__" not in text
    assert "조정된 사례" in text and "베이스라인 점수" in text
    # 경계 — 조회 결과에 기준 패치 행이 없다
    body = out["found"]["R3 과거 지표"].replace(f"(경계: {AT} 이전 기록만)", "")
    assert AT not in body


def test_code_checks_catch_a_misread_explanation(tiny_corpus: Corpus) -> None:
    """로컬 2B 가 실제로 낸 오류 셋 — 방향을 거꾸로 읽고, 경고를 바꿔 옮기고,
    없는 경고와 부딪힌다고 적었다. 모델에게 묻지 않고 코드가 잰다."""
    ctx = replace(
        build_context(tiny_corpus, "C3", AT),
        baseline={"조정": (0.3, 20, 20), "너프": (0.19, 18, 20), "버프": (0.74, 2, 20)},
        warnings=["버프 주의 — 대회 출전이 크게 오른다"],
    )
    wrong = Explanation(
        summary="s",
        stance="반대",
        baseline_side="너프",
        tension="버프 후보인데 너프 주의",
        tension_quote="너프 주의",
    )
    assert misread_baseline(wrong, ctx) == "버프"
    assert misquoted_warning(wrong, ctx) is True

    right = Explanation(
        summary="s",
        stance="부분 동의",
        baseline_side="버프",
        tension="버프 후보인데 대회 출전이 뛴다",
        tension_quote="버프 주의",
    )
    assert misread_baseline(right, ctx) is None
    assert misquoted_warning(right, ctx) is False
    plain = Explanation(summary="s", stance="동의", baseline_side="버프")
    assert misquoted_warning(plain, ctx) is False  # 긴장을 안 적었으면 볼 것이 없다

    # 띄어쓰기만 다르면 같은 인용이다 — 로컬 9b 가 실제로 그렇게 옮겼다
    spaced = right.model_copy(
        update={"tension_quote": "버프 주의 — 대회 출전이 크게 오른 다"}
    )
    assert misquoted_warning(spaced, ctx) is False
    flipped = right.model_copy(
        update={"tension_quote": "너프 주의 — 대회 출전이 크게 오른다"}
    )
    assert misquoted_warning(flipped, ctx) is True

    quiet = replace(ctx, warnings=[])
    assert invented_tension(wrong, quiet) is True
    assert invented_tension(plain, quiet) is False


# ── 화면이 쓰는 것 ─────────────────────────────────────────────────────


def test_candidates_are_the_baseline_ranking(tiny_corpus: Corpus) -> None:
    top = candidates(tiny_corpus, AT, want="nerf", n=5)
    assert len(top) == 5
    scores = [score for _, score in top]
    assert scores == sorted(scores, reverse=True)
    assert {name for name, _ in top} <= set(tiny_corpus.champions(AT))


def test_saved_lines_are_appended(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(judge, "RUNS", tmp_path)
    ctx = build_context(tiny_corpus, "C3", AT)
    save(record(ctx, Run()), "demo.jsonl")
    save(record(ctx, Run()), "demo.jsonl")
    assert len((tmp_path / "demo.jsonl").read_text().splitlines()) == 2


# ── 적재 ───────────────────────────────────────────────────────────────


def test_load_reads_the_panel_and_marks_answered_patches(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = tmp_path / "panel.sqlite"
    write_panel(panel, tiny_corpus.rows)
    monkeypatch.setattr(data, "PANEL", panel)
    for name in ("NOTES", "RANKING", "ORACLE", "ITEMS", "RULES"):
        monkeypatch.setattr(data, name, tmp_path / "없음" / name)
    assert data.available()

    data.load.cache_clear()
    try:
        corpus = data.load()
    finally:
        data.load.cache_clear()  # 실제 패널을 쓰는 테스트가 이 결과를 받지 않게
    assert len(corpus.rows) == len(tiny_corpus.rows)
    assert corpus.patches[0] == "15_16" and corpus.labeled == set(corpus.patches)
    assert corpus.row("C3", AT) is not None and corpus.row("C3", "13_14") is None
    assert corpus.blocks == {} and corpus.rules == () and corpus.churn == {}
    assert lifetime_pro(corpus.rows, "C1") is None  # 20패치가 안 된다
