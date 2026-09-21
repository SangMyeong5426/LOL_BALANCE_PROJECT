"""동일한 근거로 모델 × 출력 형식을 비교한다 — ADR 0014, ② 방향.

조회는 코드가 한 번 한다. 기존 B8 에이전트 실험과 별도의 체인 실험이다.
모델 호출은 명시적으로 실행할 때만 일어난다. 키·원문 예외는 기록하지 않는다.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ConfigDict, Field

from lol_balance.agent.data import Corpus, available, load
from lol_balance.agent.evaluate import dev_cases, score, task, variant_tools
from lol_balance.agent.judge import chat_model
from lol_balance.config import PROJECT_ROOT, load_settings

ROOT = PROJECT_ROOT / "runs" / "direction-output-comparison"
MODELS = {"local": "ollama:qwen3.5:9b", "openai": "openai:gpt-4.1-mini"}
MAX_OUTPUT = 1000
BUDGET = 1.0
PRICE_IN, PRICE_OUT = 0.40, 1.60  # 2026-09-21 공식 모델 문서, USD / 1M
SYSTEM = """리그 오브 레전드 밸런스 분석 보조입니다.
대상은 다음 패치에 조정되는 것이 확정입니다. 너프인지 버프인지 판단하세요.
익명 대상의 정체를 추측하지 말고 제공된 지표와 R1 사례만 사용하세요.
근거가 부족하면 abstain=true로 답하세요. reason에 근거와 결론을 적으세요.
반드시 Judgment 도구를 정확히 한 번 호출해 답하세요.
"""
INSTRUCTIONS = {
    "numeric": "nerf_prob는 너프일 확률(0~100 정수)입니다. 0은 버프, 100은 너프입니다.",
    "direction": 'direction은 "nerf" 또는 "buff", confidence는 선택한 방향이 맞다는 확신(50~100 정수)입니다.',
}


class NumericAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    nerf_prob: int = Field(ge=0, le=100, description="너프일 확률. 0 버프, 100 너프")
    reason: str = Field(description="근거와 방향 결론")
    abstain: bool = Field(description="판단할 근거가 부족하면 true")


class DirectionAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    direction: Literal["nerf", "buff"]
    confidence: int = Field(ge=50, le=100, description="선택한 방향이 맞다는 확신")
    reason: str = Field(description="근거와 방향 결론")
    abstain: bool = Field(description="판단할 근거가 부족하면 true")


SCHEMAS: dict[str, type[NumericAnswer] | type[DirectionAnswer]] = {
    "numeric": NumericAnswer,
    "direction": DirectionAnswer,
}


def tool_schema(kind: str) -> dict[str, Any]:
    schema = convert_to_openai_tool(SCHEMAS[kind])
    schema["function"]["name"] = "Judgment"
    schema["function"]["description"] = "근거에 따른 방향 판단"
    return schema


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def save(path: Path, value: Any) -> None:
    """같은 디렉터리에서 원자 교체. 호출 예약은 서버에 보내기 전에 디스크에 쓴다."""
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


def read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def prepare(corpus: Corpus, limit: int) -> dict[str, Any]:
    rows = []
    for case in dev_cases(corpus)[:limit]:
        evidence = variant_tools(corpus, case, "r1")[0].invoke({"champion": case.key})
        rows.append(
            {
                "key": case.key,
                "truth": case.truth,
                "b5": case.b5,
                "input": task(case) + "\n\nR1 사례 검색 결과:\n" + str(evidence),
            }
        )
    if not rows:
        raise ValueError("개발 표본이 없습니다.")
    return {
        "version": 1,
        "split": "14_13",
        "limit": limit,
        "models": MODELS,
        "system": SYSTEM,
        "instructions": INSTRUCTIONS,
        "schemas": {k: tool_schema(k) for k in SCHEMAS},
        "max_output": MAX_OUTPUT,
        "rows": rows,
    }


def normalize(kind: str, args: dict[str, Any]) -> dict[str, Any]:
    answer = SCHEMAS[kind].model_validate(args)
    result = answer.model_dump()
    if isinstance(answer, DirectionAnswer):
        result["nerf_prob"] = (
            answer.confidence if answer.direction == "nerf" else 100 - answer.confidence
        )
    else:
        # 50은 방향을 나타내지 않는다. 임의로 너프로 바꾸지 않는다.
        prob = result["nerf_prob"]
        result["direction"] = "nerf" if prob > 50 else "buff" if prob < 50 else None
        result["confidence"] = max(prob, 100 - prob)
    return result


def reservation(text: str, kind: str) -> float:
    # BPE 토큰 수는 UTF-8 바이트 수 이하. 역할/도구 포맷 몫 4096을 추가한다.
    payload = SYSTEM + INSTRUCTIONS[kind] + text + json.dumps(tool_schema(kind))
    upper_input = len(payload.encode("utf-8")) + 4096
    return (upper_input * PRICE_IN + MAX_OUTPUT * PRICE_OUT) / 1e6


class Budget:
    """성공은 사용량 정산, 응답 없는 호출은 예약액 유지. 호출마다 먼저 저장한다."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, Any] = read(path, {})

    @property
    def spent(self) -> float:
        return float(sum(x["usd"] for x in self.entries.values()))

    def reserve(self, key: str, amount: float) -> None:
        if key in self.entries:
            raise RuntimeError(
                "이미 호출한 건입니다. 중단된 호출도 자동 재시도하지 않습니다."
            )
        if self.spent + amount > BUDGET:
            raise RuntimeError("누적 $1 비용 상한에 도달해 호출을 중단합니다.")
        self.entries[key] = {"usd": amount, "status": "reserved"}
        save(self.path, self.entries)

    def settle(self, key: str, usage: dict[str, Any]) -> None:
        if not {"input_tokens", "output_tokens"} <= usage.keys():
            return
        cost = (
            usage["input_tokens"] * PRICE_IN + usage["output_tokens"] * PRICE_OUT
        ) / 1e6
        self.entries[key] = {"usd": cost, "status": "settled", "usage": usage}
        save(self.path, self.entries)


