"""고정 질문 세트 — 학습 구간에서 고르고, 묻고, **모델 없이 다시 채점한다.**

[extension 2단계](../../../docs/extension.md)의 완료 기준이 이것으로 재진다.

    학습 구간에서 고른 고정 질문 20~30개에서 도구에 없는 숫자 0개, 없는 노트 블록 0개

    make_questions   학습 구간에서 질문을 고른다. 같은 씨앗이면 같은 질문이다
    ask              모델에게 묻고 답 · 도구 출력 · 코드가 만든 맥락을 적는다
    rescore          기록에서 **다시 대조한다** — 자료(`data/`)도 모델도 필요 없다
    summarize        완료 기준이 묻는 것을 센다

B6 · B8 의 판단을 글로 커밋해 두고 채점만 하는 것과 같은 방식이다. 답을 한 번 받아 두면
대조 규칙을 고쳐도 모델을 다시 돌리지 않는다.

**모델이 받는 지시(프롬프트 · 답 형식 · 도구 설명)를 고쳤으면 새로 묻는다.** 기록마다
그 지시의 지문(`prompt`)을 적고, 지문이 다른 답을 한 파일에 섞지 않는다 — 섞이면 무엇을
잰 것인지 알 수 없다.

## 질문의 종류

    effect       너프 · 버프된 조정 — 전후를 잴 수 있다. 표본이 얇은 것과 두꺼운 것을 섞는다
    mixed        방향이 갈린 조정 — 잴 수 있지만 「의도한 방향」은 말할 수 없다
    unadjusted   그 패치에 조정되지 않았다 — 도구가 거절한다
    gap          직전 패치 지표가 없다 — 도구가 거절한다
    notes        그 패치 노트에 이 챔피언의 절이 있다
    stats        최근 지표의 흐름
    cases        닮은 사례
    beyond       기준 패치 **뒤**를 묻는다 — 「볼 수 없다」고 답해야 한다

**거절해야 하는 질문을 일부러 넣는다.** 잴 수 있는 것만 물으면 「없는 것을 지어내는가」를
못 본다.
"""

from __future__ import annotations

import json
import random
import statistics
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from lol_balance import spend
from lol_balance.agent import followup, qa
from lol_balance.agent.data import Corpus
from lol_balance.agent.judge import Step, build_context, explain_task
from lol_balance.agent.schema import Answer, Cited
from lol_balance.explain import THIN_MATCHES
from lol_balance.panel import PanelRow, next_patch, patch_index, previous_patch

# 학습 구간의 끝 — `run-report` 의 기본 분할점. **이 뒤에 서서 묻는 질문은 만들지 않는다.**
# 프롬프트를 고칠 때 이 질문들을 보기 때문이다(extension 5절 3항).
SPLIT = "15_13"

COUNTS = {
    "effect": 8,
    "mixed": 2,
    "unadjusted": 2,
    "gap": 2,
    "notes": 5,
    "stats": 3,
    "cases": 2,
    "beyond": 3,
}
SAID = {"nerf": "너프", "buff": "버프"}
Question = dict[str, str]


class InstructionsChanged(RuntimeError):
    """그 파일의 답은 지금과 다른 지시로 받았다 — 이어 적으면 섞인다."""


def _pairs(
    corpus: Corpus, at: str
) -> Iterator[tuple[str, dict[int, PanelRow], dict[int, PanelRow]]]:
    """기준 패치까지에서, 직전 패치 지표가 **있는** 패치마다 (패치, 직전 행, 그 패치 행)."""
    cut = patch_index(at)
    by_patch: dict[str, dict[int, PanelRow]] = {}
    for r in corpus.rows:
        if r.patch in corpus.panel and r.patch_index <= cut:
            by_patch.setdefault(r.patch, {})[r.champion_id] = r
    for patch in sorted(by_patch, key=patch_index):
        prior = previous_patch(patch)
        if prior in by_patch:
            yield patch, by_patch[prior], by_patch[patch]


