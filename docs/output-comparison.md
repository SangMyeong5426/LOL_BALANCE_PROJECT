# 방향 출력과 GPT-4.1 mini 비교

**OpenAI 조건에는 API 키가 필요하다. 선택 실험이다.** ② 방향의 출력 오류를
확인하며 기존 ① 대상·② 방향·③ 효과 결과를 바꾸지 않는다.
로컬 두 조건만 실행할 때는 키가 필요 없다. 설계는 [ADR 0014](adr/0014-direction-output-comparison.md).

## 바로 실행

프로젝트 루트의 `.env`에 다음 항목을 넣는다. 키를 대화나 Git에 올리지 않는다.
다른 설정은 바꾸지 않아도 된다.

```dotenv
OPENAI_API_KEY=발급받은_키
```

```bash
# 준비 확인: 모델 호출·과금 없음. 개발 표본과 같은 R1 근거를 저장한다.
.venv/bin/python scripts/compare-agent-output --prepare

# Ollama 서버와 qwen3.5:9b가 준비된 상태에서 네 조건을 실행한다.
.venv/bin/python scripts/compare-agent-output

# 키 없이 로컬 두 조건만 실행
.venv/bin/python scripts/compare-agent-output --local-only

# 저장된 결과만 다시 채점. 모델 호출 없음.
.venv/bin/python scripts/compare-agent-output --score
```

새 환경은 `pip install -r requirements.txt`로 의존성을 설치한다.
로컬 모델이 없으면 `ollama pull qwen3.5:9b`, 서버가 꺼져 있으면 `ollama serve`가
필요하다. 준비 명령은 서버나 키의 유효성을 검사하는 호출을 하지 않는다.

## 무엇을 비교하나

개발 표본 25건에 저장된 같은 지표·익명 R1 사례를 준다.

| 모델 | 숫자 출력 | 방향·확신 출력 |
| --- | --- | --- |
| 로컬 qwen3.5:9b, thinking 끔 | nerf_prob | direction + confidence |
| GPT-4.1 mini | nerf_prob | direction + confidence |

조회는 코드가 하므로 **체인** 실험이다. 기존 에이전트 B8 기록과 직접 성능을
비교하지 않는다. 이번 실험 안에서 B5 다수결을 같은 표본으로 채점한다.
네 조건에서 모두 답한 표본과 조건별 전체 답변을 구분해 보고한다.
숫자 50은 방향을 나타내지 않아 방향 정답으로 세지 않는다. 방향·확신 출력의
confidence=50은 명시된 direction으로 방향을 채점하고, AUC에는 0.5를 쓴다.

## 결과와 설명 검토

결과는 `runs/direction-output-comparison/`에 저장된다(커밋하지 않는다).

- `inputs.json`: 고정된 입력·정답·B5 점수·설정. 모델에는 input만 보낸다.
- `results.json`: 조건별 원래 응답, 검증 결과, 토큰 사용량.
- `report.md`: 방향 정확도, AUC, 기권, 실패, 공통 표본의 B5 비교.
- `review.json`: 설명을 읽고 `reason_direction`에 `nerf`, `buff`, `unclear`를
  적는 검토 파일. 미검토는 `null`로 둔다. 설명과 방향의 불일치율은 검토 후
  `--score`로 계산한다. 자연어 판단을 정규식으로 대신하지 않는다.
- `budget.json`: 호출 전 예약액과 실제 사용량. 이 파일은 지우지 않는다.

중간에 중단되면 같은 명령으로 이어받는다. 이미 저장한 실패도 반복 호출하지
않는다. 서버·인증 오류는 종류만 기록하고 중단한다. 서버에서 처리했는지 불명인
OpenAI 요청은 예약액을 유지하고 자동 재시도하지 않는다.
입력·모델·스키마가 바뀌면 기존 결과에 추가하지 않고 중단한다. `--limit`은 최초
준비 시에만 선택하며 기본 25건이다.

OpenAI 호출마다 출력 1,000토큰, 자동 재시도 0회로 제한한다. 보수적인 입력·출력
예약액을 포함해 이 명령의 누적 $1 이내에서만 요청한다. 다른 명령이나 계정의
지출은 이 장부에 포함되지 않는다. 실제 사용량이 없는 실패는 예약액으로 센다.

이 실험으로 방향 출력의 오류가 줄어드는지 확인할 수 있다. 모델 크기만의 효과,
전체 서비스의 적중률 개선, 평가 표본의 일반 성능을 증명하지는 않는다.
