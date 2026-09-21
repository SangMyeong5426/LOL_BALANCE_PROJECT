"""유료 사용 장부 — 세고, 상한에 닿으면 막는다."""

from __future__ import annotations

import json

import pytest

from lol_balance import spend


def test_local_models_cost_nothing_and_are_not_recorded(tmp_path):
    """`ollama:` 는 공짜다. 장부에 줄이 생기면 안 된다."""
    book = spend.ledger(tmp_path)
    assert spend.price("ollama:qwen3.5:9b") is None
    assert book.add("ollama:qwen3.5:9b", 9999, 9999, why="t") == 0.0
    assert book.spent() == 0.0
    book.check("ollama:qwen3.5:9b", limit=0.0)  # 막지 않는다


def test_cost_uses_the_published_rate(tmp_path):
    """제공자 문서 값 그대로 — 입력 $0.40 · 출력 $1.60 per 1M."""
    assert spend.cost("openai:gpt-4.1-mini", 1_000_000, 0) == pytest.approx(0.40)
    assert spend.cost("openai:gpt-4.1-mini", 0, 1_000_000) == pytest.approx(1.60)
    assert spend.cost("openai:gpt-4.1-mini", 2_813, 222) == pytest.approx(0.0014804)


def test_an_unknown_model_is_not_guessed(tmp_path):
    """단가를 모르면 0 으로 센다 — **지어내지 않는다.** 쓰기 전에 표에 넣는다."""
    assert spend.price("openai:gpt-9-unheard-of") is None
    assert spend.cost("openai:gpt-9-unheard-of", 10**6, 10**6) == 0.0


def test_the_ledger_adds_up_across_lines(tmp_path):
    book = spend.ledger(tmp_path)
    book.add("openai:gpt-4.1-mini", 1_000_000, 0, why="a")
    book.add("openai:gpt-4.1-mini", 0, 1_000_000, why="b")
    assert book.spent() == pytest.approx(2.00)
    lines = [json.loads(x) for x in book.path.read_text().splitlines()]
    assert [x["why"] for x in lines] == ["a", "b"]
    assert all(x["model"] == "openai:gpt-4.1-mini" for x in lines)


def test_check_blocks_once_the_cap_is_reached(tmp_path):
    book = spend.ledger(tmp_path)
    book.check("openai:gpt-4.1-mini", limit=1.0)  # 아직 0 이라 통과
    book.add("openai:gpt-4.1-mini", 1_000_000, 0, why="a")  # $0.40
    book.check("openai:gpt-4.1-mini", limit=1.0)
    book.add("openai:gpt-4.1-mini", 2_000_000, 0, why="b")  # 누적 $1.20
    with pytest.raises(spend.SpendCapReached, match=r"1\.00"):
        book.check("openai:gpt-4.1-mini", limit=1.0)


def test_the_cap_comes_from_the_environment(monkeypatch):
    assert spend.cap() == spend.DEFAULT_CAP
    monkeypatch.setenv("LOL_BALANCE_SPEND_CAP", "0.25")
    assert spend.cap() == 0.25
    monkeypatch.setenv("LOL_BALANCE_SPEND_CAP", "나쁜 값")
    with pytest.raises(ValueError, match="숫자"):
        spend.cap()


def test_usage_is_read_from_the_provider_not_estimated():
    """**토크나이저로 추정하지 않는다** — 제공자가 보고한 것만 센다."""

    class Reported:
        usage_metadata = {"input_tokens": 12, "output_tokens": 3}

    assert spend.usage_of(Reported()) == (12, 3)
    assert spend.usage_of(object()) == (0, 0)
