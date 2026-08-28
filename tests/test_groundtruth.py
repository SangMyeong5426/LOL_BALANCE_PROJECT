"""정답지 파일 왕복 테스트.

**라벨 1,599종이 매번 이 길로 지나간다.** 형식이 조용히 바뀌면 정답지가 통째로
어긋나고, 그 위의 모든 성적이 같이 어긋난다.

    write_labels   정렬해서 쓴다 — 순서가 흔들리면 diff 가 못 읽는다
    read_labels    없으면 빈 튜플. 「파일이 없다」와 「라벨이 없다」는 같게 다룬다
"""

from __future__ import annotations

import json
from pathlib import Path

from lol_balance.groundtruth import DirectionLabel, read_labels, write_labels


def label(
    champion: str, direction: str = "nerf", patch: str = "16_9"
) -> DirectionLabel:
    return DirectionLabel(
        patch=patch,
        champion=champion,
        direction=direction,  # type: ignore[arg-type]
        evidence=("Base health reduced to 580 from 600.",),
    )


def test_labels_survive_a_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "16_9.jsonl"
    labels = (label("Ahri"), label("Zed", "buff"))

    write_labels(path, labels)

    assert read_labels(path) == tuple(sorted(labels, key=lambda x: x.champion))


def test_evidence_comes_back_as_a_tuple(tmp_path: Path) -> None:
    """**리스트로 돌아오면 조용히 깨진다.**

    한 번 그랬다 — `evidence` 를 문자열로 다루는 검사를 짰다가 리스트라서
    통째로 빗나갔고, 그 결과 멀쩡한 라벨 6건을 뒤집을 뻔했다.
    """
    path = tmp_path / "16_9.jsonl"
    write_labels(path, (label("Ahri"),))

    (got,) = read_labels(path)

    assert isinstance(got.evidence, tuple)


def test_written_labels_are_sorted(tmp_path: Path) -> None:
    """순서가 흔들리면 diff 가 못 읽는다. 라벨을 의심할 때 그 diff 를 본다."""
    path = tmp_path / "16_9.jsonl"

    write_labels(path, (label("Zed"), label("Ahri"), label("Malphite")))

    names = [json.loads(line)["champion"] for line in path.read_text().splitlines()]
    assert names == ["Ahri", "Malphite", "Zed"]


def test_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_labels(tmp_path / "없다.jsonl") == ()


def test_blank_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "16_9.jsonl"
    write_labels(path, (label("Ahri"),))
    path.write_text(path.read_text() + "\n\n", encoding="utf-8")

    assert len(read_labels(path)) == 1


def test_korean_evidence_is_not_escaped(tmp_path: Path) -> None:
    """**근거를 사람이 읽을 수 있어야 한다.** 라벨을 의심할 때 여는 파일이다."""
    path = tmp_path / "16_9.jsonl"
    written = DirectionLabel("16_9", "Ahri", "adjust", ("버그 수정",))

    write_labels(path, (written,))

    assert "버그 수정" in path.read_text(encoding="utf-8")
    assert read_labels(path)[0].evidence == ("버그 수정",)