def make_questions(
    corpus: Corpus, at: str, seed: int, counts: dict[str, int] | None = None
) -> list[Question]:
    """`at` 에 서서 묻는 고정 질문. **같은 씨앗이면 같은 질문이다.**

    후보를 정렬한 뒤 뽑으므로 자료가 같으면 결과가 같다. 종류마다 후보가 모자라면
    있는 만큼만 낸다.
    """
    if patch_index(at) > patch_index(SPLIT):
        raise ValueError(
            f"{at} 은 학습 구간({SPLIT} 까지) 밖이다 — 고정 질문은 학습 구간에서만 고른다"
        )
    want = {**COUNTS, **(counts or {})}
    rng = random.Random(seed)
    here = {r.champion_id: r.champion for r in corpus.rows if r.patch == at}
    names = sorted(here.values())

    found: dict[str, list[tuple[str, str]]] = {k: [] for k in want}
    thin: set[tuple[str, str]] = set()
    for patch, before, after in _pairs(corpus, at):
        for cid, a in sorted(before.items()):
            if cid not in here or cid not in after:
                continue
            key = (here[cid], patch)
            if a.direction_next in SAID:
                found["effect"].append(key)
                if min(a.matches, after[cid].matches) < THIN_MATCHES:
                    thin.add(key)
            elif a.adjusted_next:
                found["mixed"].append(key)
            else:
                found["unadjusted"].append(key)

    cut = patch_index(at)
    present = {r.patch for r in corpus.rows if r.patch in corpus.panel}
    for patch in sorted((p for p in present if patch_index(p) <= cut), key=patch_index):
        if previous_patch(patch) not in present:
            found["gap"] += [(name, patch) for name in names]
    for patch, blocks in sorted(corpus.blocks.items()):
        try:
            inside = patch_index(patch) <= cut
        except KeyError:
            continue
        for b in blocks:
            # 스킨 절은 뺀다 — 노트 도구도 그렇게 거른다
            skin = b.champion.split()[0].lower() in b.section.lower()
            if inside and b.champion in names and not skin:
                found["notes"].append((b.champion, patch))
    beyond = next_patch(at)
    found["stats"] = [(name, "") for name in names]
    found["cases"] = [(name, "") for name in names]
    found["beyond"] = [(name, beyond) for name in names] if beyond else []

    def pick(kind: str) -> list[tuple[str, str]]:
        pool = sorted(set(found[kind]))
        if kind != "effect":
            return rng.sample(pool, min(want[kind], len(pool)))
        # 표본이 얇은 것과 두꺼운 것을 반씩 — 「표본이 얇다」를 답이 옮기는지 보려면 섞여야 한다
        half = want[kind] // 2
        lean = rng.sample(sorted(thin), min(half, len(thin)))
        rest = [p for p in pool if p not in thin]
        full = rng.sample(rest, min(want[kind] - len(lean), len(rest)))
        return lean + full

    # 영문 이름 뒤에 조사를 붙이지 않는다 — 「Aurelion Sol 가」처럼 어긋난다
    asked = {
        "effect": "{p} 패치에서 {c} {d} 뒤에 뭐가 바뀌었어?",
        "mixed": "{p} 패치에서 {c} 조정 뒤에 뭐가 바뀌었어?",
        "unadjusted": "{p} 패치에서 {c} 조정 뒤에 뭐가 바뀌었어?",
        "gap": "{p} 패치에서 {c} 조정 뒤에 뭐가 바뀌었어?",
        "notes": "{p} 패치 노트에서는 {c} 의 뭘 바꿨어?",
        "stats": "{c} 의 최근 승률 흐름은 어때?",
        "cases": "{c} 하고 지표가 비슷했던 챔피언들은 다음 패치에 어떻게 됐어?",
        "beyond": "{p} 패치에서는 {c} 조정이 어떻게 됐어?",
    }
    directions = {
        (r.champion, next_patch(r.patch)): SAID.get(r.direction_next or "", "조정")
        for r in corpus.rows
        if r.patch in corpus.panel
    }
    out: list[Question] = []
    for kind in want:
        for champion, patch in pick(kind):
            question: Question = {
                "id": f"q{len(out) + 1:02d}",
                "kind": kind,
                "champion": champion,
                "at": at,
                "question": asked[kind].format(
                    c=champion, p=patch, d=directions.get((champion, patch), "조정")
                ),
            }
            if patch:
                question["patch"] = patch
            out.append(question)
    return out


