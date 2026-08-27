"""검색 맥락과 판단 파일 테스트.

**여기서 지키는 것은 둘이다.**

    익명   맥락과 판단 파일 어디에도 챔피언 이름이 없어야 한다
    경계   `as_of` 이후 사례가 맥락에 섞이면 안 된다

둘 중 하나가 무너지면 `B6` 결과를 통째로 못 쓴다. 근거는
[ADR 0006](../docs/adr/0006-rag-generation-and-contamination-control.md).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import PanelRowFactory
from lol_balance.patchnotes import ChangeBlock
from lol_balance.ragjudge import anon_key, build, read_judgments, render
from lol_balance.retrieval import Case, NoteSearch


def test_key_is_stable_and_hides_the_champion(make_row: PanelRowFactory) -> None:
    """**키가 이름을 되돌려 주면 익명이 아니다.**

    판단 파일이 저장소에 남으므로, 그 파일만 봐서 대상을 알 수 있으면 다음
    회차가 오염된다.
    """
    row = make_row("15_13", 13, champion="Ryze")
    key = anon_key(row)

    assert anon_key(row) == key
    assert "Ryze" not in key
    assert anon_key(make_row("15_14", 13, champion="Ryze")) != key
    assert anon_key(make_row("15_13", 99, champion="Ryze")) != key


def test_anon_block_never_names_the_champion(make_row: PanelRowFactory) -> None:
    """익명 조건은 수치만 준다. **이름이 새면 기억이 발동한다.**"""
    target = make_row("15_13", 13, champion="Ryze", win_rate=0.48)
    neighbour = make_row("13_14", 103, champion="Ahri", direction_next="nerf")

    text = render(target, "anon", (Case(neighbour, 0.1),))

    assert "Ryze" not in text
    assert "Ahri" not in text
    assert "0.480" in text
    assert "nerf" in text


def test_named_block_gives_the_name_and_nothing_else(
    make_row: PanelRowFactory,
) -> None:
    """**오염 상한을 재는 조건이다.** 수치나 사례가 새면 상한이 아니게 된다."""
    target = make_row("15_13", 13, champion="Ryze", win_rate=0.483, pick_rate=0.034)

    text = render(target, "named")

    assert "Ryze" in text
    assert "15_13" in text
    assert "0.483" not in text
    assert "0.034" not in text


def test_context_never_reaches_past_the_split(make_row: PanelRowFactory) -> None:
    """**경계는 검색기가 지킨다.** 여기서 빠뜨려도 미래가 안 샌다."""
    target = make_row("15_13", 13, champion="Ryze")
    pool = (
        make_row("13_14", 103, champion="Ahri", win_rate=0.53, direction_next="nerf"),
        make_row("15_14", 222, champion="Jinx", win_rate=0.999, direction_next="buff"),
    )

    text = build((target,), pool, "15_13", "anon")

    assert "0.999" not in text  # 15_14 는 분할점 이후다
    assert "1종 중 방향이 붙은 것 1종" in text


def test_only_directed_neighbours_are_shown(make_row: PanelRowFactory) -> None:
    """`B5` 의 투표도 `nerf`/`buff` 만 센다. **같은 재료여야 비교가 된다.**

    다만 「몇 종 중 몇 종」은 그 자체가 맥락이라 수는 남긴다.
    """
    target = make_row("15_13", 13, champion="Ryze")
    cases = (
        Case(make_row("13_14", 1, direction_next="nerf"), 0.1),
        Case(make_row("13_15", 2, direction_next=None), 0.2),
        Case(make_row("13_16", 3, direction_next="adjust"), 0.3),
    )

    text = render(target, "anon", cases)

    assert "3종 중 방향이 붙은 것 1종" in text
    assert "adjust" not in text


def test_notes_come_from_the_patch_the_neighbour_was_adjusted_in(
    make_row: PanelRowFactory,
) -> None:
    """**이름만으로 질의하면 스킨 이름 변경이 최상위로 올라온다.** 실제로 그랬다.

    이웃이 `13_14` 상태였다면 조정은 `13_15` 에 일어났다. 그 패치의 내용을
    집어야 판단에 쓸모가 있다.
    """
    target = make_row("15_13", 13, champion="Ryze")
    neighbour = make_row("13_14", 103, champion="Ahri", direction_next="nerf")
    blocks = {
        "13_14": (
            ChangeBlock("Ahri", "Worlds Ahri", None, ("Skin renamed to Worlds Ahri.",)),
        ),
        "13_15": (ChangeBlock("Ahri", "Q", "Q", ("Base damage reduced to 40.",)),),
    }
    notes = NoteSearch(blocks, as_of="15_13")

    text = render(target, "anon", (Case(neighbour, 0.1),), notes)

    assert "Base damage reduced to 40." in text
    assert "Skin renamed" not in text


def test_notes_stop_at_the_boundary(make_row: PanelRowFactory) -> None:
    """이웃의 조정 패치가 분할점 밖이면 **아무것도 안 붙는다.**

    `NoteSearch` 가 색인 자체를 자르므로 여기서 따로 막지 않아도 된다.
    """
    target = make_row("15_13", 13, champion="Ryze")
    neighbour = make_row("15_12", 103, champion="Ahri", direction_next="nerf")
    blocks = {"15_13": (ChangeBlock("Ahri", "Q", "Q", ("Base damage reduced.",)),)}
    notes = NoteSearch(blocks, as_of="15_13")

    text = render(target, "anon", (Case(neighbour, 0.1),), notes)

    assert "실제로 조정된 내용" not in text


def test_notes_are_skipped_when_there_is_no_retriever(
    make_row: PanelRowFactory,
) -> None:
    """`named` 조건이나 노트 파일이 없을 때 죽지 않아야 한다."""
    target = make_row("15_13", 13, champion="Ryze")
    neighbour = make_row("13_14", 103, champion="Ahri", direction_next="nerf")

    text = render(target, "anon", (Case(neighbour, 0.1),), None)

    assert "실제로 조정된 내용" not in text
    assert "nerf" in text


def write(path: Path, records: list[dict]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "j.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8",
    )


def good(**over: object) -> dict:
    base = {
        "key": "abc123",
        "condition": "anon",
        "nerf_prob": 70,
        "reason": "승률 높다",
    }
    return base | over


def test_judgments_round_trip(tmp_path: Path) -> None:
    write(tmp_path, [good(), good(key="def456", nerf_prob=20)])

    out = read_judgments(tmp_path)

    assert out[("abc123", "anon")].nerf_prob == 70
    assert out[("def456", "anon")].reason == "승률 높다"


def test_a_missing_directory_is_not_a_crash(tmp_path: Path) -> None:
    assert read_judgments(tmp_path / "없다") == {}


@pytest.mark.parametrize(
    ("record", "message"),
    [
        (good(nerf_prob=101), "0~100"),
        (good(nerf_prob=-1), "0~100"),
        (good(nerf_prob="70"), "0~100"),
        (good(nerf_prob=True), "0~100"),
        (good(reason="  "), "reason"),
        (good(condition="whatever"), "condition"),
    ],
)
def test_bad_records_fail_loudly(tmp_path: Path, record: dict, message: str) -> None:
    """**조용히 넘기면 314건을 다 만든 뒤에 안다.**

    특히 `nerf_prob` 이 문자열이나 `bool` 로 오는 것을 잡아야 한다 — 파이썬에서
    `True` 는 `int` 의 부분형이라 범위 검사만으로는 통과한다.
    """
    write(tmp_path, [record])

    with pytest.raises(ValueError, match=message):
        read_judgments(tmp_path)


def test_the_same_target_twice_is_an_error(tmp_path: Path) -> None:
    """여러 턴에 걸쳐 붙이므로 **같은 것을 두 번 판단하기 쉽다.**"""
    write(tmp_path, [good(), good(nerf_prob=30)])

    with pytest.raises(ValueError, match="두 번"):
        read_judgments(tmp_path)


def test_the_same_target_in_two_conditions_is_fine(tmp_path: Path) -> None:
    """조건이 다르면 같은 대상이 두 번 나오는 것이 정상이다."""
    write(tmp_path, [good(), good(condition="named", nerf_prob=55)])

    out = read_judgments(tmp_path)

    assert len(out) == 2


# --- ADR 0007 · 답변 형식 -------------------------------------------------
#
# **새 칸은 전부 선택이다.** 필수로 만들면 이미 만든 판단 314건이 통째로 깨지고,
# 그것만 여덟 묶음이 걸렸다. 그래서 「없으면 기본값」이 계약이다.


def test_the_old_shape_still_reads(tmp_path: Path) -> None:
    """**기존 314건이 그대로 유효해야 한다.** 이것이 스키마를 넓힌 전제다."""
    write(tmp_path, [good()])

    j = read_judgments(tmp_path)[("abc123", "anon")]

    assert j.as_of is None
    assert j.abstain is False
    assert j.adjust_prob is None
    assert j.evidence == ()
    assert j.warnings == ()


def test_the_new_fields_round_trip(tmp_path: Path) -> None:
    write(
        tmp_path,
        [
            good(
                as_of="16_13",
                abstain=False,
                adjust_prob=62,
                evidence=[{"source": "R1", "text": "이웃 25종 중 너프 24"}],
                warnings=[{"kind": "missing_data", "text": "프로 데이터가 없다"}],
            )
        ],
    )

    j = read_judgments(tmp_path)[("abc123", "anon")]

    assert j.as_of == "16_13"
    assert j.adjust_prob == 62
    assert j.evidence[0].source == "R1"
    assert j.warnings[0].kind == "missing_data"


def test_evidence_without_a_known_source_is_rejected(tmp_path: Path) -> None:
    """**근거는 지어내지 않는다를 형식으로 건다.**

    어느 검색기가 준 것인지 안 적으면 「`R2` 가 스킨 버그를 물어 오는 약점이
    판단에 얼마나 번지나」를 영영 못 잰다.
    """
    write(tmp_path, [good(evidence=[{"source": "어디선가", "text": "세다"}])])

    with pytest.raises(ValueError, match="source"):
        read_judgments(tmp_path)


@pytest.mark.parametrize(
    ("record", "message"),
    [
        (good(adjust_prob=101), "0~100"),
        (good(adjust_prob=True), "정수"),
        (good(adjust_prob="62"), "정수"),
        (good(abstain="false"), "abstain"),
        (good(evidence={"source": "R1"}), "배열"),
        (good(evidence=[{"source": "R1", "text": "  "}]), "text"),
        (good(warnings=[{"kind": "", "text": "무엇"}]), "kind"),
    ],
)
def test_bad_new_fields_fail_loudly(tmp_path: Path, record: dict, message: str) -> None:
    """`abstain="false"` 가 특히 위험하다 — **비지 않은 문자열은 전부 참이다.**"""
    write(tmp_path, [record])

    with pytest.raises(ValueError, match=message):
        read_judgments(tmp_path)


def test_abstain_is_carried_so_coverage_can_be_counted(tmp_path: Path) -> None:
    """**「모른다」를 말할 자리가 없으면 억지로 답한다.**

    지금까지는 확신 없는 판단도 반드시 0~100 안에 들어가, 「이웃이 0종이라
    검색이 아무 말도 못 한다」가 50 으로 적혀 「반반이다」와 구별이 안 됐다.
    """
    write(tmp_path, [good(abstain=True), good(key="def456")])

    out = read_judgments(tmp_path)

    assert [k for k, v in out.items() if v.abstain] == [("abc123", "anon")]
