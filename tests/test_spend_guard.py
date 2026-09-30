"""상한이 **실제로** 호출을 막는지 — 가짜 모델로 잰다. 진짜 모델은 부르지 않는다.

LangChain 은 콜백 안의 예외를 기본으로 삼킨다(`raise_error=False`). 그래서 장부가
상한을 넘어도 검사만 예외를 던지고 호출은 그대로 나갔다(2026-09-28 확인). 여기서
그것을 막는지 본다. 장부는 전부 임시 폴더다 — `data/spend.jsonl` 을 건드리지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.prompts import ChatPromptTemplate

from lol_balance import spend
from lol_balance.agent import judge

PAID = "openai:gpt-4.1-mini"
CALLS: list[int] = []


class Counting(FakeListChatModel):
    """불린 횟수를 센다 — 상한이 막았으면 0 이어야 한다."""

    def _call(self, *args: Any, **kwargs: Any) -> str:
        CALLS.append(1)
        return super()._call(*args, **kwargs)


@pytest.fixture
def book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> spend.Ledger:
    ledger = spend.Ledger(tmp_path / "spend.jsonl")
    monkeypatch.setattr(spend, "ledger", lambda root: ledger)
    monkeypatch.setenv("LOL_BALANCE_SPEND_CAP", "1.0")
    CALLS.clear()
    return ledger


def over_the_cap(book: spend.Ledger) -> None:
    book.add(PAID, 5_000_000, 0, why="test")  # $2.00 — 상한 $1 을 넘는다


def reply_with_usage() -> LLMResult:
    message = AIMessage(
        "x",
        usage_metadata={
            "input_tokens": 1000,
            "output_tokens": 100,
            "total_tokens": 1100,
        },
    )
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def test_over_the_cap_a_direct_call_never_happens(book: spend.Ledger) -> None:
    over_the_cap(book)
    model = Counting(responses=["x"], callbacks=[judge.SpendGuard(PAID)])

    with pytest.raises(spend.SpendCapReached):
        model.invoke("hi")
    assert CALLS == []


def test_over_the_cap_a_chain_never_calls_the_model(book: spend.Ledger) -> None:
    """해설 체인(프롬프트 | 모델)도 같다."""
    over_the_cap(book)
    model = Counting(responses=["x"], callbacks=[judge.SpendGuard(PAID)])
    chain = ChatPromptTemplate.from_messages([("human", "{q}")]) | model

    with pytest.raises(spend.SpendCapReached):
        chain.invoke({"q": "hi"})
    assert CALLS == []


def test_under_the_cap_the_call_goes_through(book: spend.Ledger) -> None:
    model = Counting(responses=["x"], callbacks=[judge.SpendGuard(PAID)])

    assert model.invoke("hi").content == "x"
    assert CALLS == [1]


def test_a_paid_model_without_a_price_is_refused_before_the_call(
    book: spend.Ledger,
) -> None:
    model = Counting(
        responses=["x"], callbacks=[judge.SpendGuard("openai:gpt-9-unheard-of")]
    )

    with pytest.raises(spend.UnknownPrice):
        model.invoke("hi")
    assert CALLS == []


def test_usage_is_written_to_the_ledger(book: spend.Ledger) -> None:
    judge.SpendGuard(PAID).on_llm_end(reply_with_usage())

    assert book.spent() == pytest.approx(spend.cost(PAID, 1000, 100))


def test_a_ledger_write_failure_keeps_the_paid_answer(
    book: spend.Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**이미 돈을 낸 답은 잃지 않는다.** 장부 쓰기가 실패해도 예외로 끝내지 않는다."""

    class Broken:
        path = Path("장부.jsonl")  # Ledger 와 같은 모양

        def check(self, model: str, limit: float | None = None) -> None:
            return None

        def add(self, *args: Any, **kwargs: Any) -> float:
            raise OSError("디스크가 찼다")

    guard = judge.SpendGuard(PAID)
    monkeypatch.setattr(guard, "book", Broken())

    guard.on_llm_end(reply_with_usage())  # 예외 없이 끝난다


def test_every_paid_model_gets_the_guard(
    book: spend.Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """단가표에 없는 유료 모델도 장부를 붙인다 — 붙어야 거절할 수 있다. 로컬은 안 붙인다."""
    seen: dict[str, Any] = {}
    monkeypatch.setattr(judge, "init_chat_model", lambda model, **kw: seen.update(kw))

    judge.chat_model("openai:gpt-9-unheard-of")
    assert any(isinstance(c, judge.SpendGuard) for c in seen["callbacks"])

    seen.clear()
    judge.chat_model("ollama:qwen3.5:9b")
    assert not any(isinstance(c, judge.SpendGuard) for c in seen.get("callbacks", []))


def test_paid_models_retry_at_most_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """재시도는 한 번까지다 — 반 전체가 나눠 쓰는 크레딧이다."""
    seen: dict[str, Any] = {}
    monkeypatch.setattr(judge, "init_chat_model", lambda model, **kw: seen.update(kw))

    judge.chat_model(PAID)
    assert seen["max_retries"] == 1
