# Ascent — LLM 에이전트 기반 카드 게임 밸런스 검증 시스템

게임의 전투 규칙을 결정론적으로 구현하고, LLM 이 설계한 플레이어 페르소나가 수천 판을 플레이하게 한 뒤, 카드별 성능 지표를 뽑아 **외부 데이터와 대조 검증**하는 시스템.

대상은 Slay the Spire 2 **v0.111.0**, 아이언클래드, Act 1 이다.

## 현재 상태

**개발 환경 세팅 완료. Phase 0-A(스펙 문서) 착수 전.**

시뮬레이터는 아직 없다. 지금 저장소에 있는 것은 환경, 작업 규칙, 그리고 Phase 0-A 산출물이 들어올 자리다.

| Phase | 내용 | 상태 |
| --- | --- | --- |
| 0-A | 스펙 문서 (게임 설치 불필요) | 착수 전 |
| 0-B | 게임 데이터 추출 | 착수 전 |
| 1 | 룰 엔진 (LLM 없음) | 착수 전 |
| 2 | 페르소나 에이전트 | 착수 전 |
| 3 | 분석과 외부 검증 | 착수 전 |

## 설계 원칙

**LLM 과 결정론적 로직을 분리한다.** 전투 판정은 룰 엔진이 전담하고 LLM 은 개입하지 않는다. LLM 은 페르소나 정책을 *생성*하는 단계와 결과를 *해석*하는 단계에만 관여한다. 이 경계가 재현성(같은 시드 → 같은 결과), 비용(수천 판을 돌려도 호출은 페르소나당 수 회), 신뢰성(모델이 헛소리를 해도 결과가 오염되지 않음)을 동시에 만든다.

**정책을 만든 뒤 코드가 실행한다.** 매 턴 LLM 에 묻지 않는다. 정책이 텍스트로 남으므로 "왜 그렇게 플레이했는지" 설명할 수 있다.

**데이터와 코드를 분리한다.** 카드·적 수치는 `data/` 의 JSON 이 기준이다. 밸런스 수치를 바꿔가며 실험하려면 필수이고, 카드 12장 → 80장 확장도 이 설계가 있어야 가능하다.

**검증 기준은 프로젝트 바깥에서 가져온다.** 자기가 만든 시스템을 자기가 평가하는 순환을 피한다. 실측 승률 통계와 개발사 패치 이력이 정답지다.

## 시작하기

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pre-commit install

cp .env.example .env    # Phase 2 전에는 비워 둬도 된다
```

검사:

```bash
ruff check .        # 린팅
ruff format .       # 포매팅
pytest              # 테스트
pytest --cov        # 커버리지 포함
mypy                # 정적 타입 검사
```

`pre-commit` 이 커밋 전에 ruff 를 자동으로 돌린다. **`pytest` 와 `mypy` 는 자동으로 돌지 않으므로 직접 실행한다.**

## 구조

```text
docs/spec/            게임 규칙이 무엇인가        ← Phase 0-A 산출물
data/v0.111.0/        카드·적 수치가 얼마인가     ← Phase 0-B 산출물 (JSON)
src/ascent/           룰 엔진과 에이전트
tests/                데미지 계산 단위 테스트
datasets/             외부 정답지 (실측 통계·패치 노트 스냅샷)
docs/adr/             기술 결정 기록
```

무엇을 만드는지는 [`docs/plan.md`](docs/plan.md)에, 어떻게 작업하는지는 [`AGENTS.md`](AGENTS.md)(= [`CLAUDE.md`](CLAUDE.md))에 있다.

## 게임 데이터에 대해

`data/` 의 JSON 은 **v0.111.0 게임 데이터에서 추출해 자체 형식으로 정리한 것이고, 원본은 포함하지 않는다.** 디컴파일한 코드와 게임 데이터 파일은 저장소에 올리지 않는다.

## 기술 스택

| 기술 | 용도 |
| --- | --- |
| Python 3.11 | 룰 엔진, 시뮬레이션 |
| pytest · pytest-cov | 데미지 계산 단위 테스트 |
| pandas | 로그 집계, 지표 산출 |
| scipy | 스피어만 순위 상관계수 |
| pydantic | 데이터 스키마 검증, LLM 구조화 출력 파싱 |
| anthropic | 페르소나 정책 생성, 분석 리포트 |
| matplotlib | 결과 시각화 |
| ruff · mypy · pre-commit | 코드 품질 |

버전의 단일 기준은 [`requirements.txt`](requirements.txt)와 [`requirements-dev.txt`](requirements-dev.txt)다. 선택 근거는 [ADR-0001](docs/adr/0001-python-environment-and-tooling.md)에 있다.

### 도입하지 않은 것과 그 이유

| 기술 | 미도입 사유 |
| --- | --- |
| Vector DB / RAG | 검색할 문서가 없다. 이 프로젝트에 들어갈 자리가 없다 |
| LangChain / LangGraph | 정책 생성이 단발성 호출이라 오케스트레이션이 과하다. 분석 에이전트를 다단계로 쪼갤 경우 재검토한다 |
| FastAPI | 시뮬레이터를 API 로 감쌀 필요가 현재 없다 |
| uv / poetry | 의존성이 6개다. 잠금 파일의 이득보다 도구를 하나 더 설명해야 하는 비용이 크다 ([ADR-0001](docs/adr/0001-python-environment-and-tooling.md)) |
| Docker · Streamlit | 보여 줄 결과가 나온 뒤에 붙인다 ([followups 5](docs/followups.md)) |

안 쓴 이유를 설명할 수 있는 것이 도구를 판단할 줄 안다는 증거다.

## 알려진 한계

**Act 1 한정이라 후반 스케일링형 카드(파워류)가 저평가된다.** 이것은 결함이 아니라 분석 대상이다 — 실측 데이터에도 Act 승률과 Run 승률이 분리돼 있으므로, 그 격차를 설명하는 것이 리포트의 한 챕터가 된다.

**실측 승률 대조에서 불일치가 나오는 것이 정상이다.** 실측 승률 델타는 상관관계이지 인과관계가 아니다. 잘하는 플레이어가 좋은 덱에서 뽑는 카드는 승률이 높게 나온다. 시뮬레이터는 통제된 조건이라 이 교란이 없다. **불일치를 먼저 지적하고 원인을 분석하는 것이 리포트의 질을 결정한다.**

미확정으로 남겨 둔 것은 [`docs/spec/open-questions.md`](docs/spec/open-questions.md)와 [`docs/followups.md`](docs/followups.md)에 있다.
