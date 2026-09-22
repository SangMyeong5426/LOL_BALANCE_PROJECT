"""한 챔피언을 판단한다 — 코드가 낼 것은 코드가, 판단은 에이전트가.

    경고 · 근거 · 베이스라인 점수   코드  (explain.reasons · arms.rank_candidates)
    조회 → 검색 → 판단             에이전트 (R1·R2·R3 + Judgment)

CLAUDE.md 의 「AI 를 어디에 쓰고 어디에 안 쓰는지 가른다」를 화면 단위로 지킨다.

## 에이전트에게 먼저 주는 것이 곧 arm 의 정의다

`B6` 은 이웃 25종을 **한 번에 받고** 판단했다. 에이전트까지 이웃 요약을 미리 받으면
B6 과 다를 것이 없다. 그래서 에이전트는 **기준 패치 지표와 패치 안 순위만** 받고,
사례·이력·노트는 도구로 스스로 찾는다. 그래야 「도구를 쥐여 준 효과」가 따로
잰다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import LLMResult
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_core.runnables import (
    Runnable,
    RunnableLambda,
    RunnableParallel,
    RunnablePassthrough,
)
from langgraph.graph.state import CompiledStateGraph

from lol_balance import spend
from lol_balance.agent.data import Corpus, lifetime_pro
from lol_balance.agent.schema import Explanation, Judgment
from lol_balance.agent.tools import make_tools
from lol_balance.arms import rank_candidates
from lol_balance.baseline import direction_rows
from lol_balance.config import PROJECT_ROOT, load_settings
from lol_balance.explain import outcome, patch_notes, reasons
from lol_balance.panel import PanelRow, name_after, next_patch, patch_index
from lol_balance.retrieval import CaseSearch

# 기본은 로컬 모델이다 — **API 키 없이 돈다.** `LOL_BALANCE_AGENT_MODEL` 로 바꾼다.
MODEL = load_settings().agent_model
RUNS = PROJECT_ROOT / "runs"

# 용어집의 기준선 — ① 은 조정률 15.0%, ② 는 다수 클래스 51.0%.
# 모델에게 사전확률을 안 주면 조정 확률을 크게 부풀린다.
BASE_ADJUST = 15

Graph = CompiledStateGraph[Any, Any, Any, Any]


@dataclass
class Context:
    """코드가 만드는 쪽. 모델을 안 부른다."""

    champion: str
    at: str
    nxt: str
    row: PanelRow
    facts: list[str]
    warnings: list[str]
    baseline: dict[str, tuple[float, int, int]]  # 과제 → (점수, 순위, 전체)
    answer: str | None  # 답이 있는 패치만


def build_context(corpus: Corpus, champion: str, at: str) -> Context:
    """`scripts/ask` 와 같은 입력으로 같은 근거·같은 점수를 낸다."""
    row = corpus.row(champion, at)
    if row is None:
        raise ValueError(f"{at} 에 {champion} 이 없다")
    nxt = next_patch(at) or name_after(at)
    here = tuple(r for r in corpus.rows if r.patch == at)

    notes = reasons(
        row,
        list(here),
        CaseSearch(direction_rows(corpus.rows), at).similar(row, k=25),
        corpus.rules,
        lifetime_pro(corpus.rows, champion),
    )
    warnings = [n.text for n in notes if n.warn]
    # 아이템이 크게 바뀐 패치면 승률 변화를 챔피언 조정으로만 읽으면 안 된다.
    warnings += [n.text for n in patch_notes(corpus.churn.get(nxt))]

    train = tuple(r for r in corpus.rows if r.patch_index < patch_index(at))
    spot = here.index(row)
    baseline = {}
    for label, want in (("조정", None), ("너프", "nerf"), ("버프", "buff")):
        score = rank_candidates(train, here, want=want, seed=corpus.seed)
        baseline[label] = (
            float(score[spot]),
            int((score > score[spot]).sum()) + 1,
            len(here),
        )

    return Context(
        champion=champion,
        at=at,
        nxt=nxt,
        row=row,
        facts=[n.text for n in notes if not n.warn],
        warnings=warnings,
        baseline=baseline,
        answer=outcome(row) if at in corpus.labeled else None,
    )


def candidates(
    corpus: Corpus, at: str, want: str | None = None, n: int = 10
) -> list[tuple[str, float]]:
    """베이스라인이 고른 후보. `predict` 가 하는 줄 세우기다."""
    here = tuple(r for r in corpus.rows if r.patch == at)
    train = tuple(r for r in corpus.rows if r.patch_index < patch_index(at))
    score = rank_candidates(train, here, want=want, seed=corpus.seed)
    order = sorted(range(len(here)), key=lambda i: -score[i])[:n]
    return [(here[i].champion, float(score[i])) for i in order]


# ── 에이전트 ───────────────────────────────────────────────────────────


def _rank(here: Sequence[PanelRow], row: PanelRow, key: str) -> str:
    value = getattr(row, key)
    if value is None:
        return "—"
    higher = sum(1 for r in here if (getattr(r, key) or 0) > value)
    return f"{value:.1%} ({len(here)}종 중 {higher + 1}위)"


SYSTEM = """당신은 리그 오브 레전드 밸런스 조정 판단을 돕는 분석 보조입니다.
최종 판단은 사람이 합니다. 당신은 후보를 좁히고 근거를 붙입니다.