def ask(
    corpus: Corpus,
    question: Question,
    model: str,
    workdir: Path,
    *,
    stamp: str = "",
    **model_kwargs: Any,
) -> dict[str, Any]:
    """질문 하나를 **새 대화로** 묻고 기록 한 줄을 만든다.

    기록에 코드가 만든 맥락과 도구 출력을 다 넣는다 — 그래야 `rescore` 가 자료 없이
    다시 대조한다. `stamp` 는 모델이 받은 지시의 지문이다(안 주면 여기서 낸다).
    """
    ctx = build_context(corpus, question["champion"], question["at"])
    workdir.mkdir(parents=True, exist_ok=True)
    store = workdir / f"{question['id']}.sqlite"
    store.unlink(missing_ok=True)
    agent = followup.build_followup(
        corpus, ctx, followup.checkpointer(store), model, **model_kwargs
    )
    started = time.perf_counter()
    reply = followup.ask(
        agent, f"qa:{question['id']}", question["question"], corpus, ctx, ""
    )
    return {
        **question,
        "model": model,
        "prompt": stamp or followup.stamp(followup.tools_for(corpus, question["at"])),
        "context": explain_task(corpus, ctx),
        "steps": [
            {"tool": s.tool, "args": s.args, "output": s.output} for s in reply.steps
        ],
        "answer": reply.answer.model_dump() if reply.answer else None,
        "text": reply.text,
        # 코드가 붙인 노트 블록 — 모델이 적은 것이 아니다(ADR 0017)
        "blocks": list(reply.blocks),
        "check": {
            "mark": reply.check.mark,
            "problems": list(reply.check.problems),
            "numbers": reply.check.numbers,
            "notes": reply.check.notes,
            "why": reply.check.why,
        },
        "cautions": [{"kind": c.kind, "text": c.text} for c in reply.cautions],
        "usd": round(reply.usd, 6),
        "seconds": round(time.perf_counter() - started, 1),
    }


def rescore(record: dict[str, Any]) -> qa.Check:
    """기록에서 **다시 대조한다.** 기록에 적힌 표시는 믿지 않는다.

    **옛 형식의 기록은 그때의 규칙으로 채점한다.** 모델이 인용을 적던 때(ADR 0017 전) 받은
    답에는 `notes` 칸이 있다 — 그 인용이 도구가 준 블록인지를 계속 본다. 그래야 문서에 적은
    수치(없는 노트 블록 1 · 3 · 2 · 5 · 2)가 재현된다.
    """
    steps = [Step(s["tool"], dict(s["args"]), s["output"]) for s in record["steps"]]
    raw = record["answer"]
    answer = Answer.model_validate(raw) if raw else None
    cited = [Cited.model_validate(n) for n in (raw or {}).get("notes", [])]
    return qa.check(
        answer,
        steps,
        context=record["context"],
        questions=[record["question"]],
        asked=steps,
        text=record["text"],
        cited=cited,
    )


