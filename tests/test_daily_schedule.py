"""매일 수집의 예약 — **맥이 깨어 있을 때 하루 한 번만 돈다.**

덮개를 닫은 맥은 새벽에 몇 초씩만 깬다(다크웨이크). 2026-09-29 새벽에는 05:19 에
시작한 실행이 그렇게 기어가다 맥을 연 08:59 뒤에야 제 속도로 받았다. 그래서 예약
작업은 매시간 확인하고, 20시간 안에 받았거나 다크웨이크면 요청 없이 끝낸다.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]

# `pmset -g systemstate` 의 답. 깨어 있을 때는 이 맥에서 그대로 받아 적었다.
# 다크웨이크 쪽은 그날 기록의 표시([CDNP] — 화면 V · 소리 A 가 없다)로 만든 것이다.
AWAKE = "Current System Capabilities are: CPU Graphics Audio Network \nCurrent Power State: 4\n"
DARK = "Current System Capabilities are: CPU Network \nCurrent Power State: 4\n"


def load(name: str, script: str) -> ModuleType:
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / "scripts" / script))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def daily() -> ModuleType:
    return load("fetch_riot_daily_schedule", "fetch-riot-daily")


@pytest.fixture(scope="module")
def install() -> ModuleType:
    return load("install_riot_daily", "install-riot-daily")


def pmset(text: str) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert cmd == ["pmset", "-g", "systemstate"]
        return subprocess.CompletedProcess(cmd, 0, stdout=text, stderr="")

    return run


def collected(root: Path, hours_ago: float) -> None:
    """경기 파일 하나를 만들고 **바뀐 시각**을 그만큼 앞으로 돌린다."""
    folder = root / "data" / "riot" / "16_19"
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / "kr.jsonl"
    f.write_text('{"v": 2, "id": "KR_1", "start": 0}\n')
    when = time.time() - hours_ago * 3600
    os.utime(f, (when, when))


def run_main(daily: ModuleType, monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["fetch-riot-daily", *argv])
    return int(daily.main())


@pytest.fixture
def no_network(daily: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """건너뛰는 실행은 **요청을 한 건도 보내면 안 된다.**"""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("요청을 보냈다")

    monkeypatch.setattr(daily, "current_patch", boom)
    monkeypatch.setattr(daily.subprocess, "call", boom)


def test_awake_when_the_screen_is_on(daily: ModuleType) -> None:
    assert daily.awake(pmset(AWAKE)) is True


def test_a_dark_wake_is_not_awake(daily: ModuleType) -> None:
    """화면 기능이 없으면 덮개를 닫은 맥이 몇 초만 깬 것이다."""
    assert daily.awake(pmset(DARK)) is False


def test_an_unknown_state_counts_as_awake(daily: ModuleType) -> None:
    """판단할 수 없으면 예전처럼 돈다 — 잘못 막아 하루를 잃는 쪽이 더 나쁘다."""

    def missing(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("pmset")

    assert daily.awake(missing) is True
    assert daily.awake(pmset("")) is True


def test_too_soon_counts_from_the_last_collected_file(
    daily: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    now = datetime.now(UTC)
    assert daily.too_soon(now) is None  # 받은 것이 없다
    collected(tmp_path, hours_ago=3)
    assert daily.too_soon(now) == pytest.approx(3, abs=0.05)
    collected(tmp_path, hours_ago=daily.MIN_GAP_HOURS + 1)
    assert daily.too_soon(now) is None


def test_an_empty_file_is_not_a_collection(
    daily: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """한 판도 못 받은 실행은 받은 것이 아니다 — 다음 확인 때 다시 돈다.

    2026-10-07 에 새 패치의 첫 실행이 0판으로 끝나며 빈 파일을 남겼다. 그 파일이 방금
    생겼다는 이유로 스무 시간 동안 다시 돌지 않았다.
    """
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    collected(tmp_path, hours_ago=30)
    empty = tmp_path / "data" / "riot" / "16_20" / "kr.jsonl"
    empty.parent.mkdir(parents=True)
    empty.write_text("")
    assert daily.too_soon(datetime.now(UTC)) is None


def test_a_scheduled_run_skips_quietly_after_a_recent_collection(
    daily: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_network: None,
) -> None:
    """하루에 스무 번 넘게 되풀이되므로 로그에 아무것도 안 남긴다."""
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    collected(tmp_path, hours_ago=3)
    monkeypatch.setattr(daily, "awake", lambda: True)
    assert run_main(daily, monkeypatch, "--scheduled") == 0
    assert capsys.readouterr().out == ""


def test_a_scheduled_run_skips_a_dark_wake_and_says_so(
    daily: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_network: None,
) -> None:
    """다크웨이크는 적어 둔다 — 제대로 가려내는지 그 줄로 확인한다."""
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    collected(tmp_path, hours_ago=30)
    monkeypatch.setattr(daily, "awake", lambda: False)
    assert run_main(daily, monkeypatch, "--scheduled") == 0
    assert "다크웨이크" in capsys.readouterr().out


def test_check_says_what_a_scheduled_run_would_do(
    daily: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_network: None,
) -> None:
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    collected(tmp_path, hours_ago=3)
    assert run_main(daily, monkeypatch, "--check") == 0
    assert capsys.readouterr().out.startswith("안 돈다")

    collected(tmp_path, hours_ago=30)
    monkeypatch.setattr(daily, "awake", lambda: True)
    assert run_main(daily, monkeypatch, "--check") == 0
    assert capsys.readouterr().out.startswith("돈다")


def test_a_scheduled_run_goes_when_due_and_awake(
    daily: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """수집 양은 그대로다 — 플랫폼마다 300판."""
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    collected(tmp_path, hours_ago=30)
    monkeypatch.setattr(daily, "awake", lambda: True)
    monkeypatch.setattr(daily, "current_patch", lambda: "16_19")
    monkeypatch.setattr(daily, "code_line", lambda: "코드 시험")
    calls: list[list[str]] = []

    def fake_call(cmd: list[str], **kwargs: Any) -> int:
        calls.append(cmd)
        return 0

    monkeypatch.setattr(daily.subprocess, "call", fake_call)
    assert run_main(daily, monkeypatch, "--scheduled") == 0
    assert len(calls) == 1
    assert calls[0][calls[0].index("--add") + 1] == "300"


def test_a_run_on_a_new_patch_asks_for_the_previous_patchs_tail(
    daily: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """패치가 바뀐 날 — 앞 패치를 받던 맥이면 그 끝도 받으라고 넘긴다. 양은 그대로다."""
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    collected(tmp_path, hours_ago=30)  # `16_19` 를 받던 맥
    monkeypatch.setattr(daily, "awake", lambda: True)
    monkeypatch.setattr(daily, "current_patch", lambda: "16_20")
    monkeypatch.setattr(daily, "code_line", lambda: "코드 시험")
    calls: list[list[str]] = []

    def fake_call(cmd: list[str], **kwargs: Any) -> int:
        calls.append(cmd)
        return 0

    monkeypatch.setattr(daily.subprocess, "call", fake_call)
    assert run_main(daily, monkeypatch, "--scheduled") == 0
    assert calls[0][calls[0].index("--tail") + 1] == "16_19"
    assert calls[0][calls[0].index("--add") + 1] == "300"


def test_no_tail_without_a_patch_collected_before(
    daily: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """앞 패치를 받은 적이 없으면 넘길 것이 없다."""
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    monkeypatch.setattr(daily, "current_patch", lambda: "16_20")
    monkeypatch.setattr(daily, "code_line", lambda: "코드 시험")
    calls: list[list[str]] = []

    def fake_call(cmd: list[str], **kwargs: Any) -> int:
        calls.append(cmd)
        return 0

    monkeypatch.setattr(daily.subprocess, "call", fake_call)
    assert run_main(daily, monkeypatch) == 0
    assert "--tail" not in calls[0]


def test_a_manual_run_does_not_check(
    daily: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """손으로 부르면 예전처럼 바로 돈다 — 방금 받았어도, 다크웨이크여도."""
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    collected(tmp_path, hours_ago=1)
    monkeypatch.setattr(daily, "awake", lambda: False)
    monkeypatch.setattr(daily, "current_patch", lambda: "16_19")
    monkeypatch.setattr(daily, "code_line", lambda: "코드 시험")
    calls: list[list[str]] = []

    def fake_call(cmd: list[str], **kwargs: Any) -> int:
        calls.append(cmd)
        return 0

    monkeypatch.setattr(daily.subprocess, "call", fake_call)
    assert run_main(daily, monkeypatch) == 0
    assert len(calls) == 1


def test_the_job_checks_hourly_instead_of_at_a_fixed_time(install: ModuleType) -> None:
    """05:10 같은 시각이 아니라 간격으로 건다 — 덮개를 닫은 맥은 그 시각에 못 깬다."""
    job = install.job()
    assert "StartCalendarInterval" not in job
    assert job["StartInterval"] == 3600
    assert job["RunAtLoad"] is True
    assert "fetch-riot-daily --scheduled" in job["ProgramArguments"][-1]
