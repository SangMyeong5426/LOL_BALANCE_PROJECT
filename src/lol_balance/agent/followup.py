"""해설에 대해 더 묻는다 — 교재 3장 Short-term memory · 7장 Checkpointer · Backend.

    thread_id  세션 · 패치 · 챔피언. 같은 챔피언을 다시 고르면 이전 대화를 잇는다
    백엔드     SqliteSaver — 화면을 껐다 켜도 대화가 남는다 (InMemorySaver 는 사라진다)

첫 질문 때 **해설자가 받은 입력과 해설을 대화 맨 앞에 깐다.** 그다음부터는
Checkpointer 가 기억하므로 새 질문만 보낸다 — 교재 7장 「동일한 Thread ID 로 호출하면
이전 대화 상태를 불러와 이어감」.

## 후속 답도 코드 대조를 거친다 (2026-10-01)

답을 `Answer` 로 받는다 — 답 · 답에 쓴 숫자. 코드가 그 숫자를 도구 출력과 맞춰 보고
✅ · ⚪ · ⚠ 를 붙인다(`qa.check`, extension 3절). **근거로 본 노트 블록은 모델이 적지
않고 코드가 붙인다**(ADR 0017) — 이번 질문에서 노트 도구가 돌려준 블록이다.
**대조는 저장된 대화만으로 다시 계산한다** — 화면을 껐다 켜도 같은 표시가 나온다.

한때는 자유 서술이라 「대조 안 됨」을 붙였다. ADR 0007 이 글에서 값을 뽑는 파서를
기각했기 때문인데, 지금도 값을 뽑아 쓰지 않는다 — 옮겨 적은 칸을 보고, 글의 숫자는
출처에 있는지 없는지만 본다.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import ValidationError

from lol_balance import spend
from lol_balance.agent import qa
from lol_balance.agent.data import Corpus
from lol_balance.agent.judge import (
    MODEL,
    Context,
    Graph,
    Step,
    _steps,
    chat_model,
    explain_task,
    guards,
)
from lol_balance.agent.schema import Answer
from lol_balance.agent.tools import make_tools
from lol_balance.config import PROJECT_ROOT

# **해설의 규칙 묶음(`judge.RULES`)을 그대로 붙이지 않는다.** 거기에는 답 형식에 없는
# 칸(`tension` · `tension_quote`)이 나온다. 그것을 붙이고 「답하는 방법」을 중간에 뒀더니
# 로컬 모델(qwen3.5:9b)이 `Answer` 를 부르지 않고 글로만 답했다(3건 중 3건, 2026-10-01).
# 규칙을 짧게 다시 쓰고 **답하는 방법을 맨 끝에** 둔다.
SYSTEM = """당신은 리그 오브 레전드 밸런스 조정 판단을 돕는 해설자입니다.
앞에서 {champion} ({at} → {nxt})에 대한 해설을 했고, 사람이 그 해설에 대해 더 묻습니다.
최종 판단은 사람이 합니다.

## 도구 — {at} 뒤의 기록은 보이지 않습니다
- find_similar_cases  R1 비슷했던 과거 사례
- lookup_stats        R3 이 챔피언의 과거 지표
- search_patch_notes  R2 과거에 무엇을 얼마나 바꿨나
- effect_of           R3 조정 전후 — 승률·픽률·밴율·판수의 전후와 대조군을 뺀 효과.
                      **「조정(너프·버프) 뒤에 뭐가 바뀌었나」는 반드시 이것으로 확인합니다**

## 규칙
1. 도구가 준 것과 주어진 내용만 근거로 씁니다. 기억으로 말하지 않습니다.
2. **숫자는 도구가 준 것만 씁니다.** 직접 빼거나 더해 새 숫자를 만들지 않습니다.
3. 도구가 낸 주의는 답에 옮깁니다 — 「표본이 얇다」면 표본이 얇다고, 「출처가 다르다」면
   출처가 다르다고 적습니다.
4. 도구가 「경계 밖」이라고 하면 {at} 뒤라 **볼 수 없다**고 답합니다. 「조정되지 않았다」 ·
   「없었다」고 말하지 않습니다 — 못 본 것이지 없었던 것이 아닙니다.
5. 조정의 효과를 「먹혔다 · 안 먹혔다」로 단정하지 않습니다. 전후 변화와 판수를 같이 말합니다.
6. 「어떻게 조정해야 한다」는 쓰지 않습니다. 통계 모델의 수치는 순위를 매기는 점수이고
   확률이 아닙니다.

