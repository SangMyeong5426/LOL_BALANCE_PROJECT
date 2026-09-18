"""해설에 대해 더 묻기 — Checkpointer 가 대화를 잇고, 스레드끼리 섞이지 않고,
파일 백엔드라 다시 열어도 남는지. 키도 모델도 원자료도 필요 없다.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

import lol_balance.agent.followup as fu
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

    a1, _ = fu.ask(agent, thread, "왜 너프 쪽이야?", corpus, ctx, "해설 본문")
    a2, _ = fu.ask(agent, thread, "그럼 버프 신호는?", corpus, ctx, "해설 본문")
    assert (a1, a2) == ("첫 답", "둘째 답")

    messages = agent.get_state({"configurable": {"thread_id": thread}}).values[
        "messages"
    ]
    # 깔아 둔 맥락 2 + (질문·답) × 2 — 둘째 호출은 새 질문만 보냈다
    assert len(messages) == 6
    assert isinstance(messages[0], HumanMessage)
    assert "베이스라인 점수" in messages[0].content
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
    # 화면에는 깔아 둔 해설 맥락을 빼고 질문·답만 보인다
    assert shown == [
        {"role": "user", "content": "남아 있나?"},
        {"role": "assistant", "content": "첫 답"},
    ]


def test_saved_history_reads_without_building_an_agent(setup: Setup) -> None:
    """챔피언을 다시 고를 때 쓰는 길 — 저장소에서 바로 읽어도 같은 대화가 나온다."""
    corpus, ctx, agent, path = setup
    thread = fu.thread_id("s9", ctx.at, ctx.champion)
    fu.ask(agent, thread, "읽히나?", corpus, ctx, "해설")
    assert fu.saved_history(fu.checkpointer(path), thread) == fu.history(agent, thread)
    assert fu.saved_history(fu.checkpointer(path), "없는:스레드") == []
