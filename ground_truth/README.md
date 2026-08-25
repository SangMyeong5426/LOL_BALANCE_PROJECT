# 정답지 — 무엇이 어떻게 만들어졌나

**여기 있는 것은 「정리한 결과」이고 다시 만들면 같다는 보장이 없다.** 그래서 커밋한다([ADR-0004](../docs/adr/0004-processed-data-storage-format.md)). 어떻게 만들었는지가 값 자체만큼 중요하다.

## `directions/` — 조정 방향 라벨

챔피언 한 종이 한 패치에서 **너프됐는지 버프됐는지.**

| | |
| --- | --- |
| 규모 | **242종 · 11패치** |
| 분포 | `buff` 60 · `adjust` 48 · `nerf` 33 · `mixed` 22 (+ 자동 판정 344) |
| 만든 방법 | **대화 중 Claude 가 패치 노트를 읽고 붙였다** |
| **API 호출** | **없다.** `ANTHROPIC_API_KEY` 를 쓴 적이 없다 |
| 채점 | Data Dragon diff 와 대조 — **충돌 0** ([`score-labels`](../scripts/score-labels)) |
| 기준 | [labeling-guide.md](../docs/spec/labeling-guide.md) |

패치는 **시드 고정 무작위**로 뽑았다(`label-material --sample`). 처음에는 크기가 작은 패치를 골랐다가 표본을 편향시켰고, 그 사실과 근거는 labeling-guide 에 있다.

### 아직 없는 것

**수치 정답지가 없다** — 「무엇이 몇에서 몇으로」는 아직 안 뽑았다. 방향만 있다. 코드([`extract.py`](../src/lol_balance/extract.py))와 채점기([`crosscheck.py`](../src/lol_balance/crosscheck.py))는 있으나 **한 번도 돌리지 않았다.** API 키가 필요하다.

## `../rules/proposed.jsonl` — 밸런스 기준 규칙

| | |
| --- | --- |
| 규모 | 9개 제안 → **8개 통과** |
| 만든 방법 | **대화 중 Claude 가 학습 32패치를 읽고 제안했다** (`proposed_by: conversation`) |
| **API 호출** | **없다** |
| 채점 | 평가 20패치에서 향상 1.3배 이상 ([`run-rules`](../scripts/run-rules)) |

## 그래서 「LLM arm」이 무슨 뜻인가

비교표의 `A4` · `B4` 는 `uses_llm=True` 인데, **실행할 때 API 를 부른다는 뜻이 아니다.**

```
모델이 산출물을 만드는 데 관여했다      →  uses_llm = True
그 산출물은 저장소에 텍스트로 있다      →  rules/proposed.jsonl
실행할 때는 그 텍스트를 읽어 채점만 한다 →  API 호출 없음
```

**돌아가는 LLM 파이프라인은 아직 없다.** 에이전트(A5 · A6)도 없다.
