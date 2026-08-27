"""검색기 셋 — **에이전트가 쥘 도구.**

**지금은 검색부(R)만 있다.** RAG 는 검색해서 **생성**한다는 뜻인데 `A5`·`B5` 는
이웃 라벨을 **다수결**할 뿐 생성 단계가 없다. 문서에서 「RAG」라고만 부르지 않는
이유가 이것이다 — `docs/glossary.md` 참조.

그리고 이 프로젝트의 검색은 **문서 검색이 아니라 사례 검색**이다. 검색 키가
텍스트가 아니라 수치이기 때문이다.

    「지금 아리가 승률 53.1% · 픽률 9.2% 다.
      과거에 이 상태였던 챔피언들에게 무슨 일이 있었나?」

그래서 셋으로 나눈다.

    R1  사례 검색   피처 공간의 근접 이웃      「비슷했던 챔피언과 그때의 조정」
    R2  노트 검색   패치 노트 텍스트          「이 챔피언이 왜 조정됐었나」
    R3  수치 조회   패널 직접 조회            「지금 정확한 값이 얼마인가」

**R1 만 쓰이고 있다.** `A5`·`B5` 가 부르는 것은 `CaseSearch` 뿐이고, **R2·R3 는
만들고 테스트했으나 아직 어떤 arm 도 부르지 않는다.** 셋 다 「에이전트가 쥘
도구」로 만든 것이라 그렇다. [README](../../README.md) 의 「벡터 검색을 수치
조회에 쓰지 않는다」가 그대로 적용된다.

## 정답 누출을 도구 안에서 막는다

에이전트가 「패치 t 를 예측하라」는 과제를 받고 t 이후를 읽으면 정답을 그냥 본
것이 된다. **이것을 프롬프트 지시로 막으면 안 된다** — 모델이 뭐라고 요청하든
경계 밖 데이터에 닿을 수 없어야 한다.

그래서 모든 검색기가 `as_of` 를 생성자에서 받아 고정한다. 호출부가 넘기는 것이
아니라 **도구가 태어날 때 박힌다.**
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lol_balance.panel import PanelRow, patch_index
from lol_balance.patchnotes import ChangeBlock

# 사례 검색에 쓰는 피처. 규모가 다르므로 표준화해서 견준다.
CASE_FEATURES = ("win_rate", "pick_rate", "ban_rate", "wr_gap")

# 프로 경기까지 넣은 거리. **학습 구간 3겹 교차검증으로 골랐다** — 평가 구간을
# 보고 고르면 그것이 곧 누출이다.
#
#     기본        CV AUC 0.885
#     + 프로      CV AUC 0.932   ← 이것
#     + 추세      CV AUC 0.882   (도움이 안 된다)
#     + 둘 다     CV AUC 0.925
#
# 회귀에서 프로 피처가 준 향상(+0.04)과 같은 크기다. **솔랭에 없는 신호이고,
# 그것은 거리를 재는 데도 그대로 유효하다.**
CASE_FEATURES_PRO = CASE_FEATURES + ("pro_pick_rate", "pro_ban_rate")


@dataclass(frozen=True)
class Case:
    """과거 사례 하나 — 그때 상태와 그 다음에 일어난 일."""

    row: PanelRow
    distance: float

    @property
    def outcome(self) -> str:
        if not self.row.adjusted_next:
            return "조정 없음"
        return self.row.direction_next or "조정됨(방향 미상)"


def _standardise(
    rows: tuple[PanelRow, ...],
    features: Sequence[str] = CASE_FEATURES,
) -> tuple[dict[str, float], dict[str, float]]:
    """각 피처의 평균과 표준편차. **참고 사례에서만 구한다.**"""
    mean: dict[str, float] = {}
    spread: dict[str, float] = {}
    for name in features:
        values = [float(v) for r in rows if (v := getattr(r, name)) is not None]
        mean[name] = sum(values) / len(values) if values else 0.0
        if len(values) > 1:
            var = sum((v - mean[name]) ** 2 for v in values) / (len(values) - 1)
            spread[name] = math.sqrt(var) or 1.0
        else:
            spread[name] = 1.0
    return mean, spread


class CaseSearch:
    """R1 — 비슷했던 과거 사례를 찾는다.

    **경계는 생성자에서 박힌다.** `as_of` 이후 행은 참고 목록에 아예 안 들어간다.
    """

    def __init__(
        self,
        rows: Sequence[PanelRow],
        as_of: str,
        features: Sequence[str] = CASE_FEATURES,
    ) -> None:
        limit = patch_index(as_of)
        self.as_of = as_of
        self.features = tuple(features)
        self.pool = tuple(r for r in rows if r.patch_index < limit)
        self.mean, self.spread = _standardise(self.pool, self.features)

    def _vector(self, row: PanelRow) -> tuple[float | None, ...]:
        out = []
        for name in self.features:
            value = getattr(row, name)
            out.append(
                None
                if value is None
                else (float(value) - self.mean[name]) / self.spread[name]
            )
        return tuple(out)

    def _distance(self, a: PanelRow, b: PanelRow) -> float | None:
        """맞대 볼 피처가 하나도 없으면 거리를 정의하지 않는다.

        결측을 0 으로 채우면 「평균값이었다」가 되어 엉뚱한 사례가 가까워진다.
        """
        total, seen = 0.0, 0
        for x, y in zip(self._vector(a), self._vector(b), strict=True):
            if x is None or y is None:
                continue
            total += (x - y) ** 2
            seen += 1
        return math.sqrt(total / seen) if seen else None

    def similar(self, target: PanelRow, k: int = 10) -> tuple[Case, ...]:
        scored = []
        for row in self.pool:
            if row.champion_id == target.champion_id and row.patch == target.patch:
                continue
            distance = self._distance(target, row)
            if distance is not None:
                scored.append(Case(row, distance))
        scored.sort(key=lambda c: (c.distance, c.row.patch_index, c.row.champion_id))
        return tuple(scored[:k])


_WORD = re.compile(r"[a-z]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


class NoteSearch:
    """R2 — 패치 노트 텍스트를 찾는다.

    **BM25 를 쓴다.** 코퍼스가 패치 노트 문장 수천 개 규모라 밀집 임베딩이
    희소 방식을 이긴다는 보장이 없고, 챔피언 이름이나 스킬 이름처럼 **정확
    일치가 중요한 검색**에서는 오히려 희소 쪽이 낫다. 새 의존성도 안 는다.
    """

    K1 = 1.5
    B = 0.75

    def __init__(self, blocks: Mapping[str, Sequence[ChangeBlock]], as_of: str) -> None:
        limit = patch_index(as_of)
        self.as_of = as_of
        self.docs: list[tuple[str, ChangeBlock, list[str]]] = []
        for patch, items in blocks.items():
            if patch_index(patch) >= limit:
                continue
            for block in items:
                text = f"{block.champion} {block.section} " + " ".join(block.lines)
                self.docs.append((patch, block, _tokens(text)))
        self.frequency: Counter[str] = Counter()
        for _, _, tokens in self.docs:
            self.frequency.update(set(tokens))
        lengths = [len(t) for _, _, t in self.docs]
        self.average = sum(lengths) / len(lengths) if lengths else 1.0

    def _idf(self, term: str) -> float:
        n = len(self.docs)
        seen = self.frequency.get(term, 0)
        return math.log(1 + (n - seen + 0.5) / (seen + 0.5))

    def search(
        self, query: str, k: int = 5
    ) -> tuple[tuple[str, ChangeBlock, float], ...]:
        terms = _tokens(query)
        scored = []
        for patch, block, tokens in self.docs:
            counts = Counter(tokens)
            total = 0.0
            for term in terms:
                freq = counts.get(term, 0)
                if not freq:
                    continue
                norm = 1 - self.B + self.B * len(tokens) / self.average
                total += (
                    self._idf(term) * freq * (self.K1 + 1) / (freq + self.K1 * norm)
                )
            if total > 0:
                scored.append((patch, block, total))
        scored.sort(key=lambda x: (-x[2], x[0], x[1].champion))
        return tuple(scored[:k])


class StatLookup:
    """R3 — 지금 값이 얼마인지 그대로 준다. **추정하지 않는다.**

    **아직 부르는 arm 이 없다.** 억지로 끼워 넣지 않는다 — `NoteSearch` 를
    이름만으로 질의했다가 「스킨 이름 변경」이 최상위로 올라온 적이 있고,
    쓰이지 않는 도구를 쓰이는 것처럼 보이게 만드는 것이 그것보다 나쁘다.

    이것은 **에이전트가 루프 중에 조회하려고** 만든 도구다. 단발 판단(`B6`)은
    맥락을 한 번에 받으므로 조회할 일이 없고, 이력·추세는 `PanelRow` 가
    (`d_win_rate` · `history_len` · `recent_adjustments` · `high_wr_streak`)
    이미 담고 있다. **루프가 생기면 그때 쓰인다.**
    """

    def __init__(self, rows: Sequence[PanelRow], as_of: str) -> None:
        limit = patch_index(as_of)
        self.as_of = as_of
        self.rows = tuple(r for r in rows if r.patch_index < limit)

    def champion(self, name: str, patch: str | None = None) -> tuple[PanelRow, ...]:
        found = [r for r in self.rows if r.champion == name]
        if patch is not None:
            found = [r for r in found if r.patch == patch]
        return tuple(sorted(found, key=lambda r: r.patch_index))

    def patch(self, patch: str) -> tuple[PanelRow, ...]:
        return tuple(
            sorted(
                (r for r in self.rows if r.patch == patch),
                key=lambda r: -r.pick_rate,
            )
        )