def invoke(
    row: dict[str, Any], provider: str, kind: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """도구 호출 인자만 검증한다. 자연어를 규칙으로 파싱하지 않는다."""
    kwargs: dict[str, Any] = (
        {
            "reasoning": False,
            "num_predict": MAX_OUTPUT,
            "client_kwargs": {"timeout": 180},
        }
        if provider == "local"
        else {"max_tokens": MAX_OUTPUT, "max_retries": 0, "timeout": 90}
    )
    llm = chat_model(MODELS[provider], **kwargs)
    bound = llm.bind_tools([tool_schema(kind)], tool_choice="Judgment")
    message = bound.invoke(
        [SystemMessage(SYSTEM + INSTRUCTIONS[kind]), HumanMessage(row["input"])]
    )
    if not isinstance(message, AIMessage):
        raise ValueError("AIMessage가 아닙니다.")
    usage = dict(message.usage_metadata or {})
    result: dict[str, Any] = {
        "raw": message.model_dump(mode="json"),
        "abstain": True,
        "error": None,
        "direction": None,
        "nerf_prob": None,
        "confidence": None,
        "reason": "",
    }
    try:
        if message.response_metadata.get("finish_reason") == "length":
            raise ValueError("출력 상한으로 잘렸습니다.")
        if len(message.tool_calls) != 1 or message.tool_calls[0]["name"] != "Judgment":
            raise ValueError("Judgment 호출이 정확히 한 개가 아닙니다.")
        result.update(normalize(kind, message.tool_calls[0]["args"]))
    except (ValueError, TypeError):
        result["error"] = "InvalidStructuredOutput"
    return result, usage


def run_one(
    row: dict[str, Any], provider: str, kind: str, budget: Budget, call_id: str
) -> dict[str, Any]:
    if provider == "openai":
        budget.reserve(call_id, reservation(row["input"], kind))
    start = time.monotonic()
    try:
        result, usage = invoke(row, provider, kind)
        if provider == "openai":
            budget.settle(call_id, usage)
        result["usage"] = usage
    except Exception as exc:
        # SDK 예외는 키 일부나 요청 내용을 포함할 수 있어 종류만 남긴다.
        result = {
            "error": type(exc).__name__,
            "abstain": True,
            "reason": "",
            "direction": None,
            "nerf_prob": None,
            "confidence": None,
        }
    return {
        **result,
        "key": row["key"],
        "provider": provider,
        "format": kind,
        "model": MODELS[provider],
        "input_hash": digest(row["input"]),
        "seconds": round(time.monotonic() - start, 2),
    }


def answered(record: dict[str, Any]) -> bool:
    return (
        not record["abstain"]
        and not record["error"]
        and record["nerf_prob"] is not None
    )


def report(
    manifest: dict[str, Any], records: dict[str, Any], audit: dict[str, Any]
) -> str:
    rows = {r["key"]: r for r in manifest["rows"]}
    groups: dict[str, dict[str, Any]] = {
        f"{p}/{k}": {} for p in MODELS for k in SCHEMAS
    }
    for record in records.values():
        groups[f"{record['provider']}/{record['format']}"][record["key"]] = record
    out = [
        "② 방향 출력 비교 — 개발 표본, 고정 근거를 받은 체인 실험",
        "",
        "조건 | 완료 | 답변 | 기권 | 실패 | 방향 정확도 | AUC | 점수 종류",
        "--- | --- | --- | --- | --- | --- | --- | --- | ---",
    ]
    common = set(rows)
    for label, group in groups.items():
        valid = [r for r in group.values() if answered(r)]
        common &= {r["key"] for r in valid}
        if valid:
            accuracy = sum(
                r["direction"] == ("nerf" if rows[r["key"]]["truth"] else "buff")
                for r in valid
            ) / len(valid)
            auc = score(
                [rows[r["key"]]["truth"] for r in valid],
                [r["nerf_prob"] / 100 for r in valid],
            )["auc"]
            metrics = f"{accuracy:.1%} | {auc:.3f}"
        else:
            metrics = "— | —"
        errors = sum(bool(r["error"]) for r in group.values())
        abstain = sum(bool(r["abstain"]) and not r["error"] for r in group.values())
        out.append(
            f"{label} | {len(group)}/{len(rows)} | {len(valid)} | {abstain} | {errors} | {metrics} | {len({r['nerf_prob'] for r in valid})}"
        )
    out += [
        "",
        "위 표는 조건마다 답한 표본이 다를 수 있다. 숫자 출력 50은 방향 정답으로 세지 않는다.",
        f"네 조건 공통 답변: {len(common)}/{len(rows)}건",
    ]
    if common:
        keys = sorted(common)
        truth = [rows[k]["truth"] for k in keys]
        for label, group in groups.items():
            values = [group[k]["nerf_prob"] / 100 for k in keys]
            auc = score(truth, values)["auc"]
            correct = sum(
                group[k]["direction"] == ("nerf" if rows[k]["truth"] else "buff")
                for k in keys
            )
            out.append(
                f"- {label}: 방향 정확도 {correct / len(keys):.1%}, AUC {auc:.3f}"
            )
        base = score(truth, [rows[k]["b5"] for k in keys])
        out.append(f"- B5 다수결: 정확도 {base['accuracy']:.1%}, AUC {base['auc']:.3f}")
    out += ["", "설명과 출력 방향 대조 (사람이 검토한 건만):"]
    for label, group in groups.items():
        checked = []
        for r in group.values():
            review = audit.get(f"{label}/{r['key']}", {})
            side = review.get("reason_direction")
            if side in ("nerf", "buff") and answered(r):
                checked.append(side != r["direction"])
        out.append(
            f"- {label}: {sum(checked)}/{len(checked)} 불일치"
            if checked
            else f"- {label}: 미검토"
        )
    out += [
        "",
        "25건의 개발 진단으로 일반 성능이나 모델 크기의 인과 효과를 주장하지 않는다.",
    ]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepare", action="store_true", help="입력 저장·준비 확인만, 모델 호출 없음"
    )
    parser.add_argument(
        "--score", action="store_true", help="저장 기록 채점만, 모델 호출 없음"
    )
    parser.add_argument(
        "--local-only", action="store_true", help="키 없이 로컬 두 조건만 실행"
    )
    parser.add_argument("--limit", type=int, default=25, help="개발 표본 수, 기본 25")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 114:
        parser.error("--limit은 1~114입니다.")
    load_settings()
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / "experiment.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("비교 실험이 이미 실행 중입니다.")
            return 1
        return _main(args)


