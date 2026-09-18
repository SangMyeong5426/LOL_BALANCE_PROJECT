"""R1·R2·R3 를 LangChain `@tool` 로 감싼다 — 교재 5장.

`retrieval.py` 가 셋을 「에이전트가 쥘 도구」로 만들어 뒀다. 여기서는 **감싸기만
한다.** 검색 로직을 새로 짜면 `A5`·`B5` 와 짝지어 비교할 수 없다.

## `as_of` 는 인자로 열지 않는다

`retrieval.py` 의 원칙 그대로다 — *정답 누출을 프롬프트 지시로 막으면 안 된다.
모델이 뭐라고 요청하든 경계 밖 데이터에 닿을 수 없어야 한다.* 그래서
`make_tools` 가 검색기를 **만들 때** 경계를 박고, 도구 인자에는 경계가 없다.

## 경계와 대상 패치는 다를 수 있다

시연은 둘이 같다(「16_13 을 보고 16_14 를」). 평가는 다르다 — B5·B6 이 경계를
**분할점에 고정**하므로(`expanding=False`) 에이전트도 그래야 짝지어 비교된다. 그때
대상 행은 제 패치에 있고 검색 경계는 분할점이다. `target` 으로 그 행을 준다.

## 익명 조건 — ADR 0006 을 따른다

`alias` 를 주면 대상은 **그 키로만** 불린다. B6 이 한 대로 이웃도 **이름·패치
없이** 수치와 결과만 보인다 — 같은 챔피언의 과거 기록이 이웃으로 나와도 정체가
안 드러난다. R2 는 대상의 스킬 이름을 드러내므로 익명 평가에서는 뺀다(`notes`).

## 대상 행의 라벨은 절대 내보내지 않는다

`PanelRow` 는 `adjusted_next` · `direction_next` 를 품고 있다. **대상 행의 그 둘이
곧 정답이다.** 과거 행의 것은 이미 공개된 결과라 근거로 쓴다.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from lol_balance.agent.data import Corpus
from lol_balance.baseline import direction_rows
from lol_balance.explain import outcome
from lol_balance.panel import PanelRow, next_patch, patch_index
from lol_balance.retrieval import CaseSearch, NoteSearch, StatLookup

# 한 번에 너무 많이 주면 모델이 표를 못 읽는다. `reasons` 와 B5 가 25 를 쓴다.
MAX_CASES = 25


def _pct(value: float | None) -> str:
    return "  —  " if value is None else f"{value:5.1%}"


def _outcome(row: PanelRow) -> str:
    """과거 행의 결과. **`explain.outcome` 을 그대로 쓴다** — `ask` 와 같은 말이다.

    한때 너프·버프 밖을 전부 「조정됨(방향 미상)」으로 뭉갰는데, 저장소는
    `adjust(방향 없음)` 과 `mixed(방향이 갈림)` 을 **다른 것으로** 가른다.
    다른 것을 한 이름으로 부르면 읽는 사람이 같은 것으로 읽는다.
    """
    return outcome(row)


def patch_index_safe(patch: str) -> int:
    try:
        return patch_index(patch)
    except KeyError:
        return 10**6


def make_tools(
    corpus: Corpus,
    as_of: str,
    *,
    target: PanelRow | None = None,
    alias: str | None = None,
    notes: bool = True,
    stats: bool = True,
    stat_as_of: str | None = None,
    fixed_cases: int | None = None,
) -> list[BaseTool]:
    """`as_of` 이전만 보는 도구. **경계는 여기서 한 번 박히고 끝이다.**

    target       대상 행. 없으면 이름으로 `as_of` 패치에서 찾는다(시연)
    alias        익명 키. 주면 대상은 이것으로만 불리고 이웃 이름·패치가 가려진다
    notes        R2 를 쥐여 줄지. 익명 평가에서는 끈다
    stats        R3 를 쥐여 줄지
    stat_as_of   R3 만의 경계. 평가에서 대상 패치로 준다 — 아래 참조
    fixed_cases  주면 R1 은 **조정된 사례 이 수만큼으로 고정**된다(B5 는 25)

    ## R3 의 경계를 따로 두는 이유

    B5·B6 에 맞춰 경계를 분할점(15_13)에 고정하면 R3 의 「과거 지표」가 대상보다
    **중앙값 12패치 · 최대 24패치 전** 것이 된다. 첫 평가에서 기권 82건 중 60건이
    R3 를 부른 건이었다. 대상 패치 직전까지 열어도 누출은 없다 — 그 행들의 라벨은
    그 시점에 이미 공개된 결과다. 다만 B5·B6 이 못 본 정보이므로 **따로 적는다.**
    """
    stat = StatLookup(corpus.rows, stat_as_of or as_of)
    search_notes = NoteSearch(corpus.blocks, as_of)
    pools = {
        # ① 대상 — 전체 행. 「비슷했던 챔피언 중 몇이 조정됐나」
        "all": CaseSearch(corpus.rows, as_of),
        # ② 방향 — 조정됐고 방향이 분명한 행만. B5 와 같은 풀이다
        "adjusted": CaseSearch(direction_rows(corpus.rows), as_of),
    }
    anon = alias is not None
    query_patch = target.patch if target is not None else as_of
    boundary = f"(경계: {as_of} 이전 기록만)"
    stat_boundary = f"(경계: {stat_as_of or as_of} 이전 기록만)"

    def resolve(champion: str) -> tuple[PanelRow | None, str, str]:
        """(행, 부를 이름, 실패 메시지). 대소문자·공백이 달라도 받는다."""
        wanted = champion.strip().lower()
        if target is not None and wanted in {
            "대상",
            (alias or target.champion).lower(),
        }:
            return target, alias or target.champion, ""
        if anon:
            return None, "", f"익명 조건에서는 대상({alias})만 조회한다."
        for row in corpus.rows:
            if row.patch == query_patch and row.champion.lower() == wanted:
                return row, row.champion, ""
        near = [
            r.champion
            for r in corpus.rows
            if r.patch == query_patch
            and wanted[:3]
            and wanted[:3] in r.champion.lower()
        ]
        hint = f" 비슷한 이름: {', '.join(near[:5])}" if near else ""
        return None, "", f"{champion} 은 {query_patch} 에 없다.{hint}"

    @tool
    def lookup_stats(champion: str) -> str:
        """R3 수치 조회. 한 챔피언의 과거 패치별 솔랭 지표(승률·픽률·밴율·판수)와
        각 패치 다음에 실제로 일어난 일(너프·버프·조정 안 됨)을 준다.
        대상의 추세를 볼 때 쓴다. champion 은 대상 이름(익명이면 대상의 키)이다."""
        row, label, fail = resolve(champion)
        if row is None:
            return fail
        history = stat.champion(row.champion)[-8:]
        if not history:
            return f"{label} 의 과거 기록이 없다 {stat_boundary}"
        lines = [
            f"{label} 과거 지표 {stat_boundary}",
            "패치       승률   픽률   밴율      판수  → 다음 패치",
        ]
        for r in history:
            # 익명이면 패치 이름 대신 **대상에서 몇 패치 전인지**를 준다. 추세는
            # 살고 정체의 단서는 준다. 경계가 낡았으면 「22패치 전」이 그대로 보인다.
            when = (
                f"{patch_index(row.patch) - patch_index(r.patch)}패치 전"
                if anon
                else r.patch
            )
            lines.append(
                f"{when:10}{_pct(r.win_rate)}  {_pct(r.pick_rate)}  "
                f"{_pct(r.ban_rate)}  {r.matches:>9,}  → {_outcome(r)}"
            )
        return "\n".join(lines)

    def similar(champion: str, among: str, k: int) -> str:
        row, label, fail = resolve(champion)
        if row is None:
            return fail
        if among not in pools:
            return 'among 은 "all" 또는 "adjusted" 다.'
        cases = pools[among].similar(row, k=max(1, min(int(k), MAX_CASES)))
        if not cases:
            return f"비슷한 사례가 없다 {boundary}"

        adjusted = [c for c in cases if c.row.adjusted_next]
        nerf = sum(1 for c in cases if c.row.direction_next == "nerf")
        buff = sum(1 for c in cases if c.row.direction_next == "buff")
        lines = [
            f"{label} 과 닮은 사례 {len(cases)}종 {boundary} — "
            f"조정 {len(adjusted)} (너프 {nerf} · 버프 {buff}) · 조정 안 됨 {len(cases) - len(adjusted)}"
        ]
        if anon:
            # B6 이 한 대로 **이름·패치를 가린다.** 같은 챔피언의 과거가 이웃으로
            # 나오면 이름이 곧 정체다. 패치를 가리는 것도 같은 이유다.
            lines.append("   승률   픽률   밴율   격차   거리  → 결과")
            for c in cases:
                r = c.row
                lines.append(
                    f"  {_pct(r.win_rate)}  {_pct(r.pick_rate)}  {_pct(r.ban_rate)}  "
                    f"{abs(r.win_rate - 0.5):5.1%}  {c.distance:4.2f}  → {_outcome(r)}"
                )
        else:
            lines.append("패치     챔피언          승률   밴율   거리  → 결과")
            # 패치 순으로 준다. 시간이 뒤섞이면 흐름이 안 보인다 (`ask` 와 같다).
            for c in sorted(cases, key=lambda c: c.row.patch_index):
                lines.append(
                    f"{c.row.patch:8}{c.row.champion:15}{_pct(c.row.win_rate)}  "
                    f"{_pct(c.row.ban_rate)}  {c.distance:4.2f}  → {_outcome(c.row)}"
                )
        return "\n".join(lines)

    if fixed_cases is None:

        @tool
        def find_similar_cases(champion: str, among: str = "all", k: int = 10) -> str:
            """R1 사례 검색. 대상과 지표(승률·픽률·밴율·승률 격차)가 비슷했던 과거
            사례와, 그 사례들의 다음 패치 결과를 준다.
            among="all"      전체 사례 — 「비슷했던 챔피언 중 몇이 조정됐나」를 볼 때
            among="adjusted" 조정된 사례만 — 「조정됐다면 너프였나 버프였나」를 볼 때
            k 는 가져올 사례 수 (최대 25)."""
            return similar(champion, among, k)

    else:
        # **증거의 양을 모델이 정하지 않게 한다.** 첫 평가에서 에이전트는 풀은
        # 맞게 골랐지만(24건 중 23건 adjusted) 사례를 5건만 보는 일이 가장 잦았다.
        # B5 는 25건을 본다. 같은 증거 위에서 판단만 견주려면 고정해야 한다.
        n = fixed_cases

        @tool
        def find_similar_cases(champion: str) -> str:
            """R1 사례 검색. 대상과 지표(승률·픽률·밴율·승률 격차)가 비슷했던 과거
            사례 중 **조정된 것**과, 그 다음 패치에 너프였는지 버프였는지를 준다."""
            return similar(champion, "adjusted", n)

    @tool
    def search_patch_notes(champion: str, patch: str = "") -> str:
        """R2 노트 검색. 패치 노트 원문에서 이 챔피언의 변경 내용(수치 전후)을 찾는다.
        patch 를 비우면 이 챔피언이 과거에 조정됐던 패치들의 노트를 최근 4건까지 준다.
        patch 를 주면 (예: "16_10") 그 패치의 노트만 찾는다.
        「과거에 무엇을 얼마나 바꿨나」를 볼 때 쓴다."""
        row, label, fail = resolve(champion)
        if row is None:
            return fail
        name = row.champion

        if patch.strip():
            wanted = [patch.strip().replace(".", "_")]
        else:
            # **이름만으로 묻지 않는다.** 스킨 버그 수정이 최상위로 올라온다
            # (실측: 「Senna」 → Bewitching Senna · High Noon Senna).
            # 실제로 조정된 패치로 좁혀 「이름 + 패치」로 묻는다 — `ask` 와 같다.
            wanted = [
                n
                for r in stat.champion(name)
                if r.adjusted_next and (n := next_patch(r.patch))
            ][-4:]
        if not wanted:
            return f"{label} 은 과거에 조정된 기록이 없다 {boundary}"

        first = name.split()[0].lower()
        out = [f"{label} 패치 노트 {boundary}"]
        for p in wanted:
            if p not in corpus.blocks and patch.strip():
                out.append(f"  {p}: 그 패치 노트가 없다")
                continue
            # **검색 결과의 패치를 확인한다.** 원하던 패치가 색인에 없으면 BM25 가
            # 다른 패치의 블록을 1위로 올린다 — `ask` 가 한때 거기에 원하던 패치
            # 이름표를 붙여 찍었다. 스킨 절도 거른다.
            hits = [
                (hp, b)
                for hp, b, _ in search_notes.search(f"{name} {p}", k=8)
                if hp == p and b.champion == name and first not in b.section.lower()
            ]
            if not hits:
                reason = (
                    "경계 밖"
                    if patch_index_safe(p) >= patch_index(as_of)
                    else "해당 절 없음"
                )
                out.append(f"  {p}: 찾지 못함 ({reason})")
                continue
            for hp, block in hits[:2]:
                out.append(f"  [{hp}] {block.section}")
                out.extend(f"      {line}" for line in block.lines[:6])
        return "\n".join(out)

    tools: list[BaseTool] = [find_similar_cases]
    if stats:
        tools.insert(0, lookup_stats)
    if notes:
        tools.append(search_patch_notes)
    return tools
