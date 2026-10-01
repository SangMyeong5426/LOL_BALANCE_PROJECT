"""해설에 대해 더 묻기 — Checkpointer 가 대화를 잇고, 스레드끼리 섞이지 않고,
파일 백엔드라 다시 열어도 남는지. 그리고 **답이 코드 대조를 거치는지**
(extension 3절 4항). 키도 모델도 원자료도 필요 없다.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

import lol_balance.agent.followup as fu
import lol_balance.agent.judge as judge
from lol_balance import spend
from lol_balance.agent.data import Corpus
from lol_balance.agent.judge import Context, Graph, build_context

Setup = tuple[Corpus, Context, Graph, Path]


class Scripted(FakeMessagesListChatModel):
    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Scripted:
        return self


@pytest.fixture
def setup(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Setup:
    ctx = build_context(tiny_corpus, "C3", "15_14")
    model = Scripted(
        responses=[
            AIMessage("첫 답"),
            AIMessage("둘째 답"),
            AIMessage("다른 스레드 답"),
        ]
    )
    monkeypatch.setattr(fu, "chat_model", lambda *a, **k: model)
    path = tmp_path / "followup.sqlite"
    agent = fu.build_followup(tiny_corpus, ctx, fu.checkpointer(path))
    return tiny_corpus, ctx, agent, path


def test_second_question_remembers_the_first(setup: Setup) -> None:
    corpus, ctx, agent, _ = setup
    thread = fu.thread_id("s1", ctx.at, ctx.champion)
    assert not fu.started(agent, thread)

    a1 = fu.ask(agent, thread, "왜 너프 쪽이야?", corpus, ctx, "해설 본문")
    a2 = fu.ask(agent, thread, "그럼 버프 신호는?", corpus, ctx, "해설 본문")
    assert (a1.text, a2.text) == ("첫 답", "둘째 답")

    messages = agent.get_state({"configurable": {"thread_id": thread}}).values[
        "messages"
    ]
    # 깔아 둔 맥락 2 + (질문·답) × 2 — 둘째 호출은 새 질문만 보냈다
    assert len(messages) == 6
    assert isinstance(messages[0], HumanMessage)
    assert "통계 모델 점수" in messages[0].content
    assert messages[1].content == "해설 본문"


def test_threads_do_not_mix(setup: Setup) -> None:
    corpus, ctx, agent, _ = setup
    a = fu.thread_id("s1", ctx.at, ctx.champion)
    b = fu.thread_id("s2", ctx.at, ctx.champion)  # 다른 세션
    fu.ask(agent, a, "질문 A", corpus, ctx, "해설")
    assert not fu.started(agent, b)
    assert fu.history(agent, b) == []


def test_file_backend_survives_a_restart(setup: Setup) -> None:
    """교재 7장 Backend — 새 연결로 열어도 대화가 남는다. InMemorySaver 라면 사라진다."""
    corpus, ctx, agent, path = setup
    thread = fu.thread_id("s1", ctx.at, ctx.champion)
    fu.ask(agent, thread, "남아 있나?", corpus, ctx, "")  # 해설이 비어도 깐다

    reopened = fu.build_followup(corpus, ctx, fu.checkpointer(path))
    shown = fu.history(reopened, thread)
    # 화면에는 깔아 둔 해설 맥락을 빼고 질문·답만 보인다. 답에는 대조 결과가 붙는다 —
    # 글로만 답했으니 「대조할 수 없다」다
    assert [m["role"] for m in shown] == ["user", "assistant"]
    assert shown[0]["content"] == "남아 있나?"
    assert shown[1]["content"].startswith("첫 답") and "⚪" in shown[1]["content"]


def test_saved_history_reads_without_building_an_agent(setup: Setup) -> None:
    """챔피언을 다시 고를 때 쓰는 길 — 저장소에서 바로 읽어도 같은 대화가 나온다."""
    corpus, ctx, agent, path = setup
    thread = fu.thread_id("s9", ctx.at, ctx.champion)
    fu.ask(agent, thread, "읽히나?", corpus, ctx, "해설")
    assert fu.saved_history(fu.checkpointer(path), thread) == fu.history(agent, thread)
    assert fu.saved_history(fu.checkpointer(path), "없는:스레드") == []


def test_followup_can_measure_an_adjustment(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """후속 질문 에이전트가 **조정 전후 도구**를 쥔다. 「이 챔피언 너프 후에 뭐가
    바뀌었나」에 모델이 숫자를 지어내지 않고 도구가 계산한 값을 받는다
    (extension 3절 1항). 모델만 가짜고 도구와 루프는 진짜다."""
    ctx = build_context(tiny_corpus, "C3", "15_14")
    row = next(
        r
        for r in tiny_corpus.rows
        if r.patch == "15_12" and r.direction_next in ("nerf", "buff")
    )
    model = Scripted(
        responses=[
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "effect_of",
                        "args": {"champion": row.champion, "patch": "15_13"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage("도구가 준 전후를 봤다"),
        ]
    )
    monkeypatch.setattr(fu, "chat_model", lambda *a, **k: model)
    agent = fu.build_followup(
        tiny_corpus, ctx, fu.checkpointer(tmp_path / "followup.sqlite")
    )

    reply = fu.ask(
        agent,
        fu.thread_id("s1", ctx.at, ctx.champion),
        "조정 후에 뭐가 바뀌었나?",
        tiny_corpus,
        ctx,
        "해설",
    )

    assert reply.text == "도구가 준 전후를 봤다"
    assert [s.tool for s in reply.steps] == ["effect_of"]
    assert "%p" in reply.steps[0].output and "판수" in reply.steps[0].output
    assert "effect_of" in fu.SYSTEM


# ── 답 대조 — 후속 질문의 답도 같은 대조를 거친다 ───────────────────────


def tool_call(name: str, args: dict[str, Any], n: int) -> dict[str, Any]:
    return {"name": name, "args": args, "id": f"call_{n}", "type": "tool_call"}


def conversation(
    tiny_corpus: Corpus,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    responses: list[AIMessage],
) -> tuple[Context, Graph, str]:
    ctx = build_context(tiny_corpus, "C3", "15_14")
    monkeypatch.setattr(fu, "chat_model", lambda *a, **k: Scripted(responses=responses))
    agent = fu.build_followup(
        tiny_corpus, ctx, fu.checkpointer(tmp_path / "followup.sqlite")
    )
    return ctx, agent, fu.thread_id("s1", ctx.at, ctx.champion)


def measured(corpus: Corpus) -> tuple[str, str]:
    """15_13 에서 너프 · 버프된 챔피언과, 도구가 낼 그 조정 후 승률."""
    row = next(
        r
        for r in corpus.rows
        if r.patch == "15_12" and r.direction_next in ("nerf", "buff")
    )
    after = corpus.row(row.champion, "15_13")
    assert after is not None
    return row.champion, f"{after.win_rate:.1%}"


def test_an_answer_built_on_the_tools_passes_the_check(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """도구가 준 숫자를 옮겨 적은 답은 ✅ 다. **무엇을 확인했는지 화면에 남는다.**"""
    champion, win = measured(tiny_corpus)
    ctx, agent, thread = conversation(
        tiny_corpus,
        tmp_path,
        monkeypatch,
        [
            AIMessage(
                "",
                tool_calls=[
                    tool_call("effect_of", {"champion": champion, "patch": "15_13"}, 1)
                ],
            ),
            AIMessage(
                "",
                tool_calls=[
                    tool_call(
                        "Answer",
                        {
                            "answer": f"조정 뒤 승률은 {win} 다. 표본이 얇다.",
                            "numbers": [win],
                        },
                        2,
                    )
                ],
            ),
        ],
    )

    reply = fu.ask(agent, thread, "조정 뒤 승률은?", tiny_corpus, ctx, "해설")

    # 작은 패널은 챔피언당 수천 판이라 도구가 표본 주의를 낸다 — 답이 그것을 옮겼다
    assert reply.text == f"조정 뒤 승률은 {win} 다. 표본이 얇다."
    assert reply.check.mark == "✅" and reply.check.numbers == 1
    assert [s.tool for s in reply.steps] == [
        "effect_of"
    ]  # Answer 는 도구 호출이 아니다
    shown = fu.history(agent, thread)
    assert shown[-1]["content"].startswith(reply.text)
    assert reply.check.line() in shown[-1]["content"]


def test_a_number_the_tools_never_gave_is_flagged_on_screen(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """도구를 부르지 않고 숫자를 쓰면 ⚠ 다 — 다시 열어 봐도 그 표시가 남는다."""
    ctx, agent, thread = conversation(
        tiny_corpus,
        tmp_path,
        monkeypatch,
        [
            AIMessage(
                "",
                tool_calls=[
                    tool_call(
                        "Answer",
                        {"answer": "승률이 87.6% 로 올랐다.", "numbers": ["87.6%"]},
                        1,
                    )
                ],
            )
        ],
    )

    reply = fu.ask(agent, thread, "올랐어?", tiny_corpus, ctx, "해설")

    assert reply.check.mark == "⚠" and "87.6%" in reply.check.line()
    saved = fu.saved_history(fu.checkpointer(tmp_path / "followup.sqlite"), thread)
    assert "⚠" in saved[-1]["content"] and "87.6%" in saved[-1]["content"]


def test_numbers_from_an_earlier_tool_call_stay_usable(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """앞 질문에서 도구가 준 숫자는 다음 질문에서도 출처다 — 대화가 이어진다. **모델이
    앞에서 쓴 글은 출처가 아니다** — 거기서 지어낸 숫자를 되풀이하면 또 걸린다."""
    champion, win = measured(tiny_corpus)
    ctx, agent, thread = conversation(
        tiny_corpus,
        tmp_path,
        monkeypatch,
        [
            AIMessage(
                "",
                tool_calls=[
                    tool_call("effect_of", {"champion": champion, "patch": "15_13"}, 1)
                ],
            ),
            AIMessage(
                "",
                tool_calls=[
                    tool_call(
                        "Answer", {"answer": "승률이 87.6% 다.", "numbers": []}, 2
                    )
                ],
            ),
            AIMessage(
                "",
                tool_calls=[tool_call("Answer", {"answer": f"정확히는 {win} 다."}, 3)],
            ),
            AIMessage(
                "",
                tool_calls=[tool_call("Answer", {"answer": "앞서 말한 87.6% 다."}, 4)],
            ),
        ],
    )

    first = fu.ask(agent, thread, "승률은?", tiny_corpus, ctx, "해설")
    second = fu.ask(agent, thread, "정확히?", tiny_corpus, ctx, "해설")
    third = fu.ask(agent, thread, "아까 뭐라고 했지?", tiny_corpus, ctx, "해설")

    assert first.check.mark == "⚠"  # 도구가 준 적 없는 숫자
    assert second.check.mark == "✅"  # 앞 질문의 도구 출력에 있다
    assert third.check.mark == "⚠"  # 모델의 앞선 답은 출처가 아니다


def test_saved_answers_reopen_without_an_unregistered_type_warning(
    tiny_corpus: Corpus,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """대화 파일에는 구조화된 답(`Answer`)이 같이 남는다. 저장소가 그 형식을 **모르는
    형식**으로 읽으면 langgraph 가 경고하고, 다음 판에서는 막는다고 한다 — 그러면 화면을
    껐다 켰을 때 대화가 안 열린다. 형식을 등록해 둔다."""
    ctx, agent, thread = conversation(
        tiny_corpus,
        tmp_path,
        monkeypatch,
        [AIMessage("", tool_calls=[tool_call("Answer", {"answer": "모른다."}, 1)])],
    )
    fu.ask(agent, thread, "?", tiny_corpus, ctx, "해설")

    with caplog.at_level("WARNING"):
        saved = fu.saved_history(fu.checkpointer(tmp_path / "followup.sqlite"), thread)

    assert saved[-1]["content"].startswith("모른다.")
    assert "unregistered type" not in caplog.text


def test_a_thin_sample_shows_on_screen_whatever_the_model_writes(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**표본이 작으면 답에 그 사실을 적는다**(extension 3절 7항). 모델이 그 말을 빼면
    ⚠ 가 붙고, 빼든 말든 **도구가 낸 주의는 화면이 답 아래에 붙인다** — 사람이 못 보고
    지나가지 않게."""
    champion, win = measured(tiny_corpus)
    call = AIMessage(
        "",
        tool_calls=[
            tool_call("effect_of", {"champion": champion, "patch": "15_13"}, 1)
        ],
    )

    def answer(text: str, n: int) -> AIMessage:
        return AIMessage(
            "", tool_calls=[tool_call("Answer", {"answer": text, "numbers": [win]}, n)]
        )

    ctx, agent, thread = conversation(
        tiny_corpus,
        tmp_path,
        monkeypatch,
        [
            call,
            answer(f"조정 뒤 승률은 {win} 다.", 2),
            call,
            answer(f"조정 뒤 승률은 {win} 다. 표본이 얇아 단정하기 어렵다.", 4),
        ],
    )

    dropped = fu.ask(agent, thread, "조정 뒤 승률은?", tiny_corpus, ctx, "해설")
    kept = fu.ask(agent, thread, "다시 알려줘", tiny_corpus, ctx, "해설")

    assert dropped.check.mark == "⚠" and "표본이 얇다" in dropped.check.line()
    assert kept.check.mark == "✅"
    assert [c.kind for c in dropped.cautions] == ["표본"]
    shown = fu.history(agent, thread)
    assert "도구가 낸 주의" in shown[1]["content"] and "판(기준" in shown[1]["content"]
    assert "표본이 얇다" in fu.SYSTEM and "경계 밖" in fu.SYSTEM