## 과제
{at} 패치의 지표를 보고, 다음 패치({nxt})에서 {champion} 이
  ① 조정될 확률 (adjust_prob)
  ② 조정된다면 너프일 확률 (nerf_prob)
을 0~100 정수로 답합니다. **이유에 쓴 결론과 같은 쪽이어야 합니다.**

## 도구 — 전부 {at} 이전 기록만 보입니다
- lookup_stats        R3 수치 조회. 챔피언의 과거 지표와 각 패치 다음의 결과
- find_similar_cases  R1 사례 검색. among="all" 은 ①, among="adjusted" 는 ② 에 쓴다
- search_patch_notes  R2 노트 검색. 과거에 무엇을 얼마나 바꿨나

보통 R1 을 두 풀로 한 번씩, R3 을 한 번 부르고, 필요하면 R2 로 확인합니다.
도구를 여러 번 불러도 됩니다. 다른 챔피언과 비교해도 됩니다.

## 규칙
1. **기억으로 판단하지 않습니다.** 이 챔피언이 실제로 어떻게 됐는지 알더라도
   쓰지 마세요. 도구가 준 것과 아래 지표만 근거입니다.
2. 사전확률: 한 패치에서 조정되는 챔피언은 평균 {base}% 입니다. 근거 없이
   adjust_prob 를 높이지 마세요.
3. 근거가 부족하면 abstain 을 참으로 둡니다. 모르는 것을 50 으로 적지 않습니다.
4. 「어떻게 조정해야 한다」는 쓰지 않습니다. 관측된 패턴만 말합니다.
5. evidence 에 출처(R1·R2·R3·수치)를 붙이고, 도구가 준 숫자를 그대로 씁니다.
   **부르지 않은 도구를 출처로 적지 않습니다.**

