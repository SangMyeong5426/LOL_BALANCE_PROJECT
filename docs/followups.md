# 나중에 확인할 것

**결함 목록이 아니다.** 지금 잘못 돌고 있는 것은 고치고, 여기에는 **지금은 판단할 근거가 없어 미뤄 둔 것**만 적는다.

## 목록

| # | 항목 | 언제 | 근거 |
| --- | --- | --- | --- |
| ~~1~~ | ~~2023 이후 패치 종수·결측 측정~~ | **완료 (2026-08-24)** — 53종, 13_14~16_15 | [data-sources](spec/data-sources.md) |
| ~~2~~ | ~~champion_ranking 배열 해독~~ | **완료 (2026-08-24)** | [ugg-format](spec/ugg-format.md) |
| **2b** | **`rankings` 엔드포인트 해독** | **다음 작업** — 원본은 밤새 받아 둔다 | 결측 17패치를 메우려면 필요 |
| **2c** | **패치 노트 수집** — 정답지 | **다음 작업** | 슬러그 규칙이 깨져 있다. [ddragon-format](spec/ddragon-format.md) |
| 3 | Oracle's Elixir 2021~2026 주소 | 아무 때나 | 새 사이트가 SPA |
| 4 | op.gg 아카이브가 KR 인지 검증 | 실제 수집할 때 | 지금은 정황 증거뿐 |
| 5 | 국제 대회 일정표 (약 30행) | 아무 때나 | — |
| 6 | **저장소 디렉터리 이름** | 사용자가 직접 | 아래 |
| 7 | 강의 자료 4종 본문 | 아무 때나 | 아래 |

### 6. 저장소 디렉터리 이름이 아직 이전 것이다

패키지는 `src/patchlens/` 로 바꿨는데, **저장소 디렉터리 이름 `PROJECT_STS2_AI` 는 그대로다.** 이건 저장소 밖의 경로라 사용자가 직접 바꿔야 한다.

바꾸면 가상환경 경로가 깨지므로 `.venv` 를 다시 만들어야 한다.

```bash
mv ~/workspace/PROJECT_STS2_AI ~/workspace/patchlens
cd ~/workspace/patchlens
rm -rf .venv && python3.11 -m venv .venv
source .venv/bin/activate && pip install -r requirements-dev.txt
```

`patchlens` 라는 이름도 임시다. 계획서를 쓰면서 확정한다.

### 7. 강의 자료 4종의 본문을 아직 읽지 못했다

슬라이드 본문이 이미지라 텍스트 추출이 안 된다. 페이지 이미지로 읽어야 한다.

- `4) 생성형AI_1.Prompt 설계 및 Context Engineering`
- `4) 생성형AI_2.LLM과 Transformer 아키텍처 Day1/Day2`
- `2) 데이터분석 및 AIOps_2.기초통계 Day1/Day2`
- `2) 데이터분석 및 AIOps_3.실전 Feature Engineering`

**새 주제에서 더 중요해졌다** — 기초통계는 예측 평가와 베이스라인에, Feature Engineering 은 피처 설계에, Prompt 설계는 규칙 추출에 직접 쓰인다.
