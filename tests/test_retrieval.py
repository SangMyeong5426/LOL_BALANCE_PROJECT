"""검색기 테스트.

**가장 중요한 것은 경계다.** 에이전트가 예측 대상 패치 이후를 읽으면 정답을
그냥 본 것이 된다. 프롬프트 지시가 아니라 도구가 물리적으로 막아야 한다.
"""

from __future__ import annotations

import pytest

from conftest import PanelRowFactory
from lol_balance.panel import PanelRow
from lol_balance.patchnotes import ChangeBlock
from lol_balance.retrieval import CaseSearch, NoteSearch, StatLookup, _tokens


@pytest.fixture
def rows(make_row: PanelRowFactory) -> tuple[PanelRow, ...]:
    return (
        make_row(
            "13_14",
            1,
            champion="Ahri",
            win_rate=0.53,
            pick_rate=0.09,
            adjusted_next=True,
            direction_next="nerf",
        ),
        make_row(
            "13_15",
            2,
            champion="Zed",
            win_rate=0.46,
            pick_rate=0.02,
            adjusted_next=True,
            direction_next="buff",
        ),
        make_row("15_1", 3, champion="Lux", win_rate=0.53, pick_rate=0.09),
        make_row("16_1", 4, champion="Jinx", win_rate=0.50, pick_rate=0.05),
    )


# --- 경계 -------------------------------------------------------------


def test_case_search_cannot_reach_past_the_boundary(rows: tuple[PanelRow, ...]) -> None:
    search = CaseSearch(rows, as_of="15_1")

    assert {r.patch for r in search.pool} == {"13_14", "13_15"}


def test_stat_lookup_cannot_reach_past_the_boundary(rows: tuple[PanelRow, ...]) -> None:
    lookup = StatLookup(rows, as_of="15_1")

    assert lookup.champion("Lux") == ()
    assert lookup.patch("16_1") == ()
    assert len(lookup.champion("Ahri")) == 1


def test_note_search_cannot_reach_past_the_boundary() -> None:
    blocks = {
        "13_14": (ChangeBlock("Ahri", "Q", "Q", ("Base damage reduced.",)),),
        "16_1": (ChangeBlock("Ahri", "Q", "Q", ("Base damage reduced.",)),),
    }
    search = NoteSearch(blocks, as_of="15_1")

    assert {patch for patch, _, _ in search.search("damage", k=10)} == {"13_14"}


def test_the_boundary_is_fixed_at_construction(rows: tuple[PanelRow, ...]) -> None:
    """부르는 쪽이 경계를 넘기지 않는다 — 도구가 태어날 때 박힌다."""
    search = CaseSearch(rows, as_of="13_15")

    target = next(r for r in rows if r.champion == "Lux")
    assert {c.row.patch for c in search.similar(target, k=10)} == {"13_14"}


# --- 사례 검색 --------------------------------------------------------


def test_similar_cases_come_back_nearest_first(
    rows: tuple, make_row: PanelRowFactory
) -> None:
    search = CaseSearch(rows, as_of="16_1")
    target = make_row("16_1", 9, champion="New", win_rate=0.53, pick_rate=0.09)

    found = search.similar(target, k=3)

    assert found[0].row.champion == "Ahri"  # 같은 승률·픽률
    assert found[0].distance <= found[1].distance


def test_a_case_carries_what_happened_next(
    rows: tuple, make_row: PanelRowFactory
) -> None:
    """「비슷했던 챔피언에게 무슨 일이 있었나」가 이 도구의 요점이다."""
    search = CaseSearch(rows, as_of="16_1")
    target = make_row("16_1", 9, champion="New", win_rate=0.53, pick_rate=0.09)

    assert search.similar(target, k=1)[0].outcome == "nerf"


def test_a_champion_never_matches_its_own_row(rows: tuple[PanelRow, ...]) -> None:
    search = CaseSearch(rows, as_of="16_1")
    target = next(r for r in rows if r.champion == "Ahri")

    assert all(c.row.champion != "Ahri" for c in search.similar(target, k=5))


def test_rows_with_no_comparable_feature_are_skipped(make_row: PanelRowFactory) -> None:
    """결측을 0 으로 채우면 「평균값이었다」가 되어 엉뚱한 사례가 가까워진다."""
    pool = (make_row("13_14", 1, ban_rate=None, win_rate=0.5, pick_rate=0.05),)
    search = CaseSearch(pool, as_of="15_1")
    target = make_row("15_1", 2, ban_rate=0.1, win_rate=0.5, pick_rate=0.05)

    # 승률·픽률·wr_gap 은 맞대 볼 수 있으므로 거리가 나온다
    assert len(search.similar(target, k=5)) == 1


