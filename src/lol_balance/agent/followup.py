"""해설에 대해 더 묻는다 — 교재 3장 Short-term memory · 7장 Checkpointer · Backend.

    thread_id  세션 · 패치 · 챔피언. 같은 챔피언을 다시 고르면 이전 대화를 잇는다
    백엔드     SqliteSaver — 화면을 껐다 켜도 대화가 남는다 (InMemorySaver 는 사라진다)

첫 질문 때 **해설자가 받은 입력과 해설을 대화 맨 앞에 깐다.** 그다음부터는
Checkpointer 가 기억하므로 새 질문만 보낸다 — 교재 7장 「동일한 Thread ID 로 호출하면
이전 대화 상태를 불러와 이어감」.

## 후속 답은 코드 대조를 거치지 않는다

자유 서술이다. ADR 0007 — 글에서 값을 뽑는 파서를 만들지 않는다. 그래서 화면에
「대조 안 됨」을 붙인다. 판단의 근거로 쓰려면 해설(대조를 거친 칸)을 본다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver

from lol_balance.agent.data import Corpus
from lol_balance.agent.judge import (
    MODEL,
    RULES,
    Context,
    Graph,
    Step,
    _steps,
    chat_model,
    explain_task,
    guards,
)
from lol_balance.agent.tools import make_tools

SYSTEM = (
    """당신은 리그 오브 레전드 밸런스 조정 판단을 돕는 해설자입니다.
앞에서 {champion} ({at} → {nxt})에 대한 해설을 했고, 사람이 그 해설에 대해 더 묻습니다.
최종 판단은 사람이 합니다.

## 도구 — 전부 {at} 이전 기록만 보입니다
- find_similar_cases  R1 비슷했던 과거 사례
- lookup_stats        R3 이 챔피언의 과거 지표
- search_patch_notes  R2 과거에 무엇을 얼마나 바꿨나

필요하면 도구로 확인하고, 짧게 한국어로 답합니다. 근거에는 출처(R1·R2·R3·베이스라인)를
괄호로 붙입니다.

"""
    + RULES
)


def checkpointer(path: Path) -> SqliteSaver:
    """교재 7장 Backend — 파일 하나에 대화 상태를 남긴다.

    Gradio 는 요청을 여러 스레드에서 받으므로 `check_same_thread=False` 로 연다.
    SqliteSaver 가 안에서 잠금을 건다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(str(path), check_same_thread=False))


def thread_id(session: str, at: str, champion: str) -> str:
    return f"{session}:{at}:{champion}"


def build_followup(
    corpus: Corpus,
    ctx: Context,
    saver: SqliteSaver,
    model: str = MODEL,
    **model_kwargs: Any,
) -> Graph:
    return create_agent(
        model=chat_model(model, **model_kwargs),
        tools=make_tools(corpus, ctx.at),
        system_prompt=SYSTEM.format(champion=ctx.champion, at=ctx.at, nxt=ctx.nxt),
        checkpointer=saver,
        middleware=guards(),
    )


def _config(thread: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread}}


def started(agent: Graph, thread: str) -> bool:
    return bool(agent.get_state(_config(thread)).values.get("messages"))


def ask(
    agent: Graph,
    thread: str,
    question: str,
    corpus: Corpus,
    ctx: Context,
    explanation: str,
) -> tuple[str, list[Step]]:
    """한 번 묻는다. 첫 질문이면 해설 맥락을 깔고, 아니면 새 질문만 보낸다.

    반환 — (답, 이번에 부른 도구 호출).
    """
    first = not started(agent, thread)
    messages: list[BaseMessage] = (
        [
            HumanMessage(explain_task(corpus, ctx)),
            AIMessage(explanation or "(해설 없음)"),
            HumanMessage(question),
        ]
        if first
        else [HumanMessage(question)]
    )
    before = len(agent.get_state(_config(thread)).values.get("messages", []))
    state = agent.invoke({"messages": messages}, _config(thread))
    new = state["messages"][before:]
    answer = next(
        (m.content for m in reversed(new) if isinstance(m, AIMessage) and m.content),
        "",
    )
    return str(answer), _steps(new)


def _shown(messages: Sequence[BaseMessage]) -> list[dict[str, str]]:
    """화면에 보일 대화 — 질문과 글로 된 답만. 도구 호출만 담은 메시지는 뺀다."""
    out = []
    for m in messages:
        if isinstance(m, HumanMessage):
            out.append({"role": "user", "content": str(m.content)})
        elif isinstance(m, AIMessage) and m.content:
            out.append({"role": "assistant", "content": str(m.content)})
    return out


def history(agent: Graph, thread: str) -> list[dict[str, str]]:
    """화면에 보일 대화 — **깔아 둔 해설 맥락(앞의 두 메시지)은 뺀다.**"""
    return _shown(agent.get_state(_config(thread)).values.get("messages", [])[2:])


def saved_history(saver: SqliteSaver, thread: str) -> list[dict[str, str]]:
    """에이전트를 만들지 않고 **저장소에서 바로** 읽는다 — 챔피언을 다시 고를 때 쓴다."""
    found = saver.get_tuple(_config(thread))
    if found is None:
        return []
    values: dict[str, Any] = found.checkpoint.get("channel_values", {})
    return _shown(values.get("messages", [])[2:])
