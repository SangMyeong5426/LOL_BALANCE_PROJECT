# Data Dragon — 무엇을 잡고 무엇을 못 잡나

버전 스냅샷 두 개를 비교하면 수치 변경이 나온다. **그런데 정답지로 쓰기에는 구멍이 있다.** 이 문서는 그 경계를 적는다.

구현은 [`src/patchlens/ddragon.py`](../../src/patchlens/ddragon.py), 검사는 [`tests/test_ddragon.py`](../../tests/test_ddragon.py).

## 받는 법

```
버전 목록   https://ddragon.leagueoflegends.com/api/versions.json
전 챔피언   https://ddragon.leagueoflegends.com/cdn/{버전}/data/en_US/championFull.json
```

**`championFull.json` 하나에 전 챔피언이 들어 있다.** 버전당 요청이 한 번이고 1.5~2.2 MB 다. 라이엇 CDN 이라 요청 제한이 없다.

우리 범위(13.14 ~ 16.15)에 **74개 버전**이 있고 전부 받았다.

## 잡는 것

| 항목 | 상태 |
| --- | --- |
| **기본 스탯** (체력·방어력·공격력·이동속도·성장치 등 20종) | ✅ 완전 |
| **스킬 쿨다운** | ✅ 완전 |
| **스킬 코스트** | ✅ 완전 |
| **스킬 사거리** | ✅ 완전 |
| 스킬 `effect` 배열 | ⚠️ **스킬 932개 중 306개(33%)만** 값이 살아 있다 |
| 툴팁 텍스트 | ⚠️ 거의 노이즈 — 아래 |

실제로 잡힌 예다.

```
Alistar movespeed  330 → 335
Belveth hp         610 → 580
Zac E 쿨다운        22/19/16/13/10 → 21/18/15/12/9
```

## 못 잡는 것 — **대부분의 스킬 피해량**

툴팁이 이렇게 생겼다.

```
Ahri throws her orb, dealing <magicDamage>{{ totaldamage }} magic damage</magicDamage>
```

**실제 수치가 없다.** `{{ totaldamage }}` 는 게임 클라이언트가 채우는 자리이고, 그 값은 Data Dragon 에 없다. `datavalues` 와 `vars` 는 **932개 스킬 전부 비어 있다.**

즉 **피해량만 바꾼 조정은 diff 에 안 나타난다.** 쿨다운이나 스탯을 같이 건드렸으면 그쪽으로 잡히지만, 순수 피해량 변경은 보이지 않는다.

## 툴팁 변경은 대부분 변수명 정리다

세 패치를 재 봤다.

| 패치 | 툴팁 변경 | 플레이스홀더 이름만 | 실제 숫자 변경 | 문구만 |
| --- | --- | --- | --- | --- |
| 16.14 → 16.15 | 32 | **30** | 2 | 0 |
| 15.24 → 16.1 | 19 | **16** | 1 | 2 |
| 14.9 → 14.10 | 4 | **3** | 1 | 0 |

```
{{ e1 }}  →  {{ stackduration }}
{{ e4 }}  →  {{ bonusas }}
```

라이엇이 내부 변수명을 정리한 것이다. **이걸 수치 변경으로 세면 조정이 없는 패치가 조정 투성이로 보인다.** 비교하기 전에 `{{ }}` 와 `<태그>` 를 걷어낸다.

## 게임 모드 변형을 걸러낸다

`championFull.json` 에는 정식 챔피언이 아닌 항목이 섞여 있다.

```
Ahri        key 103
Jade_Ahri   key 60103    ← 게임 모드 변형
```

16.15.1 기준 **233종 중 60종이 `Jade_*`** 다. 안 걸러내면 그 60종이 통째로 「신규 챔피언」으로 잡힌다.

**정식 챔피언 id 에는 밑줄이 없고**(`Ahri`, `MonkeyKing`, `DrMundo`), 변형은 key 가 원본 + 60000 이다. 둘 다로 거른다.

## 그래서 정답지는 패치 노트여야 한다

**앞선 판단을 정정한다.** `data-sources.md` 에 「Data Dragon diff 가 패치 노트보다 정확하다」고 적었는데, **스킬 피해량에 대해서는 틀렸다.**

| | 역할 |
| --- | --- |
| **패치 노트** | **정답지.** 정확한 수치가 전부 적혀 있다. 자연어라 LLM 으로 구조화해야 한다 |
| **Data Dragon diff** | **검증 수단.** 스탯·쿨다운 변경은 양쪽에 다 나와야 한다 |

이것이 오히려 설계에 맞는다 — **패치 노트를 구조화된 변경 기록으로 바꾸는 것**이 원래 LLM 의 첫 번째 일이었다. Data Dragon diff 는 그 추출이 맞았는지 기계적으로 대조할 수단이 된다.

## 패치 노트 주소가 일관되지 않다

받을 수는 있는데 슬러그 규칙이 깨져 있다.

```
DDragon 13.20  →  patch-13-20-notes      200
DDragon 14.10  →  patch-14-10-notes      200
DDragon 15.14  →  patch-25-14-notes      200      ← 25.x 로 바뀜
DDragon 15.1   →  patch-25-1-notes       404
                  patch-25-s1-1-notes    200      ← 시즌 표기
DDragon 16.1   →  patch-26-1-notes       200
DDragon 16.9   →  patch-26-9-notes       404      ← 최신은 또 다름
```

**2025년부터 라이엇이 연도 기반 번호로 바꿨는데 Data Dragon 은 옛 번호를 유지한다.** 그리고 일부는 시즌 표기(`s1`)를 쓴다.

**추측으로 맞힐 수 없다.** [`/en-us/news/tags/patch-notes/`](https://www.leagueoflegends.com/en-us/news/tags/patch-notes/) 목록을 훑어 실제 슬러그를 모아야 한다. 그 페이지에서 120개를 찾았고 우리 범위에는 24개가 들어 있었다 — **목록이 잘려 있으므로 넘겨 가며 더 받아야 한다.**
