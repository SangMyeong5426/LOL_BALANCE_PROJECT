"""Data Dragon 버전 → 라이엇 위키 문서 이름 변환 테스트.

**2025년부터 라이엇이 패치 번호를 연도 기반으로 바꿨는데 Data Dragon 은 옛
번호를 유지한다.** 그래서 같은 패치가 두 이름을 갖는다. 이 변환이 틀리면
승률과 패치 노트가 엉뚱하게 짝지어지고, 그것은 조용히 지나간다.
"""

from __future__ import annotations

import pytest

from lol_balance.patchnotes import champion_changes, wiki_url, wiki_version


@pytest.mark.parametrize(
    ("ddragon", "wiki"),
    [
        ("13.14.1", "V13.14"),
        ("13.24.1", "V13.24"),
        ("14.1.1", "V14.1"),
        ("14.13.1", "V14.13"),
        ("14.24.1", "V14.24"),
        # 여기서부터 +10 하고 0 을 채운다
        ("15.1.1", "V25.01"),
        ("15.3.1", "V25.03"),
        ("15.9.1", "V25.09"),
        ("15.10.1", "V25.10"),
        ("15.24.1", "V25.24"),
        ("16.1.1", "V26.01"),
        ("16.9.1", "V26.09"),
        ("16.15.1", "V26.15"),
    ],
)
def test_maps_ddragon_version_to_wiki_page(ddragon: str, wiki: str) -> None:
    assert wiki_version(ddragon) == wiki


def test_single_digit_minor_is_padded_only_after_the_rename() -> None:
    """14.1 은 `V14.1`, 15.1 은 `V25.01`. 규칙이 시점에 따라 갈린다."""
    assert wiki_version("14.1.1") == "V14.1"
    assert wiki_version("15.1.1") == "V25.01"


def test_url_is_built_from_the_wiki_name() -> None:
    assert wiki_url("16.15.1").endswith("/V26.15")


@pytest.mark.parametrize("bad", ["", "abc", "16", "v16.15"])
def test_rejects_a_non_version_string(bad: str) -> None:
    with pytest.raises(ValueError, match="버전 형식"):
        wiki_version(bad)


# --- 챔피언 변경 파싱 ---------------------------------------------------
#
# 실제 노트를 고정물로 커밋하지 않는다 — 한 장이 600 KB 다. 대신 **실제로
# 겪은 함정을 그대로 재현한 최소 HTML** 을 쓴다. 셋 다 한 번씩 잘못 뽑았다.

_SECTION = """
<div class="mw-heading"><h3>Champions</h3></div>
{body}
<div class="mw-heading"><h3>Items</h3></div>
<dl><dt><span data-champion="ShouldNotAppear"></span></dt></dl>
"""


def _page(body: str) -> bytes:
    return _SECTION.format(body=body).encode()


def test_champion_and_ability_come_from_data_attributes() -> None:
    """이름을 문장에서 짐작하지 않는다. 위키가 속성에 정확히 넣어 준다."""
    (block,) = champion_changes(
        _page(
            '<dl><dt><span data-champion="Bel\'Veth"></span></dt></dl>'
            '<ul><li><span data-ability="Royal Maelstrom"></span>'
            "<ul><li>Cooldown reduced to 20 from 22.</li></ul></li></ul>"
        )
    )
    assert block.champion == "Bel'Veth"
    assert block.ability == "Royal Maelstrom"
    assert block.lines == ("Cooldown reduced to 20 from 22.",)


def test_decimals_survive_the_small_tag() -> None:
    """`0.<small>67</small>` 을 공백으로 이으면 12.5 가 12 과 5 로 읽힌다."""
    (block,) = champion_changes(
        _page(
            '<dl><dt><span data-champion="Aatrox"></span></dt></dl>'
            "<ul><li>Stats<ul><li>Base attack speed reduced to "
            "0.<small>67</small> from 0.<small>85</small>.</li></ul></li></ul>"
        )
    )
    assert "0.67" in block.lines[0]
    assert "0.85" in block.lines[0]


def test_word_boundaries_survive_inline_spans() -> None:
    """반대로 아무것도 안 넣으면 `reduced to<span>60% AD</span>` 가 붙는다."""
    (block,) = champion_changes(
        _page(
            '<dl><dt><span data-champion="Aatrox"></span></dt></dl>'
            "<ul><li>Stats<ul><li>AD ratio increased to"
            "<span>60% AD</span>from<span>50% AD</span>.</li></ul></li></ul>"
        )
    )
    assert "to 60% AD from 50% AD" in block.lines[0]


def test_a_node_with_both_text_and_children_keeps_its_own_sentence() -> None:
    """Aatrox 에서 `First cast ...` 네 줄이 통째로 사라졌던 경우다.

    중간 노드가 자기 문장을 가지면서 하위 항목을 품는다. 잎만 모으면 빠진다.
    """
    (block,) = champion_changes(
        _page(
            '<dl><dt><span data-champion="Aatrox"></span></dt></dl>'
            '<ul><li><span data-ability="The Darkin Blade"></span><ul>'
            "<li>First cast base damage reduced to 10 from 30."
            "<ul><li>Second cast base damage reduced to 12 from 37.</li></ul>"
            "</li></ul></li></ul>"
        )
    )
    assert block.lines == (
        "First cast base damage reduced to 10 from 30.",
        "Second cast base damage reduced to 12 from 37.",
    )


def test_stops_at_the_next_section() -> None:
    """Items·Runes 는 이 프로젝트의 대상이 아니다."""
    blocks = champion_changes(
        _page(
            '<dl><dt><span data-champion="Ahri"></span></dt></dl>'
            "<ul><li>Stats<ul><li>Base health increased to 590 from 570.</li></ul></li></ul>"
        )
    )
    assert {b.champion for b in blocks} == {"Ahri"}


def test_returns_nothing_when_there_is_no_champion_section() -> None:
    assert (
        champion_changes(b"<html><body><p>no patch notes here</p></body></html>") == ()
    )
