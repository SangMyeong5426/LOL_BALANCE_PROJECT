"""에이전트(`B8`)를 `B5`·`B6` 과 **같은 표본 · 같은 경계 · 같은 채점기**로 잰다 — ② 방향.

    표본    B6 이 판단한 행 (ground_truth/rag 의 anon 판단이 있는 행)
    경계    분할점 15_13 에 고정 — B5 가 expanding=False 로 그렇게 돈다
    채점    baseline.roc_auc
    대상    익명 키로만 부른다 — ADR 0006. B6 과 같은 키(anon_key)

전부 이 패키지의 함수를 그대로 쓴다. **표본을 여기서 새로 정의하면 B5s·B6 과
나란히 놓을 수 없다.**

## 기권은 커버리지에서 빼고 센다

ADR 0007. 그리고 에이전트가 일부만 답했을 때는 **B5s·B6 도 같은 부분집합으로 잘라**
나란히 낸다 — `arms.py` 가 B6 에 하는 것과 같다. 다른 표본의 수치를 한 표에
놓지 않는다.

## 판단 기록은 텍스트로 남는다

`scripts/run-agent` 가 한 건씩 `runs/` 에 적고, 다 돈 것을 `ground_truth/agent/`
에 옮겨 커밋한다. `scripts/score-agent` 는 그 텍스트를 읽어 **채점만** 한다 —
모델을 부르지 않는다. B6 의 판단을 다루는 방식과 같다.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.callbacks import BaseCallbackHandler, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolCall,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from numpy.typing import ArrayLike

from lol_balance.agent.data import Corpus
from lol_balance.agent.judge import TOOL_SOURCE, Run, _steps, chat_model, guards
from lol_balance.agent.schema import Judgment, StrictJudgment
from lol_balance.agent.tools import make_tools
from lol_balance.arms import _retrieved, split
from lol_balance.baseline import direction_rows, encode, fit_encoder, roc_auc
from lol_balance.config import PROJECT_ROOT
from lol_balance.panel import PanelRow, patch_index
from lol_balance.ragjudge import anon_key, read_judgments

SPLIT = "15_13"  # run-report 의 기본 --split
JUDGMENTS = PROJECT_ROOT / "ground_truth" / "rag"
RECORDS = PROJECT_ROOT / "ground_truth" / "agent"


@dataclass(frozen=True)
class Case:
    row: PanelRow
    key: str  # anon_key — B6 과 같은 키
    truth: int  # 1 = 너프. encode 가 정한 것을 그대로 쓴다
    b5: float  # 같은 행의 B5 점수 (사례 25종 중 너프 비율)
    b6: float  # 같은 행의 B6 점수 (nerf_prob / 100). 개발 표본에는 없다(NaN)
    split: str = SPLIT  # 도구 경계. 개발 표본은 DEV_SPLIT


def cases(corpus: Corpus) -> list[Case]:
    # 예측행(마지막 패치)은 라벨이 없어 방향 풀에 원래 안 들어가지만, 패널과
    # 같은 입력을 쓰려고 명시적으로 뺀다.
    rows = tuple(r for r in corpus.rows if r.patch in corpus.labeled)
    pool = direction_rows(rows)
    train, test = split(pool, SPLIT)
    te = encode(test, fit_encoder(train, with_trend=False), target="direction")
    _, b5 = _retrieved(pool, test, SPLIT, k=25, expanding=False)
    judged = read_judgments(JUDGMENTS)

    out = []
    for i, row in enumerate(test):
        key = anon_key(row)
        j = judged.get((key, "anon"))
        if j is not None:
            out.append(Case(row, key, int(te.y[i]), float(b5[i]), j.nerf_prob / 100))
    return out


# **개발용 표본** — 평가 분할점(15_13)보다 1년 앞을 분할점으로 쓴다. 표본이 전부
# 평가 구간 이전이라, 여기서 무엇을 고치고 골라도 229건은 오염되지 않는다.
# 구조는 평가 표본과 같다 — 분할점 고정 경계 · 익명 키 · 같은 방식의 B5.
DEV_SPLIT = "14_13"


def dev_cases(corpus: Corpus, n: int | None = 120, seed: int = 20260824) -> list[Case]:
    """학습 구간 안에서만 만든 ② 방향 표본. **평가 구간 행은 아예 안 본다.**"""
    limit = patch_index(SPLIT)
    rows = tuple(
        r for r in corpus.rows if r.patch in corpus.labeled and r.patch_index < limit
    )
    pool = direction_rows(rows)
    train, dev = split(pool, DEV_SPLIT)
    te = encode(dev, fit_encoder(train, with_trend=False), target="direction")
    _, b5 = _retrieved(pool, dev, DEV_SPLIT, k=25, expanding=False)
    out = [
        Case(row, anon_key(row), int(te.y[i]), float(b5[i]), float("nan"), DEV_SPLIT)
        for i, row in enumerate(dev)
    ]
    if n is not None and n < len(out):
        pick = np.random.default_rng(seed).choice(len(out), n, replace=False)
        out = [out[i] for i in sorted(pick)]
    return out


def score(truth: ArrayLike, values: ArrayLike) -> dict[str, float]:
    t, v = np.asarray(truth), np.asarray(values, dtype=float)
    return {
        "auc": roc_auc(t, v),
        "accuracy": float(((v >= 0.5).astype(int) == t).mean()),
    }


# ── 에이전트 한 건 ─────────────────────────────────────────────────────

SYSTEM = """당신은 리그 오브 레전드 밸런스 조정 판단을 돕는 분석 보조입니다.

