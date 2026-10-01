"""고정 질문 세트 — 학습 구간에서 고르고, 묻고, **모델 없이 다시 채점한다.**

질문이 의도한 경우(잴 수 있다 · 조정되지 않았다 · 직전 패치가 없다 · 경계 밖)를 실제로
만드는지를 도구에 물어 확인한다. 모델은 가짜고 도구와 대조는 진짜다.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

import lol_balance.agent.followup as fu
from lol_balance import spend
from lol_balance.agent import qa_set
from lol_balance.agent.data import Corpus
from lol_balance.agent.tools import make_tools
from lol_balance.panel import patch_index

AT = "15_13"
ROOT = Path(__file__).resolve().parents[1]
FIXED = ROOT / "ground_truth" / "qa" / "questions-dev-v1.jsonl"


class Scripted(FakeMessagesListChatModel):
    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Scripted:
        return self


def tool_call(name: str, args: dict[str, Any], n: int) -> dict[str, Any]:
    return {"name": name, "args": args, "id": f"call_{n}", "type": "tool_call"}


# ── 질문 고르기 ─────────────────────────────────────────────────────────


def test_questions_are_the_same_every_time(tiny_corpus: Corpus) -> None:
    """같은 씨앗이면 같은 질문이다 — 고정 세트를 다시 만들어도 같아야 한다."""
    first = qa_set.make_questions(tiny_corpus, AT, seed=7)
    assert first == qa_set.make_questions(tiny_corpus, AT, seed=7)
    assert len({q["id"] for q in first}) == len(first)
    assert {q["at"] for q in first} == {AT}


def test_each_kind_makes_the_case_it_names(tiny_corpus: Corpus) -> None:
    """**질문이 의도한 경우를 실제로 만든다.** 도구에 직접 물어 확인한다 — 「잴 수 있는
    조정」이라고 고른 것이 정말 재지고, 「경계 밖」이라고 고른 것이 정말 거절되는지."""
    questions = qa_set.make_questions(tiny_corpus, AT, seed=7)
    kinds = Counter(q["kind"] for q in questions)
    assert set(kinds) >= {"effect", "unadjusted", "gap", "stats", "cases", "beyond"}

    tools = {
        t.name: t
        for t in make_tools(
            tiny_corpus, AT, base_notes=True, effects=True, cautions=True
        )
    }
    for q in questions:
        ask = {"champion": q["champion"], "patch": q.get("patch", "")}
        if q["kind"] in ("effect", "mixed"):
            assert "%p" in tools["effect_of"].invoke(ask), q
        elif q["kind"] == "unadjusted":
            assert "조정되지 않았다" in tools["effect_of"].invoke(ask), q
        elif q["kind"] == "gap":
            assert "지표가 없다" in tools["effect_of"].invoke(ask), q
        elif q["kind"] == "beyond":
            assert patch_index(q["patch"]) > patch_index(AT)
            assert "경계 밖" in tools["effect_of"].invoke(ask), q
        elif q["kind"] == "notes":
            assert f"[{q['patch']}]" in tools["search_patch_notes"].invoke(ask), q
        assert q["champion"] in q["question"]


def test_no_question_stands_past_the_training_range(tiny_corpus: Corpus) -> None:
    """**학습 구간에서 고른다**(extension 5절 3항). 평가 구간에 서서 묻는 질문은 만들지
    않는다 — 프롬프트를 고칠 때 이 질문들을 보기 때문이다."""
    with pytest.raises(ValueError, match="학습 구간"):
        qa_set.make_questions(tiny_corpus, "15_14", seed=7)


# ── 묻고 적기 · 다시 채점하기 ───────────────────────────────────────────


def one_record(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    question = next(
        q
        for q in qa_set.make_questions(tiny_corpus, AT, seed=7)
        if q["kind"] == "effect"
    )
    after = tiny_corpus.row(question["champion"], question["patch"])
    assert after is not None
    win = f"{after.win_rate:.1%}"
    model = Scripted(
        responses=[
            AIMessage(
                "",
                tool_calls=[
                    tool_call(
                        "effect_of",
                        {"champion": question["champion"], "patch": question["patch"]},
                        1,
                    )
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
        ]
    )
    monkeypatch.setattr(fu, "chat_model", lambda *a, **k: model)
    return qa_set.ask(tiny_corpus, question, "가짜:모델", tmp_path)


def test_a_record_keeps_everything_needed_to_score_again(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """기록에는 답 · 도구 출력 · 코드가 만든 맥락이 다 있다. **자료(`data/`)도 모델도 없이
    다시 채점할 수 있다** — 대조 규칙을 고쳐도 모델을 다시 돌리지 않는다."""
    record = one_record(tiny_corpus, tmp_path, monkeypatch)

    assert record["check"]["mark"] == "✅"
    assert [s["tool"] for s in record["steps"]] == ["effect_of"]
    assert record["context"] and record["answer"]["numbers"]
    again = qa_set.rescore(json.loads(json.dumps(record, ensure_ascii=False)))
    assert again.mark == "✅" and again.numbers == record["check"]["numbers"]


def test_rescoring_catches_what_the_record_hides(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """채점은 기록에 적힌 표시를 믿지 않는다 — **답과 도구 출력에서 다시 계산한다.**"""
    record = one_record(tiny_corpus, tmp_path, monkeypatch)
    record["answer"]["answer"] = "조정 뒤 승률은 87.6% 다. 표본이 얇다."
    record["text"] = record["answer"]["answer"]

    again = qa_set.rescore(record)

    assert record["check"]["mark"] == "✅"  # 기록에는 통과로 적혀 있다
    assert again.mark == "⚠" and again.missing == ("87.6%",)


def test_the_summary_counts_what_the_checklist_asks(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """완료 기준은 「도구에 없는 숫자 0개, 없는 노트 블록 0개」다 — 그 둘을 센다."""
    good = one_record(tiny_corpus, tmp_path, monkeypatch)
    bad = json.loads(json.dumps(good, ensure_ascii=False))
    bad["id"] = "q99"
    bad["answer"]["answer"] = "승률은 87.6% 와 12.3% 다. 표본이 얇다."
    bad["answer"]["notes"] = [{"patch": "15_13", "section": "없는 절"}]
    bad["text"] = bad["answer"]["answer"]

    total = qa_set.summarize([good, bad])

    assert total["questions"] == 2
    assert total["marks"] == {"✅": 1, "⚪": 0, "⚠": 1}
    assert total["missing_numbers"] == 2 and total["absent_notes"] == 1
    assert total["answers_with_missing_numbers"] == 1
    assert total["answers_that_denied_the_unseen"] == 0


def test_the_summary_counts_answers_that_deny_what_cannot_be_seen() -> None:
    """「볼 수 없다」를 「없었다」로 바꾼 답을 따로 센다 — 숫자도 인용도 없어서 완료 기준의
    두 수에는 안 잡히지만 **틀린 답**이다."""
    beyond = {
        "id": "q01",
        "kind": "beyond",
        "question": "15_14 패치에서는 Riven 조정이 어떻게 됐어?",
        "model": "가짜:모델",
        "context": "",
        "steps": [
            {
                "tool": "effect_of",
                "args": {"champion": "Riven", "patch": "15_14"},
                "output": "15_14: 경계 밖 — 15_13 뒤의 자료는 보지 않는다",
            }
        ],
        "answer": {
            "answer": "15_14 에는 조정이 이루어지지 않았습니다. 볼 수 없기 때문입니다.",
            "numbers": [],
            "notes": [],
        },
        "text": "15_14 에는 조정이 이루어지지 않았습니다. 볼 수 없기 때문입니다.",
    }

    total = qa_set.summarize([beyond])

    assert total["marks"]["⚠"] == 1 and total["answers_that_denied_the_unseen"] == 1
    assert total["missing_numbers"] == 0 and total["absent_notes"] == 0
    shown = "\n".join(qa_set.report(total))
    assert "도구 결과에 없는 숫자 0개" in shown and "노트 블록 0개" in shown
    assert "없었다고 한 답 1건" in shown and "지시 지문 없음" in shown


def test_questions_already_answered_are_not_asked_again(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**같은 입력은 다시 부르지 않는다**(extension 4절 2항). 기록은 덧붙이기만 한다."""
    record = one_record(tiny_corpus, tmp_path, monkeypatch)
    out = tmp_path / "answers.jsonl"
    qa_set.append(out, record)
    questions = [q for q in qa_set.make_questions(tiny_corpus, AT, seed=7)]

    todo = qa_set.pending(questions, out, "가짜:모델")

    assert record["id"] not in {q["id"] for q in todo}
    assert len(todo) == len(questions) - 1
    assert len(qa_set.pending(questions, out, "다른:모델")) == len(questions)


