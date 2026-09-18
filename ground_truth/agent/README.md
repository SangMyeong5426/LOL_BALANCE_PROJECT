# 에이전트의 판단 — arm `B8` 의 기록

`B6` 의 판단이 [`ground_truth/rag/`](../rag/README.md)에 있듯, 에이전트가 낸
판단을 여기 둔다. **로컬 모델이 만들었고, 채점은 이 텍스트를 읽어서만 한다.**

```bash
./scripts/score-agent          # 여기 있는 기록 전부를 채점한다 — 모델을 안 부른다
```

`qwen3.5:9b` 로 229건을 다시 돌리면 한 시간이 넘는다. 기록을 두면 채점은 몇 초다.
근거는 [ADR 0009](../../docs/adr/0009-agent-framework-and-local-model.md), 결과는
[results](../../docs/results/README.md#같은-이웃으로-다시-쟀다--에이전트-b8-2026-09-18).

## 만드는 법

```bash
./scripts/run-agent --model ollama:qwen3.5:9b --no-think --strict --dev   # runs/ 에 쌓인다
cp runs/agent-dev-r1-ollama-qwen3.5-9b-nothink-no50.jsonl ground_truth/agent/dev-r1-ollama-qwen3.5-9b-nothink-no50.jsonl
```

**다 돈 것만 옮긴다.** `run-agent` 는 이어받으므로 `runs/` 의 기록은 중간일 수 있다.

## 파일

| 파일 | 표본 | 변형 | 모델 | 50 금지 | |
| --- | --- | --- | --- | :---: | --- |
| `test-v1-ollama-qwen3.5-2b-nothink` | 평가 229 | `v1` | 2b | | 평가 표본을 본 **1번째** |
| `test-r1-ollama-qwen3.5-2b-nothink` | 평가 229 | `r1` | 2b | | **2번째** |
| `test-r1-ollama-qwen3.5-9b-nothink-no50` | 평가 229 | `r1` | 9b | ✓ | **3번째** — 개발 표본에서 고른 설정 |
| `dev-r1-ollama-qwen3.5-2b-nothink` | 개발 114 | `r1` | 2b | | |
| `dev-r1-ollama-qwen3.5-2b-nothink-no50` | 개발 114 | `r1` | 2b | ✓ | |
| `dev-r1-ollama-qwen3.5-9b-nothink-no50` | 개발 114 | `r1` | 9b | ✓ | |

규칙 에이전트(`--model rule`)의 기록은 두지 않는다. LLM 이 아니라 코드라서 언제
돌려도 같은 것이 나온다.

**평가 표본을 본 횟수가 곧 이 표의 `test-` 줄 수다.** 볼 때마다 그 표본에 맞출
기회가 한 번 늘기 때문에 센다. 순서와 정한 이유는 결과 문서에 있다.

## 형식 — ADR 0007 의 심지에 실행 기록을 더했다

| 필드 | |
| --- | --- |
| `key` | 익명 키(`anon_key`). **챔피언 이름을 담지 않는다** |
| `condition` | `anon` |
| `nerf_prob` | 너프일 확률 0~100 정수. **답이 없으면 `null`** |
| `reason` | 판단 근거. 답이 없으면 그 까닭(오류 문구 등) |
| `as_of` | 도구의 경계 — `15_13` 은 평가, `14_13` 은 개발 |
| `abstain` | 참이면 **커버리지에서 빼고 센다** |
| `evidence[]` | 출처(`R1`·`R3`·`수치`)와 한 줄 |
| `variant` · `strict` · `model` | 설정 |
| `unverified_sources` | **부르지 않은 도구를 출처로 적은 것.** 코드가 잰다 |
| `tools` · `tool_args` | 실제로 부른 도구와 인자 |
| `model_calls` · `tokens_*` · `seconds` · `error` | 실행 기록. `tokens_in`·`tokens_out` 은 OpenAI 토크나이저로 센 것이다(키를 쓸 때의 비용 추정용) |

`nerf_prob` 이 `null` 일 수 있어 [`read_judgments`](../../src/lol_balance/ragjudge.py)
로는 읽지 않는다. 그래서 `B8` 은 아직 `run-report` 표에 없다 — [followups 24](../../docs/followups.md).

## 정답은 여기 없다

키를 챔피언·패치·정답에 잇는 대조표를 두지 않는다. 채점할 때 패널에서 다시
만든다 — `B6` 과 같다.

## 오염

익명 조건은 `B6` 과 같다([ADR 0006](../../docs/adr/0006-rag-generation-and-contamination-control.md)).
대상은 키로만 불리고, 이웃은 이름·패치 없이 수치와 결과만 보인다. 노트 검색(R2)은
스킬 이름이 정체를 드러내므로 뺐다.

**이름만 주는 조건(`named`)의 상한은 로컬 모델로 재지 않았다.** 결과가 다수결에
졌으므로, 모델이 기억을 꺼냈더라도 그것은 판단을 부풀리는 쪽이다 — 「못 이겼다」는
결론은 안 뒤집힌다.