## 답하는 방법
조사가 끝나면 **반드시 Judgment 도구를 호출해** 답합니다. 글로 답하지 않습니다.
"""


def task(corpus: Corpus, ctx: Context) -> str:
    """기준 패치 지표와 패치 안 순위만 준다. **라벨은 절대 안 넣는다.**"""
    row = ctx.row
    here = [r for r in corpus.rows if r.patch == ctx.at]
    lines = [
        f"{ctx.at} 패치 · {ctx.champion} ({row.main_role})",
        f"  승률   {_rank(here, row, 'win_rate')}",
        f"  픽률   {_rank(here, row, 'pick_rate')}",
        f"  밴율   {_rank(here, row, 'ban_rate')}",
        f"  판수   {row.matches:,}",
    ]
    if row.pro_pick_rate is not None or row.pro_ban_rate is not None:
        lines.append(
            f"  프로 픽률 {row.pro_pick_rate or 0:.1%} · 프로 밴율 {row.pro_ban_rate or 0:.1%}"
        )
    if row.d_win_rate is not None:
        lines.append(f"  직전 대비 승률 {row.d_win_rate:+.1%}")
    lines.append(f"\n{ctx.nxt} 에 조정될지 판단해 주세요.")
    return "\n".join(lines)


def guards() -> list[AgentMiddleware[Any, Any, Any]]:
    """교재 7장 Middleware — **한 건이 루프에 빠져도 거기서 멈춘다.**

    229건을 돌리다 한 건이 도구를 끝없이 부르면 비용이 거기서 샌다(강의자료
    「무한 루프 에이전트 → 비용 폭증」). 한도에 닿으면 조용히 끝내고, 구조화된
    답이 없으니 기권으로 센다. 보통은 모델 호출 3~4번이면 끝난다.
    """
    return [
        ModelCallLimitMiddleware(run_limit=8, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=12, exit_behavior="end"),
    ]


# 응답 한 번의 출력 상한. 정상 `Judgment` 호출이 약 220토큰이다.
MAX_OUTPUT = 600


class SpendGuard(BaseCallbackHandler):
    """유료 호출을 **부르기 전에 막고, 부른 뒤에 적는다.**

    호출 상한 미들웨어(ADR 0009)는 한 건 안의 루프를 막는다. 이것은 **건과 건
    사이로 새는 것**을 막는다 — 조금씩 계속 쓰는 쪽은 아무도 안 보고 있었다.
    로컬 모델에는 아무 일도 하지 않는다.
    """

    def __init__(self, model: str) -> None:
        self.model = model
        self.book = spend.ledger(PROJECT_ROOT)

    def on_chat_model_start(
        self, serialized: Any, messages: Any, **kwargs: Any
    ) -> None:
        self.book.check(self.model)

    def on_llm_start(self, serialized: Any, prompts: Any, **kwargs: Any) -> None:
        self.book.check(self.model)

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for batch in response.generations:
            for gen in batch:
                message = getattr(gen, "message", None)
                if message is None:
                    continue
                tin, tout = spend.usage_of(message)
                if tin or tout:
                    self.book.add(self.model, tin, tout, why="agent")


def chat_model(model: str, **kwargs: Any) -> BaseChatModel:
    """모델을 만든다. **응답 한 번의 길이에 상한을 건다.**

    호출 제한 미들웨어는 *횟수*를 막지 *한 번의 길이*를 막지 않는다. 로컬
    qwen3.5:2b 가 같은 문단을 2,400토큰 되풀이하다 `Judgment` 없이 끝난 적이
    있다(한 건 54초). 유료 모델이면 그 출력이 그대로 과금된다. 제공자마다
    인자 이름이 달라 여기서 한 번에 맞춘다.
    """
    cap = (
        {"num_predict": MAX_OUTPUT}
        if model.startswith("ollama:")
        else {"max_tokens": MAX_OUTPUT}
    )
    extra: dict[str, Any] = {}
    if spend.price(model) is not None:
        # **유료면 장부를 붙인다.** 상한에 닿으면 호출 전에 멈춘다.
        extra["callbacks"] = [SpendGuard(model), *kwargs.pop("callbacks", [])]
    llm: BaseChatModel = init_chat_model(
        model, temperature=0, **{**cap, **extra, **kwargs}
    )
    return llm


def build_agent(
    corpus: Corpus, ctx: Context, model: str = MODEL, **model_kwargs: Any
) -> Graph:
    """`model_kwargs` 는 모델 생성자로 간다 — 예: 로컬 모델의 `reasoning=False`."""
    return create_agent(
        model=chat_model(model, **model_kwargs),
        tools=make_tools(corpus, ctx.at),
        system_prompt=SYSTEM.format(
            at=ctx.at, nxt=ctx.nxt, champion=ctx.champion, base=BASE_ADJUST
        ),
        # **도구 방식으로 고정한다.** 제공자 네이티브 JSON 강제(ProviderStrategy)를
        # 로컬 모델(qwen3.5:2b)에 걸었더니 형식 제약이 도구 호출을 막아 **도구를
        # 하나도 안 부르고** 답했다 — 그러면서 출처에 R1·R3 를 적었다. 겉보기엔
        # 멀쩡한 답이라 더 위험하다. 도구 방식은 제공자와 무관하게 같게 돈다.
        response_format=ToolStrategy(Judgment),
        middleware=guards(),
    )


@dataclass
class Step:
    """도구 호출 하나. 화면의 「호출 기록」과 ADR 0007 의 출처 분석이 이것을 쓴다."""

    tool: str
    args: dict[str, Any]
    output: str = ""


@dataclass
class Run:
    steps: list[Step] = field(default_factory=list)
    judgment: Judgment | Explanation | None = None
    error: str | None = None


def _steps(messages: Sequence[BaseMessage]) -> list[Step]:
    steps: list[Step] = []
    by_id: dict[str, Step] = {}
    for msg in messages:
        if isinstance(msg, AIMessage):
            for call in msg.tool_calls or []:
                # 구조화 출력이 도구 호출로 오는 경우는 호출 기록에 넣지 않는다.
                if call["name"] in (Judgment.__name__, Explanation.__name__):
                    continue
                step = Step(call["name"], dict(call.get("args") or {}))
                steps.append(step)
                if call.get("id"):
                    by_id[str(call["id"])] = step
        elif isinstance(msg, ToolMessage) and msg.tool_call_id in by_id:
            by_id[msg.tool_call_id].output = str(msg.content)
    return steps


def stream(agent: Graph, prompt: str) -> Iterator[Run]:
    """교재 3장 Streaming — 도구를 부를 때마다 중간 상태를 내보낸다."""
    run = Run()
    try:
        for state in agent.stream(
            {"messages": [HumanMessage(content=prompt)]}, stream_mode="values"
        ):
            run.steps = _steps(state.get("messages", []))
            if state.get("structured_response") is not None:
                run.judgment = state["structured_response"]
            yield run
    except Exception as exc:  # 모델명 오류·Ollama 미기동이 화면에 보이게 한다
        run.error = f"{type(exc).__name__}: {exc}"
        yield run


# 도구 → 출처 이름. evidence.source 가 실제로 부른 도구와 맞는지 이것으로 본다.
TOOL_SOURCE = {
    "find_similar_cases": "R1",
    "search_patch_notes": "R2",
    "lookup_stats": "R3",
}


def unverified(run: Run) -> list[str]:
    """**부르지 않은 도구를 출처로 적은 것.** 코드가 잰다 — 모델에게 묻지 않는다.

    `수치` 는 처음에 받은 기준 패치 지표라 도구 호출이 필요 없다.
    """
    if run.judgment is None:
        return []
    called = {TOOL_SOURCE[s.tool] for s in run.steps if s.tool in TOOL_SOURCE}
    answer = run.judgment
    sources = (
        {p.source for p in [*answer.supporting, *answer.against]}
        if isinstance(answer, Explanation)
        else {e.source for e in answer.evidence}
    )
    # 도구가 아닌 출처 — 처음에 받은 지표·베이스라인은 호출이 필요 없다
    return sorted(set(sources) - called - {"수치", "베이스라인"})


def record(ctx: Context, run: Run, model: str = MODEL) -> dict[str, Any]:
    """ADR 0006·0007 저장 형식. 경고는 코드가 붙인다 — 모델이 쓴 칸이 아니다.

    `condition` 은 **named** 다. 이름을 주고 판단시켰으므로 오염 상한을
    못 잰다. 평가는 익명 조건으로 따로 돈다(`evaluate.py`).
    """
    j = run.judgment if isinstance(run.judgment, Judgment) else None
    key = hashlib.sha1(f"{ctx.at}:{ctx.champion}".encode()).hexdigest()[:6]
    return {
        "key": key,
        "condition": "named",
        "nerf_prob": j.nerf_prob if j else None,
        "adjust_prob": j.adjust_prob if j else None,
        "reason": j.reason if j else run.error,
        "as_of": ctx.at,
        "abstain": j.abstain if j else True,
        "evidence": [e.model_dump() for e in j.evidence] if j else [],
        "warnings": [{"kind": "rule", "text": w} for w in ctx.warnings],
        "tools": [s.tool for s in run.steps],
        "unverified_sources": unverified(run),
        "model": model,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def save(line: dict[str, Any], name: str = "judge-demo.jsonl") -> None:
    RUNS.mkdir(exist_ok=True)
    with (RUNS / name).open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


# ── 해설자 — 화면이 쓴다 ──────────────────────────────────────────────
#
# 독립 판단(위의 build_agent)은 평가용이다. 화면에서는 **베이스라인 결과와 경고를
# 받아** 왜 이 후보인지를 근거로 풀어 쓴다. 베이스라인을 보고 말하므로 성적은 못
# 잰다 — 성적은 scripts/run-agent 가 독립 판단으로 잰다.
#
# 조회를 누가 하느냐로 둘이다. **형식과 규칙은 같다.**
#
#     체인      코드가 R1·R2·R3 를 RunnableParallel 로 한꺼번에 조회 → 모델은 해석만
#               교재 4장 LCEL · 6장 검색→생성. 조회가 **반드시** 일어난다
#     에이전트  모델이 무엇을 조회할지 고른다. 교재 5장
#
# 에이전트만 두었다가 바꿨다 — 로컬 2B 에 베이스라인·근거·경고를 다 주니 **도구를
# 하나도 안 부르고** 받은 것을 되풀어 썼다(Azir·Senna 두 건 모두). 해설에 필요한
# 근거는 매번 같으므로(사례·추세·지난 변경) 무엇을 볼지 모델이 고를 일이 아니다.

RULES = """## 규칙
1. 도구가 준 것과 주어진 내용만 근거로 씁니다. 기억으로 말하지 않습니다.
2. **경고는 코드가 이미 냈습니다. 새 경고를 만들지 않습니다.** 경고와 통계 모델의 판단이
   부딪히는 지점이 있으면 tension 에 적고, 그 경고의 **원문을 그대로** tension_quote 에 옮깁니다.