# --- 노트 검색 --------------------------------------------------------


def test_note_search_ranks_by_relevance() -> None:
    blocks = {
        "13_14": (
            ChangeBlock("Ahri", "Q", "Q", ("Ahri base damage reduced heavily.",)),
            ChangeBlock("Zed", "W", "W", ("Cooldown increased.",)),
        )
    }
    found = NoteSearch(blocks, as_of="15_1").search("ahri damage", k=2)

    assert found[0][1].champion == "Ahri"


def test_note_search_returns_nothing_when_no_term_matches() -> None:
    blocks = {"13_14": (ChangeBlock("Ahri", "Q", "Q", ("Cooldown increased.",)),)}

    assert NoteSearch(blocks, as_of="15_1").search("mana") == ()


# --- 수치 조회 --------------------------------------------------------


def test_stat_lookup_returns_a_champion_in_time_order(
    rows: tuple, make_row: PanelRowFactory
) -> None:
    extra = (*rows, make_row("14_1", 1, champion="Ahri", win_rate=0.51))
    lookup = StatLookup(extra, as_of="15_1")

    assert [r.patch for r in lookup.champion("Ahri")] == ["13_14", "14_1"]


def test_stat_lookup_orders_a_patch_by_pick_rate(make_row: PanelRowFactory) -> None:
    pool = (
        make_row("13_14", 1, champion="Low", pick_rate=0.01),
        make_row("13_14", 2, champion="High", pick_rate=0.20),
    )
    lookup = StatLookup(pool, as_of="15_1")

    assert [r.champion for r in lookup.patch("13_14")] == ["High", "Low"]


# --- 묶어서 검색하기 ----------------------------------------------------
#
# **`similar_many` 는 빠르기만 한 것이 아니라 같아야 한다.** ① 대상은 풀
# 5,334행에 질의 3,433건이라 한 건씩 돌면 리포트 82초 중 74초를 여기서 쓴다.
# 묶어서 계산하면 10배 빨라지는데, 결과가 한 칸이라도 달라지면 arm 성적이
# 바뀌므로 그것은 최적화가 아니라 결함이다.


def keys(cases: tuple) -> list[tuple[int, str]]:
    return [(c.row.champion_id, c.row.patch) for c in cases]


def test_many_matches_one_at_a_time(make_row: PanelRowFactory) -> None:
    """순서까지 같아야 한다. **동점이 갈리는 자리가 위험하다.**"""
    pool = tuple(
        make_row(patch, champion_id, win_rate=0.50 + (champion_id % 5) * 0.004)
        for patch in ("13_14", "13_15", "13_16")
        for champion_id in range(1, 12)
    )
    search = CaseSearch(pool, "13_17")
    targets = [make_row("13_17", i, win_rate=0.505) for i in range(1, 6)]

    one = [search.similar(t, k=7) for t in targets]
    many = search.similar_many(targets, k=7)

    assert [keys(c) for c in many] == [keys(c) for c in one]


def test_ties_break_the_same_way(make_row: PanelRowFactory) -> None:
    """거리가 같으면 `(patch_index, champion_id)` 순이다. 풀을 미리 그 순으로
    정렬해 두고 거리만 **안정 정렬**해야 이것이 재현된다."""
    pool = tuple(
        make_row(patch, champion_id, win_rate=0.50)
        for patch in ("13_14", "13_15")
        for champion_id in (9, 3, 7, 1)
    )
    search = CaseSearch(pool, "13_16")
    target = make_row("13_16", 99, win_rate=0.50)

    assert keys(search.similar_many([target], k=8)[0]) == keys(
        search.similar(target, 8)
    )


def test_a_champion_never_matches_itself_in_batch(make_row: PanelRowFactory) -> None:
    pool = tuple(make_row(p, 5) for p in ("13_14", "13_15"))
    search = CaseSearch(pool, "13_16")
    target = pool[0]

    got = search.similar_many([target], k=5)[0]

    assert all(c.row.patch != target.patch for c in got)


def test_rows_with_no_comparable_feature_are_skipped_in_batch(
    make_row: PanelRowFactory,
) -> None:
    """**결측을 0 으로 채우면 「평균값이었다」가 되어 엉뚱한 사례가 가까워진다.**"""
    pool = (
        make_row("13_14", 1, ban_rate=None),
        make_row("13_14", 2, ban_rate=0.02),
    )
    search = CaseSearch(pool, "13_15", features=("ban_rate",))
    target = make_row("13_15", 3, ban_rate=0.02)

    got = search.similar_many([target], k=5)[0]
    one = search.similar(target, k=5)

    assert [c.row.champion_id for c in got] == [2]
    assert keys(got) == keys(one)


