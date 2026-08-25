"""arm 정의와 분할 테스트.

성적 자체는 데이터에 달렸으므로 여기서 재지 않는다. **분할이 시간순인가**,
**arm 목록이 온전한가**, **같은 시드에서 같은 결과가 나오는가**만 본다.
"""

from __future__ import annotations

import pytest

from conftest import PanelRowFactory
from lol_balance.arms import direction_arms, split, target_arms
from lol_balance.panel import PanelRow

SEED = 20260824


def _panel(make_row: PanelRowFactory) -> tuple[PanelRow, ...]:
    """학습·평가 양쪽에 너프와 버프가 들어가도록 만든다."""
    rows = []
    for i, patch in enumerate(("13_14", "13_15", "15_13", "15_14")):
        for champion in range(1, 13):
            direction = ("nerf", "buff")[champion % 2]
            adjusted = champion <= 8
            rows.append(
                make_row(
                    patch,
                    champion,
                    win_rate=0.50
                    + (0.01 if direction == "nerf" else -0.01)
                    + i * 0.001,
                    pick_rate=0.05 + champion * 0.001,
                    adjusted_next=adjusted,
                    direction_next=direction if adjusted else None,
                    direction_source="auto" if adjusted else None,
                )
            )
    return tuple(rows)


def test_split_is_by_time_not_string_order(make_row: PanelRowFactory) -> None:
    """`15_9` 가 `15_13` 뒤로 가면 미래가 학습으로 샌다."""
    rows = (make_row("13_14", 1), make_row("15_9", 1), make_row("15_13", 1))

    train, test = split(rows, "15_13")

    assert [r.patch for r in train] == ["13_14", "15_9"]
    assert [r.patch for r in test] == ["15_13"]


def test_target_arms_cover_every_baseline(make_row: PanelRowFactory) -> None:
    """arm 이 추가되면 여기서 걸린다 — 표에 조용히 끼거나 빠지면 안 된다."""
    results, meta = target_arms(_panel(make_row), "15_13", SEED)

    assert [r.arm for r in results] == [
        "A0",
        "A0b",
        "A0c",
        "A0d",
        "A1",
        "A2",
        "A3",
        "A5",
        "A5b",
    ]
    assert all(not r.uses_llm for r in results)  # A4 는 규칙을 넘겨야 나온다
    assert 0.0 < meta["기준선"] < 1.0


def test_direction_arms_only_see_adjusted_champions(make_row: PanelRowFactory) -> None:
    """조정 안 된 챔피언을 넣으면 두 과제가 뒤섞인다."""
    _, meta = direction_arms(_panel(make_row), "15_13", SEED)

    # 패치당 12종 중 8종만 조정됐고, 그중 절반이 너프다.
    assert meta["학습"] + meta["평가"] == 32


def test_same_seed_gives_the_same_numbers(make_row: PanelRowFactory) -> None:
    """시드가 같으면 결과가 같아야 한다. 아니면 비교표를 믿을 수 없다."""
    rows = _panel(make_row)
    first, _ = target_arms(rows, "15_13", SEED)
    second, _ = target_arms(rows, "15_13", SEED)

    assert [r.scores for r in first] == [r.scores for r in second]


def test_a_different_seed_moves_only_the_random_arm(make_row: PanelRowFactory) -> None:
    rows = _panel(make_row)
    first, _ = target_arms(rows, "15_13", SEED)
    other, _ = target_arms(rows, "15_13", SEED + 1)

    by_arm = dict(zip([r.arm for r in first], [r.scores for r in first], strict=True))
    moved = {r.arm for r in other if r.scores != by_arm[r.arm]}
    assert moved <= {"A0"}


def test_metrics_are_finite_where_they_are_defined(make_row: PanelRowFactory) -> None:
    results, _ = direction_arms(_panel(make_row), "15_13", SEED)

    auc = [r.scores["auc"] for r in results]
    assert all(0.0 <= value <= 1.0 for value in auc)
    assert pytest.approx(0.0, abs=1.0) == auc[0]


def test_retrieval_arms_appear_in_both_tables(make_row: PanelRowFactory) -> None:
    """A5·B5 는 사례 검색만으로 예측한다 — 모델 없이 이웃의 다수결이다."""
    rows = _panel(make_row)

    target, _ = target_arms(rows, "15_13", SEED)
    direction, _ = direction_arms(rows, "15_13", SEED)

    assert {r.arm for r in target} >= {"A5", "A5b"}
    assert {r.arm for r in direction} >= {"B5", "B5b"}


def test_retrieval_arms_are_not_marked_as_llm(make_row: PanelRowFactory) -> None:
    """검색은 통계다. LLM 이 관여하지 않는다."""
    target, _ = target_arms(_panel(make_row), "15_13", SEED)

    assert all(not r.uses_llm for r in target if r.arm.startswith("A5"))