3. 「어떻게 조정해야 한다」는 쓰지 않습니다. 관측된 패턴만 말합니다.
4. 근거마다 출처(R1·R2·R3·통계 모델 점수·수치)를 붙이고, **부르지 않은 도구를 출처로 적지 않습니다.**
5. 통계 모델도 틀릴 수 있습니다. 다른 신호를 숨기지 않습니다.
6. 통계 모델의 수치는 **순위를 매기는 점수**입니다. 확률이라고 부르지 않습니다."""

HEAD = """당신은 리그 오브 레전드 밸런스 조정 판단을 돕는 해설자입니다.
통계 모델이 {at} 패치 지표로 다음 패치({nxt})의 조정 후보를 골랐고,
사람이 그중 {champion} 을 골라 해설을 요청했습니다. 최종 판단은 사람이 합니다.

## 할 일
1. 통계 모델이 왜 이렇게 봤는지 뒷받침하는 근거를 찾습니다 (supporting)
2. 통계 모델과 **다른 신호**가 있으면 짚습니다 (against). 없으면 비웁니다
3. 통계 모델의 판단과 코드 경고가 부딪히는 지점을 적습니다 (tension). 없으면 비웁니다
4. 전체를 한두 문장으로 요약합니다 (summary)
5. 통계 모델이 본 조정 여부·방향에 대한 입장을 고릅니다 (stance)
6. 통계 모델 점수에서 너프·버프 중 더 높은 쪽을 옮겨 적습니다 (baseline_side)
"""

# 교재 3장 PromptTemplate — 변수 자리가 있는 틀로 둔다.
EXPLAIN = PromptTemplate.from_template(
    HEAD
    + """
