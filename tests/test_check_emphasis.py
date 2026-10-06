"""굵은 글씨 표시(`**`)가 GitHub 에서 닫히는지 보는 검사 — `scripts/check-emphasis`.

닫는 `**` 의 앞이 구두점이고 뒤가 바로 글자면 GitHub 은 닫는 표시로 보지 않는다.
한국어는 조사가 바로 붙어서 걸린다 — 2026-10-06 에 문서 12개에서 32곳을 고쳤다.
여기 예문은 그때 실제로 있던 문장이다.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def emphasis() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader(
        "check_emphasis", str(ROOT / "scripts" / "check-emphasis")
    )
    spec = importlib.util.spec_from_loader("check_emphasis", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_emphasis"] = module
    loader.exec_module(module)
    return module


def found(emphasis: ModuleType, text: str) -> list[str]:
    return [s.around for u in emphasis.units(text) for s in emphasis.strays(u)]


@pytest.mark.parametrize(
    "text",
    [
        "출처를 가리킬 때는 **「프로 경기」**다 — 우리 데이터의 대부분이",
        "패널 8,767행 중 **8,602행(98%)**에 프로 피처가 붙어 있다",
        "이 열이 재는 것은 **[두 지표가 어긋나는 크기](#두-지표가-어긋난다)**다.",
        "밴율은 **승률에 안 잡히는 「체감 강함」**을 담는 것으로 보인다.",
    ],
)
def test_a_letter_right_after_closing_punctuation_breaks_it(
    emphasis: ModuleType, text: str
) -> None:
    """닫는 표시 앞이 구두점이고 뒤가 글자면 안 닫힌다 — 여는 쪽도 짝을 잃는다."""
    assert len(found(emphasis, text)) == 2


@pytest.mark.parametrize(
    "text",
    [
        "출처를 가리킬 때는 「**프로 경기**」다 — 우리 데이터의 대부분이",
        "패널 8,767행 중 **8,602행**(98%)에 프로 피처가 붙어 있다",
        "이 열이 재는 것은 [**두 지표가 어긋나는 크기**](#두-지표가-어긋난다)다.",
        "밴율은 **승률에 안 잡히는 「체감 강함」을** 담는 것으로 보인다.",
    ],
)
def test_moving_the_punctuation_out_fixes_it(emphasis: ModuleType, text: str) -> None:
    """글자는 그대로 두고 표시만 옮기면 된다."""
    assert found(emphasis, text) == []


@pytest.mark.parametrize(
    "text",
    [
        "**한 번 더 받았다 (2026-09-21 16:40)**: `16_18` 에 플랫폼당 2,000판",
        "**그래서 한계를 없앴다.** 며칠을 못 돌았든",
        "화면은 **「근거로 본 노트 블록」** 을 답 아래에 붙인다",
        "- **`window_start`** — 한계 없이 마지막 경기부터",
    ],
)
def test_space_or_punctuation_after_the_closing_mark_is_fine(
    emphasis: ModuleType, text: str
) -> None:
    assert found(emphasis, text) == []


def test_an_opening_mark_between_a_letter_and_punctuation_breaks_it(
    emphasis: ModuleType,
) -> None:
    """여는 쪽도 같다 — 앞이 글자이고 뒤가 구두점이면 열지 못한다."""
    assert len(found(emphasis, "그것은**「같다」** 가 아니다")) == 2


def test_code_is_not_read(emphasis: ModuleType) -> None:
    assert found(emphasis, "`2 ** 3` 은 8 이고 ``a ** `b` ** c`` 도 그렇다") == []
    assert found(emphasis, "앞 문단\n\n```\n**「여기」**다\n```\n\n뒤 문단") == []


def test_a_backtick_does_not_end_a_code_span_it_did_not_open(
    emphasis: ModuleType,
) -> None:
    """코드 안에 역따옴표를 적으려다 코드가 일찍 끝나면 뒤의 굵은 글씨가 짝을 잃는다."""
    broken = '`re.sub(r"[\\`*_]", "", text)` 로 지운다. `_기울임_` 인데 **`13_14` 까지\n지운다.**'
    fixed = '``re.sub(r"[`*_]", "", text)`` 로 지운다. `_기울임_` 인데 **`13_14` 까지\n지운다.**'
    assert len(found(emphasis, broken)) == 1
    assert found(emphasis, fixed) == []


def test_bold_may_run_over_a_line_break_inside_a_paragraph(
    emphasis: ModuleType,
) -> None:
    assert (
        found(emphasis, "원인은 「**긁힌 것이 다른\n모집단이거나 조각이다**」이고")
        == []
    )


def test_a_blank_line_ends_the_paragraph(emphasis: ModuleType) -> None:
    assert len(found(emphasis, "**여기서 열고\n\n여기서 닫는다**")) == 2


def test_a_table_cell_is_read_on_its_own(emphasis: ModuleType) -> None:
    """표의 칸을 넘어서는 닫지 못한다. 코드 안의 `|` 는 칸을 가르지 않는다."""
    assert len(found(emphasis, "| **칸 하나 | 다른 칸** |")) == 2
    assert found(emphasis, "| **칸 하나** | `a | b` 와 **다른 칸** |") == []


def test_a_list_item_is_read_on_its_own(emphasis: ModuleType) -> None:
    assert len(found(emphasis, "- **첫 항목에서 열고\n- 둘째 항목에서 닫는다**")) == 2
    assert (
        found(emphasis, "- **첫 항목.** 설명이\n  다음 줄로 이어진다\n- **둘째 항목**")
        == []
    )


def test_escaped_stars_and_rules_are_not_marks(emphasis: ModuleType) -> None:
    assert found(emphasis, "별표 둘은 \\*\\* 로 적는다") == []
    assert found(emphasis, "앞 문단\n\n***\n\n뒤 문단") == []


def test_it_says_which_line(emphasis: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "doc.md"
    path.write_text(
        "# 제목\n\n첫 문단은 멀쩡하다.\n\n둘째 문단의\n셋째 줄이 **「깨진다」**고 한다.\n",
        encoding="utf-8",
    )
    assert [s.line for s in emphasis.scan(path)] == [6, 6]


def test_the_check_fails_on_a_broken_document_and_points_at_it(
    emphasis: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "good.md").write_text("**멀쩡하다.** 끝.\n", encoding="utf-8")
    monkeypatch.setattr(emphasis, "ROOT", tmp_path)
    assert emphasis.main() == 0

    (tmp_path / "docs" / "bad.md").write_text(
        "이것은 **「깨진다」**고.\n", encoding="utf-8"
    )
    assert emphasis.main() == 1
    assert "docs/bad.md:1" in capsys.readouterr().out


def test_outputs_and_raw_data_are_not_documents(
    emphasis: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`runs/` 에는 모델이 쓴 글이 있다. 커밋하지 않는 것은 보지 않는다."""
    for folder in ("runs", "data", ".pytest_cache"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "x.md").write_text("**「깨진다」**고.\n", encoding="utf-8")
    monkeypatch.setattr(emphasis, "ROOT", tmp_path)
    assert emphasis.main() == 0


def test_it_runs_without_git(
    emphasis: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """재현용 이미지에는 git 이 없다 — 그때는 폴더를 훑는다."""

    def no_git(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git")

    (tmp_path / "doc.md").write_text("이것은 **「깨진다」**고.\n", encoding="utf-8")
    monkeypatch.setattr(emphasis, "ROOT", tmp_path)
    monkeypatch.setattr(emphasis.subprocess, "run", no_git)
    assert emphasis.main() == 1
