"""매일 수집의 창 — **놓친 날을 마지막으로 받은 경기부터 메운다.**

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


def test_a_long_gap_is_filled_from_the_last_game_too(daily, tmp_path, monkeypatch):
    """**거슬러 가는 데 한계를 두지 않는다.**

    2026-10-06 까지는 96시간에서 끊었다. 「이용자 기록은 최근 20판까지만 보인다」가
    근거였는데 틀렸다 — 경기 목록은 창으로 묻고, `16_13` 은 75 ~ 88일 지난 창을
    받았다. 그날 실행은 한계 164초 전에 시작해 겨우 이어졌다.
    """
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    last = datetime.now(UTC) - timedelta(days=9)
    write(tmp_path / "data" / "riot" / "16_19", last)
    assert abs((daily.window_start("16_19") - last).total_seconds()) < 2


def test_a_new_patch_starts_where_the_previous_one_stopped(
    daily, tmp_path, monkeypatch
):
    """**새 패치의 첫 실행은 앞 패치에서 마지막으로 받은 경기부터 본다.**

    패치 교체일을 끼고 덮개를 닫아 두면 26시간 창으로는 앞 패치의 끝도 새 패치의 처음도
    못 받는다. 창 안의 앞 패치 경기는 버리지 않고 그 패치에 잇는다(`fetch-riot --tail`).
    """
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    last = datetime.now(UTC) - timedelta(days=3)
    write(tmp_path / "data" / "riot" / "16_19", last)
    assert abs((daily.window_start("16_20") - last).total_seconds()) < 2


def test_a_new_patch_the_day_after_a_run_uses_the_usual_window(
    daily, tmp_path, monkeypatch
):
    """어제 앞 패치를 받았으면 평소대로 지난 26시간이다."""
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    write(tmp_path / "data" / "riot" / "16_19", datetime.now(UTC) - timedelta(hours=3))
    hours = (datetime.now(UTC) - daily.window_start("16_20")).total_seconds() / 3600
    assert daily.WINDOW_HOURS - 1 < hours < daily.WINDOW_HOURS + 1


def test_a_file_without_games_is_a_patch_without_games(daily, tmp_path, monkeypatch):
    """빈 파일은 받은 것이 아니다 — 2026-10-07 에 0판으로 끝난 첫 실행이 남겼다."""
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    last = datetime.now(UTC) - timedelta(days=2)
    write(tmp_path / "data" / "riot" / "16_19", last)
    (tmp_path / "data" / "riot" / "16_20").mkdir()
    (tmp_path / "data" / "riot" / "16_20" / "kr.jsonl").write_text("")
    assert abs((daily.window_start("16_20") - last).total_seconds()) < 2


def test_once_the_new_patch_has_games_the_previous_one_is_not_consulted(
    daily, tmp_path, monkeypatch
):
    """앞 패치를 보는 것은 첫 실행뿐이다. 그 뒤로는 이 패치의 마지막 경기가 창을 묶는다."""
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    write(tmp_path / "data" / "riot" / "16_19", datetime.now(UTC) - timedelta(days=20))
    write(tmp_path / "data" / "riot" / "16_20", datetime.now(UTC) - timedelta(hours=3))
    hours = (datetime.now(UTC) - daily.window_start("16_20")).total_seconds() / 3600
    assert daily.WINDOW_HOURS - 1 < hours < daily.WINDOW_HOURS + 1


def test_the_previous_patch_is_the_latest_older_one_with_games(
    daily, tmp_path, monkeypatch
):
    """이름을 숫자로 견준다 — `16_9` 는 `16_19` 보다 앞이다. 받은 적 없는 패치는 건너뛴다."""
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    riot = tmp_path / "data" / "riot"
    for name in ("16_9", "16_18", "16_19"):
        write(riot / name, datetime.now(UTC))
    (riot / "16_20").mkdir()
    (riot / "16_20" / "kr.jsonl").write_text("")  # 0판으로 끝난 실행
    write(riot / "v1" / "16_25", datetime.now(UTC))  # 옛 형식 보관함 — 패치가 아니다

    assert daily.previous_patch("16_20") == "16_19"
    assert daily.previous_patch("17_1") == "16_19"
    assert daily.previous_patch("16_10") == "16_9"
    assert daily.previous_patch("16_9") is None


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