## 답하는 방법
필요한 도구를 부른 뒤, **마지막에는 반드시 Answer 도구를 호출해** 답합니다. 글로만 답하면
안 됩니다.
- answer   질문에 대한 답. 두세 문장으로 짧게 한국어로
- numbers  answer 에 쓴 숫자를 **도구가 준 표기 그대로** 하나씩 옮깁니다 (예: "+3.4%p", "49.4%")
코드가 numbers 를 도구 결과와 대조합니다.
"""


def checkpointer(path: Path) -> SqliteSaver:
    """교재 7장 Backend — 파일 하나에 대화 상태를 남긴다.

    Gradio 는 요청을 여러 스레드에서 받으므로 `check_same_thread=False` 로 연다.
    SqliteSaver 가 안에서 잠금을 건다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # 대화 상태에 구조화된 답(`Answer`)이 같이 저장된다. **형식을 등록해 둔다** — 안 하면
    # langgraph 가 「등록되지 않은 형식」이라고 경고하고 다음 판에서는 읽기를 막는다.
    serde = JsonPlusSerializer(
        allowed_msgpack_modules=[(Answer.__module__, Answer.__name__)]
    )
    return SqliteSaver(sqlite3.connect(str(path), check_same_thread=False), serde=serde)


def thread_id(session: str, at: str, champion: str) -> str:
    return f"{session}:{at}:{champion}"


def tools_for(corpus: Corpus, at: str) -> list[BaseTool]:
    """후속 질문이 쥐는 도구. 조정 전후 도구는 **여기서만** 쥐여 준다 — 평가 도구 묶음은
    그대로 둔다."""
    return make_tools(corpus, at, base_notes=True, effects=True, cautions=True)


def stamp(tools: Sequence[BaseTool]) -> str:
    """**모델이 받는 지시의 지문** — 프롬프트 · 답 형식 · 도구 설명 중 하나라도 바뀌면 달라진다.

    고정 질문의 답마다 같이 적는다(`qa_set.ask`). 답 형식 설명에 적어 둔 예시 하나가 답을
    바꿨다(고정 질문 q15, 2026-10-01) — 어떤 지시로 받은 답인지 모르면 견줄 수 없다.
    """
    told = [SYSTEM, json.dumps(Answer.model_json_schema(), sort_keys=True)]
    told += [f"{t.name}\n{t.description}" for t in tools]
    return hashlib.sha256("\n".join(told).encode("utf-8")).hexdigest()[:8]


def build_followup(
    corpus: Corpus,
    ctx: Context,
    saver: SqliteSaver,
    model: str = MODEL,
    **model_kwargs: Any,
) -> Graph:
    return create_agent(
        # 유료 모델이면 쓴 돈이 장부에 `qa` 로 적힌다 — 후속 질문에 든 돈을 따로 본다
        model=chat_model(model, why="qa", **model_kwargs),
        tools=tools_for(corpus, ctx.at),
        system_prompt=SYSTEM.format(champion=ctx.champion, at=ctx.at, nxt=ctx.nxt),
        # 답을 칸으로 받는다 — 숫자와 인용을 옮겨 적게 하고 코드가 대조한다
        response_format=ToolStrategy(Answer),
        checkpointer=saver,
        middleware=guards(),
    )


def _config(thread: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread}}


def started(agent: Graph, thread: str) -> bool:
    return bool(agent.get_state(_config(thread)).values.get("messages"))


@dataclass(frozen=True)
class Reply:
    """답 하나. `check` 는 코드가 도구 결과와 맞춰 본 결과다."""

    text: str
    answer: Answer | None  # 구조화된 답. 글로만 답했으면 None
    steps: list[Step]  # 이 질문에서 부른 도구
    check: qa.Check
    cautions: list[qa.Caution]  # 이 질문에서 도구가 낸 주의 — 화면이 답 아래에 붙인다
    usd: float = 0.0  # 이 답에 든 돈. 로컬 모델이면 0 이다. 장부에서 잰다
    # 이 질문에서 노트 도구가 돌려준 블록 — **코드가 붙인다**, 모델이 적지 않는다(ADR 0017)
    blocks: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Turn:
    question: str
    reply: Reply


