# Architecture Decision Records

중요한 기술·방법론 결정을 ADR 로 남긴다.

## 무엇을 여기 쓰는가

언어·런타임·패키지 관리, 데이터 수집·저장 구조, 예측 방법론, 평가 설계, LLM 제공자와 호출 방식.

이미 정한 도구의 버전 갱신, 결함 수정, 문서 편집, 출처 한 곳 추가는 해당하지 않는다. 판단 기준은 **"이 선택을 되돌리려면 코드를 다시 써야 하는가"** 다.

## 쓰는 방법

1. `template.md` 를 복사해 `NNNN-short-title.md` 로 만든다.
2. 번호는 `0001` 부터 순서대로. 브랜치를 만들기 직전에 최신 `main` 을 받아 번호가 겹치지 않는지 확인한다.
3. 구현 PR 이나 커밋 본문에서 어느 ADR 인지 밝힌다.
4. 이전 결정을 바꿀 때 기존 문서를 지우지 않고 새 ADR 에서 대체 관계를 기록한다.

**번호가 비는 것은 정상이다.** 쓰다 만 ADR 을 지우면 그렇게 되고, 그 자리를 재사용하면 오히려 이력이 헷갈린다.

## 기록 목록

- [ADR 0001: Python 환경과 코드 품질 도구](0001-python-environment-and-tooling.md)
- [ADR 0002: 예측 지점으로 인정할 패치 커버리지 기준](0002-prediction-point-coverage-criterion.md)
- [ADR 0003: LLM 제공자와 호출 방식](0003-llm-provider-and-calling-convention.md)
- [ADR 0004: 정리한 결과를 어떤 형식으로 남기는가](0004-processed-data-storage-format.md)
