"""고정 데이터 묶음 — **평가는 매일 불어나는 최신 파일이 아니라 이것을 읽는다.**

매일 수집이 현재 패치를 계속 불린다. 코드나 모델을 견주는 자리에서 자료까지
같이 움직이면 **무엇이 숫자를 바꿨는지 못 가른다.** 묶음은 어느 경기를 셌는지를
경기 ID 로 굳힌다(`scripts/freeze-eval`).

집계값이 아니라 ID 를 굳히는 이유는, 코드가 바뀌면 집계는 바뀌어야 하기 때문이다.
그 차이를 보려고 자료를 굳힌다.
"""

from __future__ import annotations

import json
from pathlib import Path


class SnapshotMissing(LookupError):
    """묶음이나 그 안의 ID 목록이 없다."""


def folder(root: Path, name: str) -> Path:
    return root / "data" / "snapshots" / name


def manifest_path(root: Path, name: str) -> Path:
    return root / "ground_truth" / "snapshots" / f"{name}.json"


def manifest(root: Path, name: str) -> dict:
    """묶음의 해시와 셈. 커밋된 쪽이라 ID 목록이 없어도 읽힌다."""
    path = manifest_path(root, name)
    if not path.is_file():
        raise SnapshotMissing(f"{path} 가 없다 — ./scripts/freeze-eval --list")
    out = json.loads(path.read_text())
    assert isinstance(out, dict)
    return out


def ids(root: Path, name: str) -> dict[str, frozenset[str]]:
    """패치마다 묶음에 든 경기 ID.

    ID 목록은 원자료라 커밋하지 않는다. 다른 기계에서는 `fetch-riot` 로 다시
    받은 뒤 같은 이름으로 굳혀야 하고, **해시가 다르면 다른 묶음이다.**
    """
    home = folder(root, name)
    if not home.is_dir():
        raise SnapshotMissing(f"{home} 가 없다 — 이 기계에 ID 목록이 없다")
    out: dict[str, frozenset[str]] = {}
    for f in sorted(home.glob("*.txt")):
        out[f.stem] = frozenset(x for x in f.read_text().split() if x)
    if not out:
        raise SnapshotMissing(f"{home} 가 비었다")
    return out


def label(root: Path, name: str) -> str:
    """보고서 머리에 한 줄로 적을 것."""
    body = manifest(root, name)
    code = body["code"]
    dirty = " · 작업 트리 더러움" if code.get("dirty") else ""
    return (
        f"고정 묶음 «{body['name']}» · 기준 {body['as_of']}"
        f" · 경기 {body['totals']['games']:,} · 코드 {code['commit'][:8]}{dirty}"
    )