def test_an_empty_pool_gives_empty_results(make_row: PanelRowFactory) -> None:
    search = CaseSearch((), "13_15")

    assert search.similar_many([make_row("13_15", 1)], k=5) == ((),)


# --- 토큰 나누기 --------------------------------------------------------
#
# **한때 `[a-z]+` 였다.** 그래서 `16_13` 도 `reduced to 60 from 85` 의 60·85 도
# 통째로 사라졌고, 색인에 패치 번호가 없으니 **어느 패치의 Ahri 인지 원리적으로
# 못 가렸다.** 「챔피언 + 패치」로 그 블록을 찾는 과제에서 Recall@10 이 56.3%
# 였는데, 숫자를 살리고 패치를 색인에 넣으니 98.1% 가 됐다.


def test_numbers_survive_tokenising() -> None:
    """수치가 사라지면 「60 에서 85 로 바뀐 그 블록」을 못 찾는다."""
    assert _tokens("damage 60 from 85") == ["damage", "60", "from", "85"]


def test_a_patch_id_stays_one_word() -> None:
    """`16` 과 `13` 으로 쪼개면 아무 패치에나 걸린다."""
    assert _tokens("Ahri 16_13 cooldown") == ["ahri", "16_13", "cooldown"]


def test_a_decimal_stays_one_word() -> None:
    """`0.67` 이 `0` 과 `67` 이 되면 12.5 를 겪은 것과 같은 문제가 난다."""
    assert _tokens("reduced to 0.67 from 0.85.") == [
        "reduced",
        "to",
        "0.67",
        "from",
        "0.85",
    ]


def test_a_sentence_period_does_not_join_words() -> None:
    """마침표는 **숫자 사이에서만** 이어 붙인다."""
    assert _tokens("Cooldown increased. Damage reduced.") == [
        "cooldown",
        "increased",
        "damage",
        "reduced",
    ]


def test_the_patch_is_searchable() -> None:
    """색인 문서에 패치가 들어가야 질의로 가릴 수 있다.

    같은 챔피언이 여러 패치에 나오므로, 이것이 없으면 이름으로 찾은 뒤
    **바깥에서 패치로 좁히는 우회**밖에 없다.
    """
    blocks = {
        "13_14": (ChangeBlock("Ahri", "Q", "Q", ("Damage reduced.",)),),
        "13_15": (ChangeBlock("Ahri", "Q", "Q", ("Damage reduced.",)),),
    }
    found = NoteSearch(blocks, as_of="15_1").search("Ahri 13_15", k=1)

    assert found[0][0] == "13_15"


def test_a_long_block_that_matches_more_wins() -> None:
    """**길이 정규화가 정확 일치를 누르면 안 된다.**

    한때 `B=0.75` 였다. 그래서 「Heimerdinger 13_15」로 물으면 두 낱말을 다 맞힌
    68토큰짜리 `13_15` 블록(6.17)이 `heimerdinger` 하나만 맞힌 10토큰짜리 `16_3`
    블록(7.77)에 밀렸다. 놓친 질의 31건의 정답 블록이 유독 길었던 것도(중앙값
    75토큰 · 전체 20토큰) 같은 기전이다.

    **채우는 블록이 있어야 재현된다.** 문서가 둘뿐이면 챔피언 이름이 양쪽에
    있어 흔한 낱말이 되고, 실제 코퍼스의 관계(`heimerdinger` idf 5.44 >
    `13_15` 4.27)가 뒤집힌다. 길이 비도 실제에 맞춘다 — 정답 블록이 평균의
    두세 배일 때가 문제 되는 자리다.
    """
    filler = tuple(f"w{i} tweak" for i in range(10))
    patches: dict[str, list[ChangeBlock]] = {
        f"14_{i}": [
            ChangeBlock(f"Other{i}{j}", "Stats", None, filler) for j in range(5)
        ]
        for i in range(1, 12)
    }
    patches["13_15"] = [
        ChangeBlock("Ahri", "Q", None, tuple(f"value {i} changed" for i in range(20)))
    ]
    patches["14_3"].append(ChangeBlock("Ahri", "Stats", None, ("health up",)))

    top = NoteSearch(patches, as_of="16_15").search("Ahri 13_15", k=2)[0]

    assert top[0] == "13_15"
