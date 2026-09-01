"""개발사 노트에서 이유를 뽑는 것 — **여기서 지키는 것은 둘이다.**

    이유가 없으면 빈 문자열   있는 척하지 않는다
    챔피언이 아닌 블록도 낸다  거르는 것은 부르는 쪽 몫이다

그리고 **버전 이름이 출처마다 다르다** — 우리 `16.13` 이 개발사 사이트에서는
`26.13` 이고, 주소 형식도 두 가지가 섞여 있다.
"""

from __future__ import annotations

from lol_balance.riotnotes import parse_rationale, rationale_by_patch, slugs


def block(title: str, quote: str | None) -> str:
    q = (
        f'<blockquote class="blockquote context"><p>{quote}</p></blockquote>'
        if quote
        else ""
    )
    return (
        f'<div class="patch-change-block white-stone">'
        f'<div><p><a><img/></a></p><h3 class="change-title">{title}</h3>{q}'
        f'<h4 class="change-detail-title">Base Stats</h4>'
        f"<ul><li><strong>Health</strong>: 104 ⇒ <strong>98</strong></li></ul></div></div>"
    )


def test_the_reason_is_pulled_out_of_the_blockquote() -> None:
    """**이유는 `blockquote.context` 에 있다.** 수치 앞, 이름 뒤다."""
    html = block("Cassiopeia", "She has risen to the top of pro play presence.")

    got = parse_rationale(html)

    assert len(got) == 1
    assert got[0].title == "Cassiopeia"
    assert "pro play presence" in got[0].text


def test_a_block_without_a_reason_gets_an_empty_string() -> None:
    """**모르는 것을 지어내지 않는다.** 수치만 있는 챔피언이 실제로 있다."""
    got = parse_rationale(block("Vex", None))

    assert got[0].title == "Vex"
    assert got[0].text == ""


def test_blocks_that_are_not_champions_come_through_too() -> None:
    """`RANKED REWARDS` · 아이템 · 룬에도 같은 블록이 붙는다.

    **여기서 거르지 않는다** — 챔피언 목록을 아는 것은 부르는 쪽이고, 여기서
    걸러 버리면 나중에 아이템 이유를 쓰고 싶을 때 다시 파싱해야 한다.
    """
    html = block("Naafiri", "Join the hunt.") + block(
        "RANKED REWARDS", "Rewards go out."
    )

    titles = [r.title for r in parse_rationale(html)]

    assert titles == ["Naafiri", "RANKED REWARDS"]


def test_a_page_with_no_blocks_is_empty_not_an_error() -> None:
    assert parse_rationale("<html><body><p>없다</p></body></html>") == ()


def test_the_site_calls_2025_and_later_by_a_different_number() -> None:
    """**우리 `16.13` 이 개발사 사이트에서는 `26.13` 이다.**

    `patchnotes.wiki_version` 이 이미 그 매핑을 한다. 여기서 다시 만들지 않는다.
    """
    assert slugs("16.13.1")[0] == "patch-26-13-notes"
    assert slugs("13.14.1")[0] == "patch-13-14-notes"


def test_both_zero_paddings_are_tried() -> None:
    """**앞의 0 을 남기는 주소와 떼는 주소가 둘 다 쓰인다.**

    2025년은 `patch-25-05-notes` 이고 2026년은 `patch-26-5-notes` 다. 한쪽만
    시도하면 22패치가 빈다 — 실제로 그렇게 비었다.
    """
    got = slugs("15.3.1")

    assert "patch-25-03-notes" in got
    assert "patch-25-3-notes" in got


def test_both_address_shapes_are_tried() -> None:
    """`patch-…` 와 `league-of-legends-patch-…` 가 섞여 있다."""
    got = slugs("16.13.1")

    assert "patch-26-13-notes" in got
    assert "league-of-legends-patch-26-13-notes" in got


def test_only_blocks_whose_title_is_a_champion_are_kept(tmp_path) -> None:
    """`RANKED REWARDS` · 아이템 블록은 챔피언 목록에서 떨어진다."""
    html = block("Naafiri", "Join the hunt.") + block(
        "RANKED REWARDS", "Rewards go out."
    )
    (tmp_path / "13.14.1.html").write_text(html, encoding="utf-8")

    got = rationale_by_patch(tmp_path, ["Naafiri", "Ahri"])

    assert got == {("13_14", "Naafiri"): "Join the hunt."}


def test_the_file_name_is_the_data_dragon_version(tmp_path) -> None:
    """본문은 `26.13` 이라 부르는데 **파일 이름은 우리 표기 `16.13.1`** 이다."""
    (tmp_path / "16.13.1.html").write_text(
        block("Ahri", "Too strong."), encoding="utf-8"
    )

    assert ("16_13", "Ahri") in rationale_by_patch(tmp_path, ["Ahri"])
