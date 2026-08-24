# 검증 데이터셋 (0-A-1)

계획서 3-4 의 **외부 정답지**가 들어오는 곳이다. 여기가 비어 있으면 이 프로젝트의 결과는 자기가 자기를 평가한 것에 불과하다.

## 주 출처 — gaistou 커뮤니티 런 통계

[gaistou.github.io/sts2-stats](https://gaistou.github.io/sts2-stats/) · 표본 약 **512,000 런**

**2026-08-24 조사에서 찾았다. 이 프로젝트에 필요한 것을 거의 그대로 준다.**

| 컬럼 | 내용 | 쓰임 |
| --- | --- | --- |
| `N` | 9층까지 픽업 횟수 | **표본 수.** 저표본 카드를 거르거나 가중치를 준다 |
| `With` / `Without` | **Act 1 클리어율** — 카드가 덱에 있을 때 / 없을 때 | 계획서 3-1 지표와 같은 값 |
| `Diff` | 위 둘의 차이 | **시뮬레이터 값과 직접 대조** |
| `Global winrate` | 3막 전체 승률 | Act 1 성능과 런 성능의 격차 |
| `Δ (A9/A10 only)` | 승천 9~10 구간 별도 | 승천 구간 맞추기 |
| `Last changed patch` | 카드별 마지막 변경 패치 | 패치 이후 데이터만 집계돼 있다 |

### 이 발견이 해결한 것

| 이전에 막혔던 것 | 해결 |
| --- | --- |
| 출처가 표본 수를 공개하지 않는다 | `N` 컬럼이 있다 |
| 대부분 Run 승률만 주고 Act 1 을 분리 못 한다 | `With/Without` 자체가 **Act 1 클리어율**이다 |
| 승천 구간을 맞출 수 없다 | 승천 구간 컬럼이 있다 |
| 카드 있음/없음 비교가 없다 | 그것이 이 사이트의 기본 지표다 |

**픽률을 표본 수의 대리 지표로 쓰려던 우회는 필요 없어졌다.**

## 보조 출처

| 출처 | 내용 | 표본 |
| --- | --- | --- |
| [spire-codex 커뮤니티 통계](https://spire-codex.com/community-stats) | **층별 생존율**, 승천별 승률, 치명적인 적 | 1,323,090 런 (승률 26%) |
| [spire-codex 카드 등급 확률](https://spire-codex.com/mechanics/card-rarity) | 보상 등급 확률표, 레어 보정 | — |
| [OP.GG](https://op.gg/slay-the-spire2/stats/cards) | Pick Rate · Win Rate · Impact | 미공개 |
| [untapped.gg](https://sts2.untapped.gg/en/tier-list/cards/ironclad) | Pick rate, Act 상대 지표 | 미공개 |
| MetaBot.GG | 캐릭터별 승률 (아이언클래드 14.9%) | 미공개 |

**층별 생존율은 성능 곡선의 대조 대상 후보다.** 다만 그래프로만 제공돼 수치 추출이 별도 과제다.

**출처마다 전체 승률이 다르다** — spire-codex 26%, MetaBot 14.9%. 승천 구간과 집계 방식 차이로 보인다. **어느 값을 썼는지 리포트에 명시한다.**

## 들어올 파일

| 파일 | 내용 | 쓰이는 곳 |
| --- | --- | --- |
| `card-stats-v0.111.0.csv` | gaistou 의 아이언클래드 카드별 지표 | 3-4 축 1 |
| `patch-changes-v0.107.1-v0.111.0.csv` | 패치별 카드 변경 내역 | 3-4 축 2 |
| `survival-by-floor.csv` | 층별 생존율 (추출 가능하면) | 성능 곡선 대조 |

## 백테스트 창

**v0.111.0 이 최신이다** (2026-08-13). 창 안에 패치 7개가 있다.

```
v0.107.1 (Major Update 2) → v0.108.0 → v0.109.0 → v0.109.1
                          → v0.110.0 → v0.110.1 → v0.111.0
```

v0.111.0 의 아이언클래드 변경 3건(Expect a Fight 리워크, Forgotten Ritual 버프, Rampage 버프)은 [`../docs/spec/research-notes.md`](../docs/spec/research-notes.md)에 수치까지 적어 뒀다. **나머지 6개 패치의 변경 내역을 모아야 탐지율·오탐률이 통계적으로 의미를 갖는다.**

## 원본을 그대로 두지 않는다

긁어온 HTML 이나 JSON 원본은 커밋하지 않는다. **CSV 로 정리한 것과 언제 어디서 가져왔는지만** 남긴다.
