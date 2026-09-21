"""출력 비교의 비용·재개·점수 의미를 모델 호출 없이 확인한다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from lol_balance.agent import output_comparison as oc


def test_direction_at_fifty_is_not_changed_to_nerf() -> None:
    answer = oc.normalize(
        "direction",
        {"direction": "buff", "confidence": 50, "reason": "r", "abstain": False},
    )
    assert answer["nerf_prob"] == 50
    assert answer["direction"] == "buff"
    numeric = oc.normalize(
        "numeric", {"nerf_prob": 50, "reason": "r", "abstain": False}
    )
    assert numeric["direction"] is None
    with pytest.raises(ValidationError):
        oc.normalize(
            "direction",
            {"direction": "buff", "confidence": 49, "reason": "r", "abstain": False},
        )


def test_budget_persists_before_call_and_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "budget.json"
    budget = oc.Budget(path)
    budget.reserve("first", 0.6)
    resumed = oc.Budget(path)
    with pytest.raises(RuntimeError, match="상한"):
        resumed.reserve("second", 0.5)
    with pytest.raises(RuntimeError, match="이미"):
        resumed.reserve("first", 0.1)
    resumed.settle("first", {})
    assert resumed.spent == 0.6
    resumed.settle("first", {"input_tokens": 1000, "output_tokens": 100})
    assert oc.Budget(path).spent == pytest.approx(0.00056)


def test_exception_keeps_reservation_and_does_not_write_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: Any) -> Any:
        raise ConnectionError("secret-key-do-not-log")

    monkeypatch.setattr(oc, "invoke", fail)
    budget = oc.Budget(tmp_path / "budget.json")
    record = oc.run_one(
        {"key": "k", "input": "facts"}, "openai", "numeric", budget, "id"
    )
    assert record["error"] == "ConnectionError"
    assert "secret-key" not in str(record)
    assert budget.spent > 0


def test_provider_options_and_malformed_output_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    class Fake:
        def bind_tools(self, tools: Any, **kwargs: Any) -> Fake:
            assert tools[0]["function"]["name"] == "Judgment"
            return self

        def invoke(self, messages: Any) -> AIMessage:
            assert "truth" not in str(messages)
            return AIMessage(
                "not structured",
                usage_metadata={
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                },
            )

    def model(name: str, **kwargs: Any) -> Fake:
        captured.append(kwargs)
        return Fake()

    monkeypatch.setattr(oc, "chat_model", model)
    result, usage = oc.invoke({"input": "facts", "truth": 1}, "openai", "direction")
    assert result["error"] == "InvalidStructuredOutput"
    assert usage["input_tokens"] == 10
    assert captured[0]["max_retries"] == 0
    assert "reasoning" not in captured[0]
    oc.invoke({"input": "facts"}, "local", "numeric")
    assert captured[1]["reasoning"] is False


def test_common_sample_and_manual_review_are_separate() -> None:
    manifest = {
        "rows": [
            {"key": "a", "truth": 0, "b5": 0.1},
            {"key": "b", "truth": 1, "b5": 0.9},
        ]
    }
    records = {}
    for p in oc.MODELS:
        for k in oc.SCHEMAS:
            for key, side, prob in [("a", "buff", 20), ("b", "nerf", 80)]:
                records[f"{p}/{k}/{key}"] = {
                    "provider": p,
                    "format": k,
                    "key": key,
                    "direction": side,
                    "nerf_prob": prob,
                    "abstain": False,
                    "error": None,
                }
    records["openai/direction/b"]["abstain"] = True
    text = oc.report(
        manifest, records, {"local/numeric/a": {"reason_direction": "nerf"}}
    )
    assert "네 조건 공통 답변: 1/2건" in text
    assert "local/numeric: 1/1 불일치" in text
    assert "openai/numeric: 미검토" in text


def test_main_missing_key_and_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {"rows": [{"key": "a", "input": "facts", "truth": 0, "b5": 0.1}]}
    monkeypatch.setattr(oc, "ROOT", tmp_path)
    monkeypatch.setattr(oc, "load_settings", lambda: None)
    monkeypatch.setattr(oc, "available", lambda: True)
    monkeypatch.setattr(oc, "load", lambda: None)
    monkeypatch.setattr(oc, "prepare", lambda *args: manifest)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    calls = []

    def fake(row: Any, provider: str, kind: str, *args: Any) -> dict[str, Any]:
        calls.append((provider, kind))
        return {
            "key": row["key"],
            "provider": provider,
            "format": kind,
            "direction": "buff",
            "nerf_prob": 20,
            "reason": "buff",
            "abstain": False,
            "error": None,
        }

    monkeypatch.setattr(oc, "run_one", fake)
    assert oc.main([]) == 1
    assert not calls
    assert oc.main(["--local-only"]) == 0
    assert len(calls) == 2
    assert oc.main(["--local-only"]) == 0
    assert len(calls) == 2
    monkeypatch.setenv("OPENAI_API_KEY", "fake-not-sent")
    assert oc.main([]) == 0
    assert len(calls) == 4
    assert oc.main(["--score"]) == 0
    assert len(calls) == 4
    monkeypatch.setattr(oc, "prepare", lambda *args: {"rows": []})
    assert oc.main([]) == 1
    assert len(calls) == 4
