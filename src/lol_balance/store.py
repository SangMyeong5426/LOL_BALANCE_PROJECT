"""패널 저장·조회.

[ADR-0004](../../docs/adr/0004-processed-data-storage-format.md) 대로 패널은
SQLite 로 `data/` 에 두고 커밋하지 않는다 — 같은 원자료에서 항상 같은 값이
나오므로 언제든 다시 만든다.

**조회에 패치 경계를 걸 수 있게 만든다.** 4단계 에이전트가 「패치 t 를 예측하라」는
과제를 받고 t 이후 데이터를 읽으면 정답을 그냥 본 것이 된다. 그것을 프롬프트
지시로 막으면 안 되고, **조회 함수가 물리적으로 못 주게** 해야 한다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from lol_balance.panel import PanelRow

TABLE = "panel"

_COLUMNS = (
    "patch TEXT NOT NULL",
    "patch_index INTEGER NOT NULL",
    "champion_id INTEGER NOT NULL",
    "champion TEXT NOT NULL",
    "main_role TEXT NOT NULL",
    "role_count INTEGER NOT NULL",
    "win_rate REAL NOT NULL",
    "pick_rate REAL NOT NULL",
    "ban_rate REAL",
    "matches INTEGER NOT NULL",
    "kills REAL NOT NULL",
    "deaths REAL NOT NULL",
    "assists REAL NOT NULL",
    "cs REAL NOT NULL",
    "gold REAL NOT NULL",
    "damage REAL NOT NULL",
    "d_win_rate REAL",
    "d_pick_rate REAL",
    "d_ban_rate REAL",
    "history_len INTEGER NOT NULL",
    "recent_adjustments INTEGER",
    "high_wr_streak INTEGER NOT NULL",
    "adjusted_next INTEGER NOT NULL",
    "direction_next TEXT",
    "direction_source TEXT",
)
_FIELDS = tuple(c.split()[0] for c in _COLUMNS)


def write_panel(path: Path, rows: tuple[PanelRow, ...]) -> None:
    """패널을 새로 쓴다. 있던 파일은 지우고 다시 만든다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            f"CREATE TABLE {TABLE} ({', '.join(_COLUMNS)}, "
            "PRIMARY KEY (patch, champion_id))"
        )
        conn.execute(f"CREATE INDEX idx_patch ON {TABLE} (patch_index)")
        conn.executemany(
            f"INSERT INTO {TABLE} VALUES ({', '.join('?' * len(_FIELDS))})",
            [tuple(row.as_dict()[f] for f in _FIELDS) for row in rows],
        )


def read_panel(
    path: Path,
    before_patch: str | None = None,
    patch: str | None = None,
) -> tuple[PanelRow, ...]:
    """패널을 읽는다.

    `before_patch` 를 주면 **그 패치보다 앞선 것만** 돌려준다. 경계를 넘는 행은
    아예 나오지 않는다 — 부르는 쪽의 선의에 기대지 않는다.
    """
    where: list[str] = []
    args: list[object] = []
    if before_patch is not None:
        from lol_balance.panel import patch_index

        where.append("patch_index < ?")
        args.append(patch_index(before_patch))
    if patch is not None:
        where.append("patch = ?")
        args.append(patch)

    sql = f"SELECT {', '.join(_FIELDS)} FROM {TABLE}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY patch_index, champion_id"

    with sqlite3.connect(path) as conn:
        cursor = conn.execute(sql, args)
        return tuple(_row(record) for record in cursor)


def _row(record: tuple[object, ...]) -> PanelRow:
    """한 줄을 `PanelRow` 로 되돌린다.

    **위치가 아니라 이름으로 집는다.** 열을 하나 추가했더니 마지막 열이
    `adjusted_next` 에서 `direction_source` 로 바뀌었는데, `row[-1]` 로 읽고
    있어서 조정 여부가 통째로 틀렸다. 파싱은 성공하고 타입도 맞아서 조용히
    지나갔고, 왕복 테스트가 잡았다.
    """
    values = dict(zip(_FIELDS, record, strict=True))
    values["adjusted_next"] = bool(values["adjusted_next"])
    return PanelRow(**values)  # type: ignore[arg-type]