# ── 지문 — 어떤 지시로 받은 답인가 ──────────────────────────────────────


def test_a_record_says_which_instructions_it_was_asked_under(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """기록에 **모델이 받은 지시의 지문**을 적는다 — 프롬프트 · 답 형식 · 도구 설명 중
    하나라도 바뀌면 달라진다. 답 형식의 예시 하나가 답을 바꿨다(고정 질문 q15)."""
    record = one_record(tiny_corpus, tmp_path, monkeypatch)
    now = fu.stamp(fu.tools_for(tiny_corpus, AT))

    assert record["prompt"] == now and len(now) == 8
    monkeypatch.setattr(fu, "SYSTEM", fu.SYSTEM + "\n7. 새 규칙")
    assert fu.stamp(fu.tools_for(tiny_corpus, AT)) != now


def test_answers_under_different_instructions_are_not_mixed(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**지시가 바뀌었으면 같은 파일에 이어 적지 않는다.** 섞이면 무엇을 잰 것인지 알 수
    없다 — 앞 절반은 옛 프롬프트, 뒤 절반은 새 프롬프트의 답이 된다. 한 건도 묻지 않고
    멈춘다. 새 이름(`run-qa --label`)에 받으면 처음부터 다시 묻는다."""
    asked = fake_asking(monkeypatch)
    book = spend.Ledger(tmp_path / "spend.jsonl")
    questions = qa_set.make_questions(tiny_corpus, AT, seed=7)[:3]
    out = tmp_path / "answers.jsonl"
    local = "ollama:qwen3.5:9b"
    list(qa_set.run(tiny_corpus, questions[:1], local, out, tmp_path, book, limit=1.0))
    assert len(asked) == 1

    monkeypatch.setattr(fu, "SYSTEM", fu.SYSTEM + "\n7. 새 규칙")
    with pytest.raises(qa_set.InstructionsChanged, match="label"):
        list(qa_set.run(tiny_corpus, questions, local, out, tmp_path, book, limit=1.0))
    assert len(asked) == 1 and len(qa_set.read(out)) == 1

    fresh = tmp_path / "answers-r2.jsonl"
    done = list(
        qa_set.run(tiny_corpus, questions, local, fresh, tmp_path, book, limit=1.0)
    )
    assert len(done) == 3


# ── 계약: 커밋된 고정 질문 ──────────────────────────────────────────────


def test_the_committed_set_is_fixed_and_in_the_training_range() -> None:
    """고정 질문은 20~30개고 전부 학습 구간(`15_13` 까지)에 서서 묻는다."""
    questions = [json.loads(x) for x in FIXED.read_text(encoding="utf-8").splitlines()]
    assert 20 <= len(questions) <= 30
    assert len({q["id"] for q in questions}) == len(questions)
    assert all(patch_index(q["at"]) <= patch_index("15_13") for q in questions)
    kinds = Counter(q["kind"] for q in questions)
    assert kinds["effect"] >= 6 and kinds["notes"] >= 3 and kinds["beyond"] >= 2


# ── 계약: 커밋된 답 — 문서의 수치는 여기서 나온다 ────────────────────────

SAVED = ROOT / "ground_truth" / "qa"
# 지금 실린 지시(프롬프트 · 답 형식 · 도구 설명)로 받은 답
SHIPPED = SAVED / "answers-dev-v1-ollama-qwen3.5-9b-r3.jsonl"
# (파일, ✅ · ⚪ · ⚠, 없는 숫자, 없는 노트 블록) — docs/agent.md 의 표와 같다
MEASURED = [
    ("answers-dev-v1-ollama-qwen3.5-9b.jsonl", (13, 9, 5), 0, 1),
    ("answers-dev-v1-ollama-qwen3.5-9b-r2.jsonl", (15, 8, 4), 0, 3),
    ("answers-dev-v1-ollama-qwen3.5-9b-r3.jsonl", (12, 9, 6), 0, 2),
]


@pytest.mark.parametrize(("name", "marks", "missing", "absent"), MEASURED)
def test_the_committed_answers_score_as_the_docs_say(
    name: str, marks: tuple[int, int, int], missing: int, absent: int
) -> None:
    """**문서에 적은 수치는 커밋된 답을 다시 대조한 값이다.** 대조 규칙을 고치면 여기가
    깨진다 — 그때 문서의 표(`docs/agent.md` · `ground_truth/qa/README.md`)를 같이 고친다.

    세 벌 모두 **도구에 없는 숫자는 0개**다. 없는 노트 블록은 어느 지시에서도 0 이 아니다
    — 완료 기준의 절반이 아직 안 찼다."""
    records = qa_set.read(SAVED / name)
    questions = [json.loads(x) for x in FIXED.read_text(encoding="utf-8").splitlines()]
    assert [r["id"] for r in records] == [q["id"] for q in questions]
    assert [r["question"] for r in records] == [q["question"] for q in questions]

    total = qa_set.summarize(records)

    assert tuple(total["marks"][m] for m in ("✅", "⚪", "⚠")) == marks
    assert total["missing_numbers"] == missing
    assert total["absent_notes"] == absent
    assert total["usd"] == 0.0  # 로컬 모델로만 쟀다


def test_the_shipped_instructions_are_the_measured_ones(tiny_corpus: Corpus) -> None:
    """**문서의 수치는 지금 실린 지시로 잰 것이다.** 프롬프트 · 답 형식 · 도구 설명을 고치면
    지문이 달라져 여기가 깨진다 — 고정 질문을 새 이름에 다시 받고(`run-qa --label`), 받은
    답을 커밋하고, 이 파일 이름과 문서의 수치를 고친다. 설명의 예시 하나에 답이 바뀌었다."""
    told = {r["prompt"] for r in qa_set.read(SHIPPED)}
    assert told == {fu.stamp(fu.tools_for(tiny_corpus, AT))}


# ── 돌리기 — 유료면 묻기 전에 상한을 본다 ───────────────────────────────

PAID = "openai:gpt-4.1-mini"


def fake_asking(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """모델을 부르는 대신 불린 질문만 적는다."""
    asked: list[str] = []

    def fake(corpus: Corpus, question: dict[str, str], model: str, *a: Any, **k: Any):
        asked.append(question["id"])
        return {
            **question,
            "model": model,
            "prompt": k.get("stamp", ""),
            "answer": None,
            "text": "",
            "steps": [],
        }

    monkeypatch.setattr(qa_set, "ask", fake)
    return asked


def test_over_the_experiment_cap_nothing_is_asked(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**상한은 묻기 전에 실제로 막는다**(extension 4절 1항). 크레딧은 반 전체가 나눠
    쓴다 — 이 실험의 누적 상한을 넘었으면 유료 질문은 한 건도 나가지 않는다."""
    asked = fake_asking(monkeypatch)
    book = spend.Ledger(tmp_path / "spend.jsonl")
    book.add(PAID, 3_000_000, 0, why="test")  # $1.20
    questions = qa_set.make_questions(tiny_corpus, AT, seed=7)
    out = tmp_path / "answers.jsonl"

    with pytest.raises(spend.SpendCapReached):
        list(qa_set.run(tiny_corpus, questions, PAID, out, tmp_path, book, limit=1.0))

    assert asked == [] and not out.exists()


def test_under_the_cap_every_pending_question_is_asked_once(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = fake_asking(monkeypatch)
    book = spend.Ledger(tmp_path / "spend.jsonl")
    questions = qa_set.make_questions(tiny_corpus, AT, seed=7)
    out = tmp_path / "answers.jsonl"

    first = list(
        qa_set.run(tiny_corpus, questions, PAID, out, tmp_path, book, limit=1.0)
    )
    again = list(
        qa_set.run(tiny_corpus, questions, PAID, out, tmp_path, book, limit=1.0)
    )

    assert len(first) == len(questions) == len(asked) and again == []
    assert len(qa_set.read(out)) == len(questions)


def test_a_local_model_is_never_stopped_by_the_cap(
    tiny_corpus: Corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """로컬 모델은 공짜다 — 장부가 상한을 넘었어도 돈다."""
    asked = fake_asking(monkeypatch)
    book = spend.Ledger(tmp_path / "spend.jsonl")
    book.add(PAID, 3_000_000, 0, why="test")
    questions = qa_set.make_questions(tiny_corpus, AT, seed=7)[:3]

    done = list(
        qa_set.run(
            tiny_corpus,
            questions,
            "ollama:qwen3.5:9b",
            tmp_path / "a.jsonl",
            tmp_path,
            book,
            limit=1.0,
        )
    )

    assert len(done) == 3 == len(asked)