## 과제
대상 {key} 는 다음 패치에 **조정되는 것이 확정**된 챔피언입니다.
조정 여부가 아니라 **방향만** 판단합니다 — 너프인가 버프인가.
nerf_prob 에 너프일 확률을 0~100 정수로 답합니다. **이유에 쓴 결론과 같은 쪽이어야
합니다** — 버프가 유력하면 50 보다 작게 줍니다. adjust_prob 는 100 으로 둡니다.

## 도구
{tools}
대상은 champion="{key}" 로 부릅니다.

## 규칙
1. **기억으로 판단하지 않습니다.** 대상이 어느 챔피언인지 추측하지 마세요.
   도구가 준 것과 아래 지표만 근거입니다.
2. 근거가 부족하면 abstain 을 참으로 둡니다. 모르는 것을 50 으로 적지 않습니다.
3. evidence 에 출처(R1·R3·수치 중)를 붙입니다. **부르지 않은 도구를 출처로 적지 않습니다.**

## 답하는 방법
조사가 끝나면 **반드시 Judgment 도구를 호출해** 답합니다. 글로 답하지 않습니다.
"""


# 변형 — 같은 `B8` 안에서 도구 구성만 다르다(용어집 「변형」).
#
#     v1    첫 설정. R1 인자를 모델이 정하고, R3 경계는 분할점에 고정 — 낡은 기록을 준다
#     r1    R1 만, **B5 와 같은 증거**(조정된 사례 25건). 판단만 LLM — 순수한 판단 효과
#     r1r3  r1 + R3 (경계를 대상 패치 직전까지). B5·B6 이 못 본 정보라 따로 적는다
VARIANTS = ("v1", "r1", "r1r3")
TOOL_LINES = {
    "v1": (
        "- lookup_stats        R3 수치 조회. 대상의 과거 지표와 각 패치 다음의 결과 (경계 {as_of})\n"
        '- find_similar_cases  R1 사례 검색. among="adjusted" 는 조정된 사례만 — 너프였나 버프였나\n'
        '                      among="all" 은 전체 사례 (경계 {as_of})'
    ),
    "r1": "- find_similar_cases  R1 사례 검색. 조정된 비슷한 사례 25건과 그 방향 (경계 {as_of})",
    "r1r3": (
        "- find_similar_cases  R1 사례 검색. 조정된 비슷한 사례 25건과 그 방향 (경계 {as_of})\n"
        "- lookup_stats        R3 수치 조회. 대상의 최근 지표와 각 패치 다음의 결과 (대상 직전까지)"
    ),
}


def variant_tools(corpus: Corpus, case: Case, variant: str) -> list[BaseTool]:
    if variant == "v1":
        return make_tools(
            corpus, case.split, target=case.row, alias=case.key, notes=False
        )
    if variant == "r1":
        return make_tools(
            corpus,
            case.split,
            target=case.row,
            alias=case.key,
            notes=False,
            stats=False,
            fixed_cases=25,
        )
    if variant == "r1r3":
        return make_tools(
            corpus,
            case.split,
            target=case.row,
            alias=case.key,
            notes=False,
            stat_as_of=case.row.patch,
            fixed_cases=25,
        )
    raise ValueError(f"변형은 {VARIANTS} 중 하나다: {variant}")


def task(case: Case) -> str:
    """B6 이 받은 대상 블록과 **같은 칸만** 준다 (`runs/rag-anon-*.md`)."""
    r = case.row

    def delta(v: float | None) -> str:
        return "—" if v is None else f"{v:+.3f}"

    return (
        f"대상 {case.key}\n"
        f"역할: {r.main_role}  ·  판수: {r.matches:,}\n"
        f"승률 {r.win_rate:.3f}  픽률 {r.pick_rate:.3f}  밴율 {(r.ban_rate or 0):.3f}  "
        f"|승률−0.5| {abs(r.win_rate - 0.5):.3f}\n"
        f"직전 대비  승률 {delta(r.d_win_rate)}  픽률 {delta(r.d_pick_rate)}\n\n"
        "너프인가 버프인가 판단해 주세요."
    )


class RuleAgent(BaseChatModel):
    """**B5 를 LangChain 루프에 태운다.** LLM 이 아니다.

    R1 을 조정된 사례 25종으로 한 번 부르고, 너프 비율을 `nerf_prob` 로 낸다.
    B5 는 같은 풀·같은 경계·같은 k 로 같은 다수결을 한다. 그러니 **AUC 가 B5s 와
    같아야 한다.** 같으면 도구·경계·표본·채점이 전부 맞다는 증거고, 다르면 어딘가
    어긋난 것을 모델을 돌리기 전에 안다.

    도구 출력을 읽는 정규식은 **이 규칙 에이전트 전용이다.** 모델의 글에서 값을
    뽑는 파서가 아니다 (ADR 0007 이 기각한 것은 그쪽이다).
    """

    alias: str

    @property
    def _llm_type(self) -> str:
        return "rule-b5"

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> RuleAgent:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        seen = [m for m in messages if isinstance(m, ToolMessage)]
        call: ToolCall
        if not seen:
            call = {
                "name": "find_similar_cases",
                "args": {"champion": self.alias},
                "id": "r1",
                "type": "tool_call",
            }
        else:
            m = re.search(r"너프 (\d+) · 버프 (\d+)", str(seen[-1].content))
            nerf, buff = (int(m[1]), int(m[2])) if m else (0, 0)
            prob = round(100 * nerf / (nerf + buff)) if nerf + buff else 50
            call = {
                "name": "Judgment",
                "args": {
                    "adjust_prob": 100,
                    "nerf_prob": prob,
                    "reason": f"조정된 닮은 사례 25종 중 너프 {nerf} · 버프 {buff}",
                    "evidence": [
                        {"source": "R1", "text": f"너프 {nerf} · 버프 {buff}"}
                    ],
                },
                "id": "judgment",
                "type": "tool_call",
            }
        message = AIMessage("", tool_calls=[call])
        return ChatResult(generations=[ChatGeneration(message=message)])


def _encoder() -> Callable[[str], list[int]]:
    """OpenAI 토크나이저(`o200k_base` — gpt-4o · gpt-4o-mini).

    **처음 한 번은 내려받는다**(tiktoken 이 캐시한다). 테스트는 이것을 바꿔 끼워
    망 없이 돈다.
    """
    import tiktoken

    return tiktoken.get_encoding("o200k_base").encode


class Tokens(BaseCallbackHandler):
    """모델 호출마다 **실제로 보낸 메시지**의 토큰을 센다 — OpenAI 기준.

    호출마다 대화 전체가 다시 들어가므로 입력 토큰은 누적된다. 도구 정의도
    매번 같이 간다. 로컬 모델이 실제로 처리한 토큰(`usage_metadata`)은 토크나이저가
    달라 따로 적는다.

    **판단에는 안 쓴다.** 키를 쓸 때의 비용을 가늠하려고 센다.
    """

    def __init__(self, tool_tokens: int) -> None:
        self.encode = _encoder()
        self.tool_tokens = tool_tokens
        self.calls = self.input = self.output = 0
        self.local_in = self.local_out = 0

    def _count(self, m: BaseMessage) -> int:
        text = str(m.content or "")
        for tc in getattr(m, "tool_calls", None) or []:
            text += json.dumps(tc.get("args", {}), ensure_ascii=False) + tc.get(
                "name", ""
            )
        return len(self.encode(text)) + 4  # 메시지 하나당 역할 표시 몫

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        **kwargs: Any,
    ) -> None:
        for batch in messages:
            self.calls += 1
            self.input += sum(self._count(m) for m in batch) + self.tool_tokens

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for generations in response.generations:
            for g in generations:
                msg = getattr(g, "message", None)
                if msg is None:
                    continue
                self.output += self._count(msg)
                usage = getattr(msg, "usage_metadata", None) or {}
                self.local_in += usage.get("input_tokens", 0)
                self.local_out += usage.get("output_tokens", 0)


def _tool_tokens(tools: Sequence[BaseTool]) -> int:
    schemas = [convert_to_openai_tool(t) for t in tools] + [
        convert_to_openai_tool(Judgment)
    ]
    return len(_encoder()(json.dumps(schemas, ensure_ascii=False)))


def run_case(
    corpus: Corpus,
    case: Case,
    model: str,
    variant: str = "r1",
    strict: bool = False,
    **model_kwargs: Any,
) -> dict[str, Any]:
    """한 건. **익명 조건 · R2 없음.** 도구 구성은 변형이 정한다.

    규칙 에이전트는 고정형 R1 을 부르므로 `r1` 로만 돈다.
    """
    if model == "rule":
        variant = "r1"
    tools = variant_tools(corpus, case, variant)
    llm: BaseChatModel = (
        RuleAgent(alias=case.key)
        if model == "rule"
        else chat_model(model, **model_kwargs)
    )
    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM.format(
            key=case.key,
            as_of=case.split,
            tools=TOOL_LINES[variant].format(as_of=case.split),
        ),
        response_format=ToolStrategy(StrictJudgment if strict else Judgment),
        middleware=guards(),
    )
    counter = Tokens(_tool_tokens(tools))
    start = time.time()
    run = Run()
    try:
        state = agent.invoke(
            {"messages": [HumanMessage(task(case))]}, {"callbacks": [counter]}
        )
        run.steps = _steps(state.get("messages", []))
        run.judgment = state.get("structured_response")
    except Exception as exc:
        run.error = f"{type(exc).__name__}: {exc}"

    j = run.judgment if isinstance(run.judgment, Judgment) else None
    called = {TOOL_SOURCE[s.tool] for s in run.steps if s.tool in TOOL_SOURCE}
    return {
        "key": case.key,
        "condition": "anon",
        "variant": variant,
        "nerf_prob": j.nerf_prob if j else None,
        "reason": j.reason if j else (run.error or "구조화된 답 없음"),
        "as_of": case.split,
        "strict": strict,
        "abstain": bool(j.abstain) if j else True,
        "evidence": [e.model_dump() for e in j.evidence] if j else [],
        "unverified_sources": sorted({e.source for e in j.evidence} - called - {"수치"})
        if j
        else [],
        "tools": [s.tool for s in run.steps],
        "tool_args": [s.args for s in run.steps],
        "model": model,
        "model_calls": counter.calls,
        "tokens_in": counter.input,
        "tokens_out": counter.output,
        "local_tokens_in": counter.local_in,
        "local_tokens_out": counter.local_out,
        "seconds": round(time.time() - start, 2),
        "error": run.error,
    }


def _answered(
    all_cases: Sequence[Case], lines: Sequence[dict[str, Any]]
) -> list[tuple[Case, float]]:
    by_key = {c.key: c for c in all_cases}
    return [
        (by_key[x["key"]], x["nerf_prob"] / 100)
        for x in lines
        if x["key"] in by_key and not x["abstain"] and x["nerf_prob"] is not None
    ]


# ── 답이 자기 이유와 맞나 ─────────────────────────────────────────────

# **어순이 모델마다 다르다.** 로컬은 「버프 18 건」, gpt-4.1-mini 는 「18건이 버프」로
# 쓴다. 한쪽만 잡으면 그 모델만 0 건으로 나와 비교가 거짓이 된다.
_COUNTED = {
    "buff": (
        re.compile(r"버프\s*(\d+)\s*건"),
        re.compile(r"(\d+)\s*건[이은는]?\s*버프"),
    ),
    "nerf": (
        re.compile(r"너프\s*(\d+)\s*건"),
        re.compile(r"(\d+)\s*건[이은는]?\s*너프"),
    ),
}


def _count(reason: str, side: str) -> int | None:
    for pattern in _COUNTED[side]:
        found = pattern.search(reason)
        if found:
            return int(found.group(1))
    return None


def stated_share(reason: str) -> float | None:
    """이유 문장이 적은 **너프 비율**. 이웃 셈이 없으면 `None`.

    모델은 「유사 사례 25 건 중 버프 18 건, 너프 7 건」처럼 도구가 준 셈을 그대로
    옮겨 적는다. 그 비율은 `B5s` 와 같아야 하고, 실제로 같았다(122/122 · 상관 1.000).
    그래서 **이 값과 점수를 견주면 「읽기」와 「적기」를 가를 수 있다.**

    한쪽만 적은 문장(「25개 사례 중 18건이 버프」)은 나머지를 총합에서 뺀다.
    """
    text = reason or ""
    buff, nerf = _count(text, "buff"), _count(text, "nerf")
    if buff is not None and nerf is not None:
        return nerf / (buff + nerf) if buff + nerf else None
    known = buff if buff is not None else nerf
    if known is None:
        return None
    total = re.search(r"(\d+)\s*(?:개|건)\s*(?:사례|의 사례|중)", text)
    if total is None:
        return None
    whole = int(total.group(1))
    if known > whole or whole == 0:
        return None
    other = whole - known
    return (other if buff is not None else known) / whole


def contradictions(lines: Sequence[dict[str, Any]]) -> tuple[int, int]:
    """(이유와 반대쪽인 건, 방향이 적힌 건).

    **옛 형식이 여기서 샜다** — 이유는 버프라고 써 놓고 `nerf_prob` 에 98 을 적는
    일이 108건 중 27건이었다([ADR 0007](../../../docs/adr/0007-answer-schema.md)).
    방향을 말로 받은 뒤로는 구조적으로 0 이어야 한다.
    """
    flipped = counted = 0
    for x in lines:
        if x["abstain"] or x.get("nerf_prob") is None:
            continue
        said = stated_share(x.get("reason", ""))
        if said is None or abs(said - 0.5) < 0.04:
            continue
        counted += 1
        if (said > 0.5) != (x["nerf_prob"] / 100 > 0.5):
            flipped += 1
    return flipped, counted


def push(pairs: Sequence[tuple[Case, float]]) -> tuple[float, float]:
    """이웃이 한쪽을 가리킬 때 점수가 **가운데로 얼마나 밀리나** (버프 쪽, 너프 쪽).

    옛 형식은 버프 근거만 +0.14~+0.27 밀어 올리고 너프 근거는 그대로 통과시켰다.
    한쪽만 밀면 두 무리의 점수가 겹쳐 순위가 무너진다.
    """
    out = []
    for lo, hi in ((0.0, 0.2), (0.8, 1.01)):
        picked = [(c.b5, v) for c, v in pairs if lo <= c.b5 < hi]
        out.append(
            float(np.mean([v - b for b, v in picked])) if picked else float("nan")
        )
    return out[0], out[1]


def summary(
    all_cases: Sequence[Case],
    lines: Sequence[dict[str, Any]],
    label: str = "에이전트 · 익명",
    arm: str = "B8",
) -> list[tuple[str, str, int, dict[str, float]]]:
    """에이전트가 답한 행에서 에이전트 · B5s · B6 을 **같은 부분집합**으로 낸다."""
    answered = _answered(all_cases, lines)
    if not answered:
        return []
    truth = [c.truth for c, _ in answered]
    n = len(answered)
    rows = [
        (arm, label, n, score(truth, [p for _, p in answered])),
        (
            "B5s",
            "사례 검색만 — 같은 표본",
            n,
            score(truth, [c.b5 for c, _ in answered]),
        ),
        (
            "B6",
            "검색 위의 판단 — 같은 표본",
            n,
            score(truth, [c.b6 for c, _ in answered]),
        ),
    ]
    # 개발 표본에는 B6 판단이 없다(대화 중에 매긴 것은 평가 표본뿐이다)
    return [r for r in rows if not (r[0] == "B6" and np.isnan(answered[0][0].b6))]


def paired_bootstrap(
    truth: ArrayLike,
    a: ArrayLike,
    b: ArrayLike,
    draws: int = 20_000,
    seed: int = 20260824,
) -> dict[str, float]:
    """A 와 B 의 AUC 차이 — **같은 건을 같이 뽑는다.** 결과 문서의 방법이다.

    `docs/results/README.md` — B6 대 B5s 를 「짝지어 부트스트랩 2만 회」로 쟀다
    (차이 −0.020, 95% [−0.045, +0.004], 구간이 0 을 포함해 유의하지 않다).
    한 건이 한쪽에만 뽑히면 표본 차이가 arm 차이로 섞인다 — 그래서 짝짓는다.
    """
    t, x, y = np.asarray(truth), np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(draws):
        i = rng.integers(0, len(t), len(t))
        if t[i].min() == t[i].max():  # 한 클래스만 뽑히면 AUC 가 정의되지 않는다
            continue
        diffs.append(roc_auc(t[i], x[i]) - roc_auc(t[i], y[i]))
    d = np.array(diffs)
    return {
        "diff": roc_auc(t, x) - roc_auc(t, y),
        "low": float(np.percentile(d, 2.5)),
        "high": float(np.percentile(d, 97.5)),
        "p_higher": float((d > 0).mean()),
    }


# ── 보고 — run-agent 와 score-agent 가 같이 쓴다 ─────────────────────────

# 기본 유료 모델(`gpt-4.1-mini`)의 값, 100만 토큰당 달러. **2026-09-21 확인** —
# https://developers.openai.com/api/docs/pricing. 바뀌면 여기와 `spend.PRICES` 를 같이 고친다.
# **이 추정은 옛 기록용이다** — 실제로 부른 건은 `spend` 장부가 제공자 보고값으로 적는다.
PRICE = {"standard": (0.40, 1.60), "batch": (0.20, 0.80)}
# 같은 방식으로 더 크게 돌릴 때의 규모 — ① 대상 평가 구간 전체
TARGET_ROWS = 3433
# 짝지은 부트스트랩 횟수 — 결과 문서의 `B6` 대 `B5s` 와 같다
DRAWS = 20_000


# **칸이 생기기 전의 기록.** 평가 표본의 처음 두 벌은 `variant`·`strict` 가 생기기
# 전에 만들었다. 기록은 고치지 않는다 — ADR 0007 이 옛 판단 314건을 안 고친 것과
# 같다. 없으면 그때의 값이다: 변형은 `v1` 하나였고 50 금지는 없었다.
# 첫 벌에는 `arm: "A6"` 도 남아 있다 — 이름을 바로잡기 전의 것이고 안 읽는다.
LEGACY = {"variant": "v1", "strict": False}


def setting(record: dict[str, Any], key: str) -> Any:
    return record.get(key, LEGACY[key])


def read_records(path: Path) -> list[dict[str, Any]]:
    """한 줄에 한 건. 빈 줄은 건너뛴다."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return [json.loads(x) for x in text.splitlines() if x.strip()]