## 도구 — 전부 {at} 이전 기록만 보입니다
- find_similar_cases  R1 비슷했던 과거 사례. among="adjusted" 는 방향, "all" 은 조정 여부
- lookup_stats        R3 이 챔피언의 과거 지표와 각 패치 다음의 결과
- search_patch_notes  R2 과거에 무엇을 얼마나 바꿨나

**해설하기 전에 반드시 도구로 확인합니다.** 적어도 find_similar_cases 를 부릅니다.

"""
    + RULES
    + """

## 답하는 방법
조사가 끝나면 **반드시 Explanation 도구를 호출해** 답합니다. 글로 답하지 않습니다.
"""
)


def explain_task(corpus: Corpus, ctx: Context) -> str:
    """해설자가 받는 것 — 지표 + **베이스라인 점수 · 경고.** 답은 없다.

    코드 근거 목록(`reasons`)은 넣지 않는다. 사례 요약은 R1 과, 순위는 지표와
    겹치고, 규칙 이름(`A-ban` …)은 모델이 읽을 수 없다. 화면 왼쪽에 그대로 있다.
    """
    lines = [
        task(corpus, ctx).rsplit("\n\n", 1)[0],
        "",
        "## 통계 모델 점수 (코드가 계산한 값 — 검증돼 있습니다)",
    ]
    lines.append(
        " · ".join(
            f"{k} {v[0]:.2f} ({v[2]}종 중 {v[1]}위)" for k, v in ctx.baseline.items()
        )
    )
    lines += ["", "## 코드가 낸 경고"] + (
        [f"- {w}" for w in ctx.warnings] or ["- 없음"]
    )
    lines += ["", f"{ctx.champion} 에 대한 통계 모델의 판단을 해설해 주세요."]
    return "\n".join(lines)


def build_explainer(
    corpus: Corpus, ctx: Context, model: str = MODEL, **model_kwargs: Any
) -> Graph:
    """에이전트 방식 — 무엇을 조회할지 모델이 고른다."""
    return create_agent(
        model=chat_model(model, **model_kwargs),
        tools=make_tools(corpus, ctx.at),
        system_prompt=EXPLAIN.format(at=ctx.at, nxt=ctx.nxt, champion=ctx.champion),
        response_format=ToolStrategy(Explanation),
        middleware=guards(),
    )


# ── 체인 방식 ─────────────────────────────────────────────────────────

EXPLAIN_CHAIN = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            HEAD
            + "\n조회는 이미 끝났습니다. 아래 조회 결과만 근거로 씁니다.\n\n"
            + RULES,
        ),
        ("human", "{task}\n\n## 조회 결과\n\n{evidence}"),
    ]
)

# 체인이 부르는 조회 — 해설에 필요한 근거는 매번 같다.
LOOKUPS: dict[str, tuple[str, dict[str, Any]]] = {
    "R1 조정된 사례": ("find_similar_cases", {"among": "adjusted", "k": 25}),
    "R1 전체 사례": ("find_similar_cases", {"among": "all", "k": 25}),
    "R3 과거 지표": ("lookup_stats", {}),
    "R2 지난 변경": ("search_patch_notes", {}),
}


def evidence(corpus: Corpus, ctx: Context) -> RunnableParallel[dict[str, Any]]:
    """교재 4장 RunnableParallel — 조회 넷을 **동시에** 한다. 경계는 도구가 지킨다."""
    tools = {t.name: t for t in make_tools(corpus, ctx.at)}

    def lookup(name: str, args: dict[str, Any]) -> RunnableLambda[Any, str]:
        return RunnableLambda(
            lambda _: str(tools[name].invoke({"champion": ctx.champion, **args}))
        )

    return RunnableParallel(
        {label: lookup(name, args) for label, (name, args) in LOOKUPS.items()}
    )


def build_explain_chain(
    corpus: Corpus,
    ctx: Context,
    model: str = MODEL,
    llm: Runnable[Any, Any] | None = None,
    **model_kwargs: Any,
) -> Runnable[Any, dict[str, Any]]:
    """체인 방식 — 조회(코드) | 프롬프트 | 모델 | 구조화 출력.  교재 4장 LCEL.

    `llm` 은 시험용이다 — Explanation 을 돌려주는 Runnable 을 끼운다.
    반환은 {"found": 조회 결과, "answer": Explanation} 이다. 화면이 조회 결과를
    호출 기록 칸에 보인다.
    """
    structured = llm or chat_model(model, **model_kwargs).with_structured_output(
        Explanation
    )
    head = {
        "at": ctx.at,
        "nxt": ctx.nxt,
        "champion": ctx.champion,
        "task": explain_task(corpus, ctx),
    }

    def prompt_vars(found: dict[str, str]) -> dict[str, str]:
        joined = "\n\n".join(f"### {k}\n{v}" for k, v in found.items())
        return {**head, "evidence": joined}

    answer = RunnableLambda(prompt_vars) | EXPLAIN_CHAIN | structured
    return evidence(corpus, ctx) | RunnableParallel(
        found=RunnablePassthrough(), answer=answer
    )


def invented_tension(e: Explanation, ctx: Context) -> bool:
    """경고가 없는데 긴장을 적었다 — **경고를 만들어 낸 것이다.** 코드가 잰다."""
    return bool(e.tension.strip()) and not ctx.warnings


def misread_baseline(e: Explanation, ctx: Context) -> str | None:
    """**베이스라인을 거꾸로 읽었나.** 모델이 옮겨 적은 쪽과 실제 점수를 대조한다.

    로컬 2B 가 Azir(버프 0.74 · 너프 0.19)를 「통계 모델이 너프로 보았지만」이라고
    해설한 적이 있다. 글에서 뽑지 않고 **옮겨 적게 한 칸**을 코드가 대조한다.
    """
    side = "너프" if ctx.baseline["너프"][0] > ctx.baseline["버프"][0] else "버프"
    return None if e.baseline_side == side else side


def misquoted_warning(e: Explanation, ctx: Context) -> bool:
    """**경고를 바꿔 옮겼나.** 인용한 원문이 코드가 낸 경고에 실제로 있는지 본다.

    같은 해설이 「버프 주의」를 「너프 주의」로 옮겼다. 방향이 뒤집힌 경고는 없는
    경고보다 나쁘다.

    **띄어쓰기는 안 본다.** 로컬 9b 가 경고를 글자 그대로 옮기면서 「5할」을
    「5 할」로, 「챔피언이다(」를 「챔피언이다 (」로 띄웠는데 이것을 「바꿔 옮겼다」로
    잡았다(2026-09-18). 방향을 뒤집으면 글자가 바뀌므로 공백을 지워도 잡힌다.
    """
    if not e.tension.strip():
        return False
    quote = _squeeze(e.tension_quote)
    return not quote or not any(quote in _squeeze(w) for w in ctx.warnings)


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text)