def _final(chunk: Sequence[BaseMessage]) -> tuple[Answer | None, str]:
    """한 질문에 대한 마지막 답 — 구조화된 답이 있으면 그것, 없으면 글."""
    for m in reversed(chunk):
        if not isinstance(m, AIMessage):
            continue
        for call in m.tool_calls or []:
            if call["name"] == Answer.__name__:
                try:
                    answer = Answer.model_validate(call.get("args") or {})
                except ValidationError:
                    return None, str(m.content)
                return answer, answer.answer
        if m.content:
            return None, str(m.content)
    return None, ""


def turns(messages: Sequence[BaseMessage]) -> list[Turn]:
    """대화를 질문 단위로 끊고 **답마다 대조한다.**

    저장된 메시지만으로 계산한다 — 대조 결과를 따로 저장하지 않으므로 화면을 껐다
    켜도, 대조 규칙을 고친 뒤에도 같은 대화에서 다시 나온다.

    출처는 쌓인다 — 앞 질문에서 도구가 준 것은 뒤 질문에서도 출처다. **모델이 쓴
    글(해설 · 앞선 답)은 출처가 아니다.** 맨 앞 메시지는 코드가 만든 맥락이고 그다음은
    모델이 쓴 해설이다.
    """
    if not messages:
        return []
    context = str(messages[0].content)
    out: list[Turn] = []
    seen: list[Step] = []
    questions: list[str] = []
    chunk: list[BaseMessage] = []

    def close() -> None:
        steps = _steps(chunk)
        seen.extend(steps)
        answer, text = _final(chunk)
        found = qa.check(
            answer,
            seen,
            context=context,
            questions=questions,
            asked=steps,
            text=text,
        )
        raised = qa.cautions(steps)
        reply = Reply(text, answer, steps, found, raised, blocks=qa.note_blocks(steps))
        out.append(Turn(questions[-1], reply))

    for m in messages[2:]:
        if isinstance(m, HumanMessage):
            if questions:
                close()
            questions.append(str(m.content))
            chunk = []
        else:
            chunk.append(m)
    if questions:
        close()
    return out


def ask(
    agent: Graph,
    thread: str,
    question: str,
    corpus: Corpus,
    ctx: Context,
    explanation: str,
) -> Reply:
    """한 번 묻는다. 첫 질문이면 해설 맥락을 깔고, 아니면 새 질문만 보낸다.

    반환 — 답과 이번에 부른 도구 호출, 그리고 **코드가 대조한 결과.**
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
    # **이 답에 든 돈을 장부에서 잰다** — 묻기 전과 뒤의 차다. 토큰 수를 따로 세지
    # 않는다(장부가 한 곳이다). 상한에 닿았으면 `invoke` 가 묻기 전에 멈춘다.
    book = spend.ledger(PROJECT_ROOT)
    before = book.spent()
    state = agent.invoke({"messages": messages}, _config(thread))
    reply = turns(state["messages"])[-1].reply
    return replace(reply, usd=book.spent() - before)


def _shown(messages: Sequence[BaseMessage]) -> list[dict[str, str]]:
    """화면에 보일 대화 — 질문과 답, 그리고 **답마다 대조 결과 한 줄.**"""
    out = []
    for turn in turns(messages):
        out.append({"role": "user", "content": turn.question})
        text = turn.reply.text or "(답이 없다)"
        notes = [turn.reply.check.line()]
        # **근거로 본 노트 블록도 코드가 붙인다** — 도구가 실제로 돌려준 것만 나온다
        if turn.reply.blocks:
            notes.append("근거로 본 노트 블록 — " + " · ".join(turn.reply.blocks))
        # **주의는 코드가 붙인다** — 모델이 답에서 빼도 사람은 본다
        notes += [f"도구가 낸 주의 — {c.text}" for c in turn.reply.cautions]
        out.append(
            {
                "role": "assistant",
                "content": text + "\n\n" + "  \n".join(f"> {n}" for n in notes),
            }
        )
    return out


def history(agent: Graph, thread: str) -> list[dict[str, str]]:
    """화면에 보일 대화 — **깔아 둔 해설 맥락(앞의 두 메시지)은 뺀다.**"""
    return _shown(agent.get_state(_config(thread)).values.get("messages", []))


def saved_history(saver: SqliteSaver, thread: str) -> list[dict[str, str]]:
    """에이전트를 만들지 않고 **저장소에서 바로** 읽는다 — 챔피언을 다시 고를 때 쓴다."""
    found = saver.get_tuple(_config(thread))
    if found is None:
        return []
    values: dict[str, Any] = found.checkpoint.get("channel_values", {})
    return _shown(values.get("messages", []))