def report(all_cases: Sequence[Case], lines: Sequence[dict[str, Any]]) -> list[str]:
    """판단 기록 한 벌을 채점한다. **모델을 부르지 않는다.**

    변형 · 50 금지 · 모델 · 표본은 기록에서 읽는다 — 기록이 스스로를 설명한다.
    """
    if not lines:
        return ["기록이 없다."]
    first = lines[0]
    variant, strict = setting(first, "variant"), setting(first, "strict")
    model = first["model"]
    dev = first["as_of"] == DEV_SPLIT
    rule = model == "rule"

    out: list[str] = []
    where = "개발 표본(학습 구간)" if dev else "평가 표본(B6 과 같은 229건)"
    out.append(
        f"② 방향 · {model} · 변형 {variant}{' · 50 금지' if strict else ''} — "
        f"{where} {len(all_cases)}건 중 기록 {len(lines)}건"
    )
    answered_lines = [x for x in lines if not x["abstain"]]
    out.append(
        f"   커버리지 {len(answered_lines)}/{len(lines)}  "
        f"(기권 {len(lines) - len(answered_lines)} — 커버리지에서 빼고 센다)"
    )
    out.append("")
    out.append(f"{'arm':6}{'방법':28}{'N':>6}{'AUC':>8}{'정확도':>9}")
    out.append("-" * 57)
    # 규칙 에이전트는 LLM 이 아니다. 틀린 이름은 틀린 주장이 된다.
    arm, label = (
        ("(B5)", "사례 검색만 — 루프를 거쳐")
        if rule
        else ("B8", f"에이전트 · {variant} · 익명")
    )
    for name, text, n, s in summary(all_cases, lines, label, arm):
        out.append(f"{name:6}{text:28}{n:>6}{s['auc']:>8.3f}{s['accuracy']:>9.1%}")

    # **차이가 우연인지 가른다.** 229건이면 AUC 0.03 안팎은 흔들린다.
    pairs = _answered(all_cases, lines)
    if len(pairs) >= 20 and not rule:
        truth = [c.truth for c, _ in pairs]
        agent = [p for _, p in pairs]
        out.append("")
        out.append("짝지은 부트스트랩 2만 회 — B8 에서 뺀 값")
        others = [("B5s", [c.b5 for c, _ in pairs])]
        if not dev:  # 개발 표본에는 B6 이 없다
            others.append(("B6", [c.b6 for c, _ in pairs]))
        for name, other in others:
            r = paired_bootstrap(truth, agent, other, draws=DRAWS)
            verdict = "유의하지 않다" if r["low"] <= 0 <= r["high"] else "유의하다"
            out.append(
                f"  B8 − {name:4} {r['diff']:+.3f}  95% [{r['low']:+.3f}, {r['high']:+.3f}]"
                f"  B8 이 높을 확률 {r['p_higher']:.1%}  → {verdict}"
            )

    calls = sum(x["model_calls"] for x in lines) / len(lines)
    tin = sum(x["tokens_in"] for x in lines) / len(lines)
    tout = sum(x["tokens_out"] for x in lines) / len(lines)
    secs = sum(x["seconds"] for x in lines) / len(lines)
    fake = sum(1 for x in lines if x["unverified_sources"])
    out.append("")
    out.append(
        f"건당 평균  모델 호출 {calls:.1f}회 · 입력 {tin:,.0f} · 출력 {tout:,.0f} "
        f"토큰(OpenAI 기준) · {secs:.1f}초"
    )
    out.append(f"부르지 않은 도구를 출처로 적은 건 {fake}/{len(lines)}")
    flipped, counted = contradictions(lines)
    if counted:
        out.append(
            f"답이 자기 이유와 반대쪽인 건 {flipped}/{counted}"
            f" ({flipped / counted:.0%}) — 형식이 아니라 모델이 정한다(ADR 0007)"
        )
    buff_push, nerf_push = push(pairs)
    if pairs:
        out.append(
            f"이웃이 가리키는 쪽에서 점수가 가운데로 밀린 정도 —"
            f" 버프 쪽 {buff_push:+.2f} · 너프 쪽 {nerf_push:+.2f}"
        )

    # **토큰은 이 기록의 대화를 OpenAI 토크나이저로 센 것이다.** 모델이 다르면
    # 도구를 부르는 횟수와 답의 길이가 달라지므로 추정이다 — 규칙 에이전트
    # (호출 2회)가 바닥이다.
    out.append("")
    out.append("같은 대화를 gpt-4.1-mini 로 돌린다면 (토크나이저 추정)")
    for mode, (pin, pout) in PRICE.items():
        per = (tin * pin + tout * pout) / 1e6
        out.append(
            f"  {mode:9} 건당 ${per:.5f} · ② 방향 {len(all_cases)}건 "
            f"${per * len(all_cases):.2f} · ① 대상 {TARGET_ROWS:,}행 ${per * TARGET_ROWS:.2f}"
        )
    return out
