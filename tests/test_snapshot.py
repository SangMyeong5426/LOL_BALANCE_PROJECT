"""고정 묶음 — 없는 묶음은 말이 되게 죽고, 있으면 ID 를 그대로 준다."""

from __future__ import annotations

import json

import pytest

from lol_balance import snapshot


def make(root, name="pin", *, ids=("KR_1", "KR_2"), dirty=False):
    (root / "data" / "snapshots" / name).mkdir(parents=True)
    (root / "data" / "snapshots" / name / "16_18.txt").write_text("\n".join(ids) + "\n")
    (root / "ground_truth" / "snapshots").mkdir(parents=True)
    (root / "ground_truth" / "snapshots" / f"{name}.json").write_text(
        json.dumps(
            {
                "name": name,
                "as_of": "2026-09-21T05:00:00Z",
                "totals": {"games": len(ids)},
                "code": {"commit": "abcdef1234", "dirty": dirty},
            }
        )
    )


def test_ids_come_back_per_patch(tmp_path):
    make(tmp_path)
    assert snapshot.ids(tmp_path, "pin") == {"16_18": frozenset({"KR_1", "KR_2"})}


def test_missing_manifest_says_so(tmp_path):
    with pytest.raises(snapshot.SnapshotMissing):
        snapshot.manifest(tmp_path, "없다")


def test_missing_id_list_says_so(tmp_path):
    """**해시만 커밋된다.** 다른 기계에는 ID 목록이 없을 수 있다."""
    make(tmp_path)
    (tmp_path / "data" / "snapshots" / "pin" / "16_18.txt").unlink()
    with pytest.raises(snapshot.SnapshotMissing):
        snapshot.ids(tmp_path, "pin")


def test_empty_folder_is_not_a_snapshot(tmp_path):
    make(tmp_path)
    for f in (tmp_path / "data" / "snapshots" / "pin").glob("*.txt"):
        f.rename(f.with_suffix(".bak"))
    with pytest.raises(snapshot.SnapshotMissing):
        snapshot.ids(tmp_path, "pin")


def test_label_carries_the_as_of_and_code(tmp_path):
    make(tmp_path)
    line = snapshot.label(tmp_path, "pin")
    assert "2026-09-21T05:00:00Z" in line
    assert "abcdef12" in line
    assert "작업 트리 더러움" not in line


def test_label_says_when_the_tree_was_dirty(tmp_path):
    """**더러운 트리로 만든 묶음은 그렇다고 적는다** — 재현이 안 될 수 있다."""
    make(tmp_path, dirty=True)
    assert "작업 트리 더러움" in snapshot.label(tmp_path, "pin")