def summarize(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """완료 기준이 묻는 것을 센다 — 도구에 없는 숫자, 없는 노트 블록."""
    checks = [rescore(r) for r in records]
    marks = {m: sum(c.mark == m for c in checks) for m in ("✅", "⚪", "⚠")}
    by_kind: dict[str, dict[str, int]] = {}
    for r, c in zip(records, checks, strict=True):
        row = by_kind.setdefault(r["kind"], {"✅": 0, "⚪": 0, "⚠": 0})
        row[c.mark] += 1
    seconds = [float(r.get("seconds", 0.0)) for r in records]
    return {
        "questions": len(records),
        "marks": marks,
        "missing_numbers": sum(len(c.missing) for c in checks),
        "answers_with_missing_numbers": sum(bool(c.missing) for c in checks),
        "absent_notes": sum(len(c.absent) for c in checks),
        "answers_with_absent_notes": sum(bool(c.absent) for c in checks),
        "answers_that_misfiled_a_citation": sum(bool(c.misfiled) for c in checks),
        "answers_that_dropped_a_caution": sum(bool(c.dropped) for c in checks),
        "answers_that_denied_the_unseen": sum(bool(c.denied) for c in checks),
        "prompts": sorted({str(r.get("prompt", "")) for r in records}),
        "unstructured": sum(r["answer"] is None for r in records),
        # 노트 인용을 누가 적었나 — 답에 `notes` 칸이 있으면 모델이 적던 때의 기록이다
        "cited_by": (
            "모델" if any("notes" in (r["answer"] or {}) for r in records) else "코드"
        ),
        "by_kind": by_kind,
        "usd": round(sum(float(r.get("usd", 0.0)) for r in records), 6),
        "median_seconds": statistics.median(seconds) if seconds else 0.0,
    }


def report(total: dict[str, Any]) -> list[str]:
    """요약을 사람이 읽을 줄로 — `run-qa` 와 `score-qa` 가 같은 줄을 낸다."""
    marks = total["marks"]
    told = " · ".join(p or "없음" for p in total["prompts"])
    numbers = (
        f"도구 결과에 없는 숫자 {total['missing_numbers']}개"
        f"(답 {total['answers_with_missing_numbers']}건)"
    )
    plain = (
        f"Answer 를 부르지 않고 글로만 답한 것 {total['unstructured']}건"
        "(숫자는 글에서 확인한다)"
    )
    if total["cited_by"] == "모델":
        # 옛 형식 — 모델이 인용을 적던 때의 기록이다
        numbers += (
            f" · 도구가 준 적 없는 노트 블록 {total['absent_notes']}개"
            f"(답 {total['answers_with_absent_notes']}건)"
        )
        plain += (
            " · 인용 칸에 노트 블록이 아닌 도구 줄을 적은 것 "
            f"{total['answers_that_misfiled_a_citation']}건(걸지 않는다)"
        )
    else:
        numbers += (
            " · 노트 블록은 코드가 붙인다 — 모델이 적지 않아 없는 블록이 나올 수 없다"
        )
    return [
        f"질문 {total['questions']}개 · ✅ {marks['✅']} · ⚪ {marks['⚪']} · ⚠ {marks['⚠']}",
        numbers,
        f"주의를 뺀 답 {total['answers_that_dropped_a_caution']}건 · "
        f"볼 수 없는 것을 없었다고 한 답 {total['answers_that_denied_the_unseen']}건",
        plain,
        f"비용 ${total['usd']:.4f} · 한 건 중앙값 {total['median_seconds']:.1f}초 · "
        f"지시 지문 {told}",
    ]


def read(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def append(path: Path, record: dict[str, Any]) -> None:
    """**덧붙이기만 한다.** 받은 답은 지우지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def run(
    corpus: Corpus,
    questions: Sequence[Question],
    model: str,
    out: Path,
    workdir: Path,
    book: spend.Ledger,
    *,
    limit: float,
    count: int | None = None,
    **model_kwargs: Any,
) -> Iterator[dict[str, Any]]:
    """아직 안 물은 질문을 묻고 **받는 대로 덧붙인다.** 받은 기록을 하나씩 내준다.

    유료 모델이면 **묻기 전에** 장부를 본다 — 누적이 `limit` 에 닿았으면
    `SpendCapReached` 로 멈춘다. 그때까지 받은 답은 이미 파일에 있다. 로컬 모델은 공짜라
    막지 않는다. `count` 를 주면 그만큼만 묻는다.

    **지시가 바뀌었으면 한 건도 묻지 않는다**(`InstructionsChanged`). 그 파일에 지금과
    다른 지문으로 받은 답이 있으면 이어 적지 않는다 — 새 이름에 받는다.
    """
    if not questions:
        return
    told = followup.stamp(followup.tools_for(corpus, questions[0]["at"]))
    others = {str(r.get("prompt", "")) for r in read(out) if r.get("model") == model}
    if others - {told}:
        was = ", ".join(sorted(o or "지문 없음" for o in others - {told}))
        raise InstructionsChanged(
            f"{out.name} 의 답은 다른 지시({was})로 받았다 — 지금은 {told} 다. "
            "섞지 않는다. `--label` 로 새 이름에 받는다"
        )
    for question in pending(questions, out, model)[:count]:
        if not spend.is_local(model):
            book.check(model, limit=limit)
        record = ask(corpus, question, model, workdir, stamp=told, **model_kwargs)
        append(out, record)
        yield record


def pending(questions: Sequence[Question], path: Path, model: str) -> list[Question]:
    """아직 이 모델로 안 물은 질문. **같은 입력은 다시 부르지 않는다.**"""
    done = {r["id"] for r in read(path) if r.get("model") == model}
    return [q for q in questions if q["id"] not in done]
