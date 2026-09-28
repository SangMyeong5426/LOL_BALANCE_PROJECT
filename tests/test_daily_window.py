"""매일 수집의 창 — **놓친 날을 메우되 무한정 거슬러 가지 않는다.**

맥이 자거나 꺼져 있으면 그날 실행이 통째로 빠진다. 26시간 창만 보면 그 사이가
영영 빈다 — 2026-09-22 뒤로 닷새가 그렇게 사라졌다.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def daily():
    loader = importlib.machinery.SourceFileLoader(
        "fetch_riot_daily", str(ROOT / "scripts" / "fetch-riot-daily")
    )
    spec = importlib.util.spec_from_loader("fetch_riot_daily", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_riot_daily"] = module
    loader.exec_module(module)
    return module


def write(folder: Path, when: datetime) -> None:
    """저장 형식 그대로 한 줄. **칸 이름은 `start` 다**(riot.dumps)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "kr.jsonl").write_text(
        json.dumps({"v": 2, "id": "KR_1", "start": int(when.timestamp() * 1000)}) + "\n"
    )


def test_a_fresh_patch_uses_the_usual_window(daily, tmp_path, monkeypatch):
    """받아 둔 것이 없으면 평소대로 지난 26시간."""
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    start = daily.window_start("16_19")
    hours = (datetime.now(UTC) - start).total_seconds() / 3600
    assert daily.WINDOW_HOURS - 1 < hours < daily.WINDOW_HOURS + 1


def test_a_recent_run_uses_the_usual_window(daily, tmp_path, monkeypatch):
    """어제 받았으면 메울 것이 없다 — 겹치는 두 시간은 그대로 둔다."""
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    write(tmp_path / "data" / "riot" / "16_19", datetime.now(UTC) - timedelta(hours=3))
    start = daily.window_start("16_19")
    hours = (datetime.now(UTC) - start).total_seconds() / 3600
    assert daily.WINDOW_HOURS - 1 < hours < daily.WINDOW_HOURS + 1


def test_a_missed_day_is_filled_from_the_last_game(daily, tmp_path, monkeypatch):
    """이틀 못 돌았으면 마지막 경기부터 메운다."""
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    last = datetime.now(UTC) - timedelta(hours=50)
    write(tmp_path / "data" / "riot" / "16_19", last)
    assert abs((daily.window_start("16_19") - last).total_seconds()) < 2


def test_the_catch_up_stops_at_the_limit(daily, tmp_path, monkeypatch):
    """**무한정 거슬러 가지 않는다.**

    이용자 기록은 최근 20판까지만 보인다. 나흘 너머를 요청해도 받은 경기가 전부
    창 밖이라 버려지기만 하고, 그러면 수집이 한 판도 못 건지고 끝난다.
    """
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    write(tmp_path / "data" / "riot" / "16_19", datetime.now(UTC) - timedelta(days=30))
    hours = (datetime.now(UTC) - daily.window_start("16_19")).total_seconds() / 3600
    assert hours == pytest.approx(daily.CATCH_UP_LIMIT_HOURS, abs=1)


def test_a_broken_line_does_not_stop_the_scan(daily, tmp_path, monkeypatch):
    """줄 하나가 깨져도 나머지에서 마지막 경기를 찾는다."""
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    folder = tmp_path / "data" / "riot" / "16_19"
    folder.mkdir(parents=True)
    good = datetime.now(UTC) - timedelta(hours=40)
    (folder / "kr.jsonl").write_text(
        "{망가진 줄\n"
        + json.dumps({"v": 2, "id": "KR_1"})
        + "\n"  # start 가 없다
        + json.dumps({"v": 2, "id": "KR_2", "start": int(good.timestamp() * 1000)})
        + "\n"
    )
    assert abs((daily.last_game("16_19") - good).total_seconds()) < 2