def _main(args: argparse.Namespace) -> int:
    manifest_path = ROOT / "inputs.json"
    if args.score:
        if not manifest_path.exists():
            print("저장된 입력이 없습니다. 먼저 --prepare를 실행하세요.")
            return 1
        manifest = read(manifest_path, {})
    else:
        if not available():
            print("평가용 데이터가 없습니다. README의 수집 절차가 필요합니다.")
            return 1
        manifest = prepare(load(), args.limit)
        old = read(manifest_path, None)
        if old is not None and old != manifest:
            print("저장된 입력·설정과 다릅니다. 기존 결과와 섞지 않으므로 중단합니다.")
            return 1
        if old is None:
            save(manifest_path, manifest)
    records = read(ROOT / "results.json", {})
    audit = read(ROOT / "review.json", {})
    budget = Budget(ROOT / "budget.json")
    if args.prepare:
        print(f"준비 완료: 개발 표본 {len(manifest['rows'])}건 × 네 조건")
        print(
            f"OPENAI_API_KEY: {'등록됨' if os.environ.get('OPENAI_API_KEY', '').strip() else '미등록 — .env에 입력하세요'}"
        )
        estimate = sum(
            reservation(r["input"], k) for r in manifest["rows"] for k in SCHEMAS
        )
        print(f"OpenAI 두 조건 보수적 예약 합계 ${estimate:.3f}; 누적 상한 $1")
        print(f"입력: {manifest_path}")
        return 0
    providers = ["local"] if args.local_only else list(MODELS)
    if (
        not args.score
        and "openai" in providers
        and not os.environ.get("OPENAI_API_KEY", "").strip()
    ):
        print(
            "OPENAI_API_KEY가 없습니다. .env에 입력하거나 --local-only를 사용하세요. 모델 호출 없음."
        )
        return 1
    failed = False
    if not args.score:
        # 건별로 네 조건을 번갈아 실행해 중단돼도 공통 표본이 최대한 남게 한다.
        for row in manifest["rows"]:
            for provider in providers:
                for kind in SCHEMAS:
                    key = f"{provider}/{kind}/{row['key']}"
                    if key in records:
                        continue
                    call_id = digest([digest(manifest), key])
                    if provider == "openai" and call_id in budget.entries:
                        print(
                            f"{key}: 이전 호출의 응답이 없어 예약액을 유지하며 건너뜁니다."
                        )
                        failed = True
                        continue
                    try:
                        result = run_one(row, provider, kind, budget, call_id)
                    except RuntimeError as exc:
                        print(str(exc))
                        return 1
                    records[key] = result
                    save(ROOT / "results.json", records)
                    print(
                        f"{len(records)}/{len(manifest['rows']) * len(providers) * 2} {key}: {result['error'] or result['direction'] or '기권'}",
                        flush=True,
                    )
                    if result["error"] and result["error"] != "InvalidStructuredOutput":
                        print(
                            "연결·인증 등의 오류로 중단합니다. 기록은 보존되며 재실행은 미실행 건부터 이어집니다."
                        )
                        failed = True
                        break
                if failed:
                    break
            if failed:
                break
    for key, r in records.items():
        audit.setdefault(
            key,
            {
                "reason": r["reason"],
                "reason_direction": None,
                "note": "설명만 읽고 nerf/buff/unclear 중 하나를 적는다",
            },
        )
    save(ROOT / "review.json", audit)
    text = report(manifest, records, audit)
    (ROOT / "report.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"OpenAI 누적 사용량·미확인 예약액: ${budget.spent:.4f} / $1")
    print(f"결과: {ROOT / 'report.md'}")
    return int(failed or any(r["error"] for r in records.values()))
