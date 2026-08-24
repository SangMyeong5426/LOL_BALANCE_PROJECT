# 웹 조사 원자료 (2026-08-24)

Phase 0-A 를 쓰기 위한 1차 조사. **명세가 아니라 원자료다.** 여기 있는 값을 그대로 믿고 코드에 넣지 않는다 — 0-A-2/0-A-3 에서 교차 확인한 뒤 명세로 옮긴다.

**게임 설치 없이 어디까지 되는지를 확인하는 것이 이 조사의 목적이었다.** 결론은 아래 「조사 결론」에 있다.

## 출처와 신뢰도

| 출처 | 무엇이 있나 | 데이터 버전 | 신뢰도 |
| --- | --- | --- | --- |
| [slaythespire.wiki.gg](https://slaythespire.wiki.gg/wiki/Slay_the_Spire_2:Main) | 카드·적·유물·상태이상·패치 노트. **개별 적 페이지에 행동 패턴 있음** | 2026-08-16 갱신 | **높음** — 1차 참조 |
| [op.gg](https://op.gg/slay-the-spire2/stats/cards) | 카드별 Pick Rate · Win Rate · Impact | 미표기 | 중 — 표본 수 미공개 |
| [sts2.untapped.gg](https://sts2.untapped.gg/en/tier-list/cards/ironclad) | 카드별 Pick rate, Act 상대 지표 | 미표기 | 중 — 계획서가 지목한 출처 |
| [sts2front.com](https://sts2front.com/status-effects/) | 상태이상 약 260종 목록 | v0.102.0 언급 | 중 — 버전이 낡음 |
| [spire-codex.com](https://spire-codex.com/spa/mechanics/combat-mechanics) | 전투 메커니즘 정리 | 2026-07-15 | 중 |
| [slaythespire2.net](https://slaythespire2.net/monster) | 적 로스터 112종 (HP만) | v0.110.1 | 낮음 — 행동 로직 없음 |
| mobalytics.gg | 카드·유물 DB | — | **접근 불가 (HTTP 403)** |

**wiki.gg 개별 적 페이지가 이 프로젝트의 핵심 출처다.** 로스터 사이트들은 HP 만 있고 행동 로직이 없다.

---

## 1. 데미지 파이프라인

두 출처가 일치한다.

```
1. 기본 데미지 (카드/기술에 적힌 값)
2. + 힘             (가산, 스택당 +1)
3. × 0.75           (공격자가 약화면)
4. × 1.5            (대상이 취약이면)
5. 내림 (floor)      ← 모든 배율을 곱한 뒤 한 번만
6. − 블록
```

**계획서가 StS1 기준으로 적어 둔 순서(힘 → 약화 → 취약 → 내림 → 블록)와 같다.** 이 부분은 확인됐다.

### 확인된 세부

- **내림은 마지막에 한 번만.** 배율마다 자르지 않는다.
- **다중 타격은 타격마다 파이프라인을 돈다.** 근거: 약화 상태에서 5뎀 3연타는 25% 가 아니라 40% 감소한다 — `5 × 0.75 = 3.75 → 3` 이 타격마다 일어나기 때문이다. 이건 시뮬레이터에서 반드시 재현해야 하는 동작이다.
- **취약·약화는 공격 카드에만 적용된다.** 중독·유물 피해·비공격 카드 피해는 영향을 받지 않는다.
- **약화 배율은 스택 수와 무관하다.** 스택은 지속 턴 수다.

### 미확정 → [`open-questions.md`](open-questions.md)

- 다중 타격 시 **블록**이 타격마다 차감되는지 합산에서 차감되는지 (출처가 상반)
- **취약**이 스택마다 배율이 커지는지 (출처가 상반)
- **Shrink(-30%)** 가 약화와 어떻게 결합되는지

---

## 2. 상태이상

### 기존 (StS1 과 같음)

| 이름 | 효과 | 지속 |
| --- | --- | --- |
| 힘 Strength | 공격 피해 +1/스택 (가산) | 전투 내내 |
| 민첩 Dexterity | 카드 블록 +1/스택 (가산) | 전투 내내 |
| 취약 Vulnerable | 받는 공격 피해 ×1.5 | 턴 종료 시 1 감소 |
| 약화 Weak | 주는 공격 피해 ×0.75 | 턴 종료 시 1 감소 |
| 손상 Frail | 카드 블록 ×0.75 | 턴 종료 시 1 감소 |
| Artifact | 디버프 X회 무효 | 감소 없음 |

### StS2 신규 — **이것이 계획서 0-A-3 의 실제 작업량이다**

| 이름 | 효과 | 비고 |
| --- | --- | --- |
| **Shrink** | 공격 피해 **30% 감소**. **적용한 적이 죽으면 해제** | Shrinker Beetle 이 사용. 2턴, 미중첩 |
| **Tangled** | 2턴간 **공격 카드 비용 +1** | Vine Shambler 가 사용 |
| **Plating** (Plated Armor) | 턴 종료 시 X 블록 획득. **턴 시작 시 1 감소** | 적이 보유 |
| **Wither** | 손에 남는(Retained) 카드. 턴 종료 시 2 피해 후 소멸 | v0.105.0 Aeonglass 보스가 부여 |

**Shrink 와 Tangled 는 Act 1 일반 적이 쓴다.** 페르소나의 킬각 계산(Shrink)과 자원 관리(Tangled)에 직접 걸리므로 12~15장 범위에서도 구현해야 한다.

---

## 3. 아이언클래드

| 항목 | 값 |
| --- | --- |
| 시작 HP | **80** (Ascension 2+ 에서 64) |
| 시작 덱 | Strike ×5, Defend ×4, Bash ×1 |
| 시작 유물 | **Burning Blood** — 전투 종료 시 6 HP 회복 |
| 카드 풀 | **80장** — Common 20 / Uncommon 35 / Rare 25 |
| 멀티 전용 | Blaze, Demonic Shield, Outrage, Tank, Midnight (5장) |
| 획득 불가 | Break, Corruption (2장) |

### 기본 카드 수치

| 카드 | 미강화 | 강화 |
| --- | --- | --- |
| Strike | 6 피해 | 9 피해 |
| Defend | 5 블록 | 8 블록 |
| Bash | 8 피해 + 취약 2 | 10 피해 + 취약 3 |

**Burning Blood(전투당 6 회복)는 클리어율에 직접 영향을 준다.** 시뮬레이터에 반드시 넣어야 한다 — 유물을 "변수 최소화"로 뭉뚱그려 빼면 안 되는 이유다.

---

## 4. Act 1 — **변종이 둘이다**

**계획서가 놓친 것이다.** Act 1 은 **Overgrowth** 와 **Underdocks** 두 갈래이고, **상호 배타적이며 적 풀·엘리트·보스가 통째로 다르다.** 각 15개 방.

| | Overgrowth | Underdocks |
| --- | --- | --- |
| 일반 적 | Nibbit, Inklet, Mawler, Ruby Raiders, Fogmog, Cubex Construct, Vine Shambler, Shrinker Beetle, Fuzzy Wurm Crawler, Slithering Strangler, Snapping Jaxfruit, 슬라임류 | Corpse Slug, Cultists, Fossil Stalker, Gremlin Merc, Haunted Ship, Living Fog, Punch Construct, Seapunk, Sewer Clam, Sludge Spinner, Toadpoles, Two-Tailed Rats |
| 엘리트 | Bygone Effigy, Byrdonis, Phrog Parasite | Terror Eel, Phantasmal Gardeners, Skulking Colony |
| 보스 | Ceremonial Beast, The Kin, Vantom | (미조사) |

**어느 변종을 대상으로 할지 정해야 한다.** 둘 다 하면 적 구현량이 두 배다.

### 조사한 적 4종 — 전부 결정론적 고정 순환

계획서는 "확률 분기 없이 결정론적으로 순환하는 적만 골라 회피"를 **대안**으로 적었는데, **Overgrowth 일반 적은 대체로 그렇다.** 대안이 아니라 기본값으로 쓸 수 있다.

#### Nibbit — HP 42~46 (A9 44~48)

| 기술 | 효과 | A9 |
| --- | --- | --- |
| Butt | 12 피해 | 13 |
| Hesitant Slice | 6 피해 + 5 블록 | 7 + 6 |
| Hiss | 힘 +2 | +3 |

**패턴**: 단독이면 Butt 로 시작. 2마리면 앞은 Hesitant Slice, 뒤는 Hiss 로 시작(순환 지점을 어긋나게 해서 매 턴 서로 다른 기술을 쓴다). 이후 **Butt → Hesitant Slice → Hiss 고정 순환.**

#### Shrinker Beetle — HP 38~40 (A8 40~42)

| 기술 | 효과 | A8 |
| --- | --- | --- |
| Shrinker | Shrink 부여 (2턴, 미중첩) | — |
| Chomp | 7 피해 | 8 |
| Stomp | 13 피해 | 14 |

**패턴**: **항상 Shrinker 로 시작**, 이후 Chomp ↔ Stomp 교대. HP 조건·확률 분기 없음.

#### Vine Shambler — HP 61 (A8 64)

| 기술 | 효과 | A9 |
| --- | --- | --- |
| Swipe | 6 피해 ×2 | 7 ×2 |
| Grasping Vines | 8 피해 + Tangled 1 | 9 |
| Chomp | 16 피해 | 18 |

**패턴**: **Swipe → Grasping Vines → Chomp 고정 순환.**

#### Cubex Construct — HP 65 (A8 70), **Artifact 1 보유**

| 기술 | 효과 | A8 |
| --- | --- | --- |
| Charge Up | 힘 +2 | — |
| Repeater Blast | 7 피해 + 힘 +2 | 8 |
| Expel Blast | 5 피해 ×2 | 6 ×2 |

**패턴**: Charge Up → Repeater Blast → Expel Blast, 이후 **Repeater → Repeater → Expel 반복.** 힘이 계속 쌓여 피해가 체증한다.

> Ascension 수치 표기가 출처에서 일관되지 않았다(`8/9` 같은 표기). 재확인 필요 — [`open-questions.md`](open-questions.md).

**적 3종 후보**: Shrinker Beetle(Shrink 검증), Vine Shambler(Tangled·다중 타격 검증), Cubex Construct(힘 체증·Artifact 검증). 셋이 서로 다른 메커니즘을 하나씩 물고 있어 데미지 파이프라인 검증 커버리지가 넓다.

---

## 5. 유물

| 항목 | 값 |
| --- | --- |
| 등급 | Starter · Common · Uncommon · Rare · Ancient · Shop · Event |
| 등장 확률 | Common 50% / Uncommon 33% / Rare 17% |
| 캐릭터 전용 | 9종/캐릭터 |
| Shop 전용 | 30종 (캐릭터당 1종 독점) |
| 획득처 | 엘리트, 보물방, 상인, 휴식처(Shovel), Ancient(막 시작 시) |

개별 유물 효과는 각 유물 페이지를 따로 봐야 한다. **계획서에 유물 범위가 정의돼 있지 않다** — 시작 유물만인지, Act 1 획득분까지인지.

---

## 6. 검증 데이터 출처 (0-A-1)

| 출처 | 지표 | 필터 | 표본 수 |
| --- | --- | --- | --- |
| **OP.GG** | Pick Rate · Win Rate · **Impact** | 캐릭터 / 풀 / 타입 / 등급 / 비용 / 싱글·멀티 | **미공개** |
| **untapped.gg** | Pick rate, Act 상대 지표 | 캐릭터 | **미공개** |

OP.GG 예시 행: Supercritical 34.8% / 57.1% / +9.7, Parse 12.5% / 60.0% / +33.7.

**둘 다 표본 수를 공개하지 않는다.** 계획서 3-4 축 1(스피어만 상관)에서 저표본 카드를 거를 방법이 없다는 뜻이다. **이건 0-A-1 을 시작하기 전에 해결해야 한다.**

**Act 별 분리 지표는 untapped.gg 에만 있는 것으로 보인다.** 이 프로젝트가 Act 1 한정이므로 중요하다.

---

## 7. 패치 — 백테스트 정답지

**v0.111.0 이 최신이다** (2026-08-13, wiki 2026-08-16 갱신 기준). 계획서의 버전 고정이 유효하다.

백테스트 창(v0.107.1 → v0.111.0)에 **7개 패치**가 있다.

```
v0.107.1 (Major Update 2) → v0.108.0 → v0.109.0 → v0.109.1
                          → v0.110.0 → v0.110.1 → v0.111.0
```

### v0.111.0 아이언클래드 변경 — 정답지 샘플

| 카드 | 분류 | 변경 전 | 변경 후 |
| --- | --- | --- | --- |
| Expect a Fight | **리워크** | Uncommon / Skill / 코스트 2(1) / 손의 공격 카드 수만큼 에너지 획득 | Uncommon / Skill / 코스트 3 / 15(16) 블록 + 힘당 5(8) 추가 블록 |
| Forgotten Ritual | 버프 | 에너지 획득에 카드 소멸 필요 | 소멸 불필요 |
| Rampage | 버프 | 기본 9, 증가 5(9) | 기본 10, 증가 5(10) |

**적 변경**: Axebot(Hammer Uppercut 12→14, 부활당 최대 HP +10, One-Two 9→10), Mechaknight(화염방사 턴 8(12) 피해), Exoskeleton·Globe Head·Louse Progenitor·Soul Fysh·Entomancer 상향.

**유물 변경**: Regalite 블록 6→4(너프), Vakuu's Jeweled Mask(Innate 파워 회피), Nonupeipe's Beautiful Bracelet(선택 → 무작위 4장, 너프).

**주의**: v0.111.0 변경 목록은 Act 1 Overgrowth 일반 적과 겹치지 않는다. 백테스트 표본을 확보하려면 **v0.107.1~v0.110.1 의 아이언클래드 카드 변경까지 모아야 한다.**

---

## 조사 결론

**게임 설치 없이 Phase 0-A 를 끝낼 수 있다.** 그리고 디컴파일(0-B-1/0-B-2)이 필요하다는 계획서의 전제는 **적어도 Act 1 Overgrowth 일반 적에 대해서는 성립하지 않는다.**

| 계획서가 걱정한 것 | 실제 |
| --- | --- |
| "위키에 적 행동 패턴 로직이 불완전하다" | wiki.gg **개별 적 페이지에 있다.** 조사한 4종 전부 결정론적 고정 순환이 명시돼 있었다 |
| "데미지 파이프라인이 StS2 에서 유효한지 모른다" | **StS1 순서 그대로 확인됐다.** 세부 3건만 미확정 |
| "StS2 신규 상태이상이 어디 끼는지 모른다" | Shrink·Tangled·Plating·Wither 를 특정했다. 파이프라인 위치는 1건 미확정 |

**게임이 여전히 필요한 곳은 Phase 1-7(실제 게임과 데미지 대조)이다.** 그건 플레이만 하면 되고 디컴파일이 필요 없다.

**미확정 5건은 [`open-questions.md`](open-questions.md)로 넘겼다.** 그중 셋은 게임 안에서 몇 판만 돌려도 확인된다.