def test_note_blocks_are_attached_by_code_not_by_the_model(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**근거로 본 노트 블록은 코드가 붙인다**(ADR 0017). 모델은 답과 숫자만 적고, 화면이
    이번 질문에서 노트 도구가 돌려준 블록을 답 아래에 적는다 — 그래서 없는 블록이 나올 수
    없다. **앞 질문에서 본 블록을 뒤 질문의 답에 붙이지 않는다.**"""
    ctx, agent, thread = conversation(
        tiny_corpus,
        tmp_path,
        monkeypatch,
        [
            AIMessage(
                "",
                tool_calls=[
                    tool_call(
                        "search_patch_notes", {"champion": "C3", "patch": "15_12"}, 1
                    )
                ],
            ),
            AIMessage(
                "",
                tool_calls=[
                    tool_call(
                        "Answer",
                        {
                            "answer": "Q 의 피해량을 60 에서 50 으로 줄였다.",
                            "numbers": ["60", "50"],
                        },
                        2,
                    )
                ],
            ),
            AIMessage(
                "", tool_calls=[tool_call("Answer", {"answer": "너프입니다."}, 3)]
            ),
        ],
    )

    first = fu.ask(agent, thread, "15_12 에 뭘 바꿨어?", tiny_corpus, ctx, "해설")
    second = fu.ask(agent, thread, "그게 너프야?", tiny_corpus, ctx, "해설")

    assert first.blocks == ["[15_12] Q - Blade"]
    assert first.check.mark == "✅" and first.check.numbers == 2
    assert second.blocks == []  # 이번 질문에서는 노트 도구를 부르지 않았다
    shown = fu.history(agent, thread)
    assert "근거로 본 노트 블록 — [15_12] Q - Blade" in shown[1]["content"]
    assert "근거로 본 노트 블록" not in shown[3]["content"]
    # 모델에게 인용을 적으라고 하지 않는다
    assert "notes" not in fu.SYSTEM.split("## 답하는 방법")[1]


def test_a_plain_text_answer_cannot_be_checked(setup: Setup) -> None:
    """구조화된 답 없이 글로만 답하면 대조할 수 없다 — ⚪ 로 보인다."""
    corpus, ctx, agent, _ = setup
    reply = fu.ask(
        agent, fu.thread_id("s1", ctx.at, ctx.champion), "?", corpus, ctx, "해설"
    )
    assert reply.text == "첫 답" and reply.check.mark == "⚪"


# ── 비용 — 쓴 돈이 장부에 남고, 상한에 닿으면 묻기 전에 멈춘다 ─────────────

PAID = "openai:gpt-4.1-mini"


def paid(text: str) -> AIMessage:
    """유료 모델이 보고하는 토큰 수가 붙은 답."""
    return AIMessage(
        text,
        usage_metadata={
            "input_tokens": 2000,
            "output_tokens": 150,
            "total_tokens": 2150,
        },
    )


@pytest.fixture
def book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> spend.Ledger:
    """장부는 임시 폴더다 — `data/spend.jsonl` 을 건드리지 않는다. 진짜 모델도 안 부른다."""
    ledger = spend.Ledger(tmp_path / "spend.jsonl")
    monkeypatch.setattr(spend, "ledger", lambda root: ledger)
    monkeypatch.setenv("LOL_BALANCE_SPEND_CAP", "1.0")
    return ledger


def paid_conversation(
    tiny_corpus: Corpus,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    responses: list[AIMessage],
) -> tuple[Context, Graph, str, dict[str, Any]]:
    """`chat_model` 이 받은 인자를 적어 두고, 장부가 붙은 가짜 모델을 돌려준다."""
    seen: dict[str, Any] = {}

    def fake(model: str, **kwargs: Any) -> Scripted:
        seen.update(kwargs, model=model)
        guard = judge.SpendGuard(model, why=kwargs.get("why", "agent"))
        return Scripted(responses=responses, callbacks=[guard])

    monkeypatch.setattr(fu, "chat_model", fake)
    ctx = build_context(tiny_corpus, "C3", "15_14")
    agent = fu.build_followup(
        tiny_corpus, ctx, fu.checkpointer(tmp_path / "followup.sqlite"), PAID
    )
    return ctx, agent, fu.thread_id("s1", ctx.at, ctx.champion), seen


def test_a_paid_answer_is_written_to_the_ledger_as_qa(
    tiny_corpus: Corpus,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    book: spend.Ledger,
) -> None:
    """**쓴 비용이 장부에 남는다.** 후속 질문에 쓴 돈은 `qa` 로 적히고, 그 답에 든 돈이
    답과 함께 돌아온다 — 화면이 보여 준다."""
    ctx, agent, thread, seen = paid_conversation(
        tiny_corpus, tmp_path, monkeypatch, [paid("모른다.")]
    )

    reply = fu.ask(agent, thread, "?", tiny_corpus, ctx, "해설")

    assert seen["why"] == "qa"
    expected = spend.cost(PAID, 2000, 150)
    assert reply.usd == pytest.approx(expected) and expected > 0
    assert book.spent("qa") == pytest.approx(expected)


def test_at_the_cap_a_question_is_never_sent(
    tiny_corpus: Corpus,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    book: spend.Ledger,
) -> None:
    """상한에 닿았으면 **묻기 전에** 멈춘다 — 답도 장부도 그대로다."""
    book.add(PAID, 5_000_000, 0, why="test")  # $2.00 — 상한 $1 을 넘는다
    before = book.spent()
    ctx, agent, thread, _ = paid_conversation(
        tiny_corpus, tmp_path, monkeypatch, [paid("나가면 안 되는 답")]
    )

    with pytest.raises(spend.SpendCapReached):
        fu.ask(agent, thread, "?", tiny_corpus, ctx, "해설")

    assert book.spent() == before
    assert fu.history(agent, thread) == [] or "나가면 안 되는 답" not in str(
        fu.history(agent, thread)
    )


def test_a_local_answer_costs_nothing(setup: Setup) -> None:
    corpus, ctx, agent, _ = setup
    reply = fu.ask(
        agent, fu.thread_id("s1", ctx.at, ctx.champion), "?", corpus, ctx, "해설"
    )
    assert reply.usd == 0.0
