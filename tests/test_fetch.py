"""수집 공통 테스트.

**네트워크를 쓰지 않는다.** 두 층을 따로 본다.

1. `get_with_retry` 를 갈아 끼워 `get_archived` 의 판단을 본다 — 어느 replay
   형식을 어떤 순서로 몇 번의 예산으로 시도하는가
2. `subprocess.run` 을 갈아 끼워 `get` 과 재시도 루프를 본다 — **끊긴 전송을
   실패로 보는가, 그래서 다시 받아 보는가**
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable

import pytest

from lol_balance import fetch
from lol_balance.fetch import (
    REPLAY_MODIFIERS,
    Retryable,
    get,
    get_archived,
    get_with_retry,
)

TS = "20250126163334"
URL = "https://stats2.u.gg/lol/1.5/champion_ranking/world/15_2/x.json"


class _Recorder:
    """`get_with_retry` 대역. 어떤 주소를 몇 번의 예산으로 불렀는지 적어 둔다."""

    def __init__(self, alive: set[str]) -> None:
        self.alive = alive
        self.calls: list[tuple[str, int]] = []

    def __call__(self, url: str, attempts: int = 5, **_: object) -> bytes:
        self.calls.append((url, attempts))
        if any(f"{TS}{modifier}/" in url for modifier in self.alive):
            return b"payload"
        raise Retryable("빈 응답")

    def modifiers_tried(self) -> list[str]:
        return [url.split(TS, 1)[1].split("/", 1)[0] for url, _ in self.calls]


Install = Callable[[set[str]], _Recorder]


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> Install:
    def install(alive: set[str]) -> _Recorder:
        recorder = _Recorder(alive)
        monkeypatch.setattr(fetch, "get_with_retry", recorder)
        return recorder

    return install


def test_first_modifier_wins_without_touching_the_others(patched: Install) -> None:
    recorder = patched({"id_"})

    assert get_archived(TS, URL) == b"payload"
    assert recorder.modifiers_tried() == ["id_"]


def test_falls_through_to_the_next_modifier(patched: Install) -> None:
    """`id_` 가 죽어도 스냅샷이 없는 것이 아니다.

    15_2 · 15_6 · 16_2 가 실제로 이랬다. 이 갈아타기가 없으면 세 패치를
    「아카이브에 없음」으로 잘못 적게 된다.
    """
    recorder = patched({"if_"})

    assert get_archived(TS, URL) == b"payload"
    assert recorder.modifiers_tried() == ["id_", "if_"]


def test_last_modifier_gets_the_full_retry_budget(patched: Install) -> None:
    """앞 형식은 빨리 포기하고 마지막 형식에서만 버틴다.

    빈 응답은 다시 해도 같은 결과라 형식을 바꾸는 쪽이 빠르다. 반대로 아카이브
    일시 장애는 시간이 약이므로 마지막 형식에서는 예산을 다 쓴다.
    """
    recorder = patched(set())

    with pytest.raises(Retryable):
        get_archived(TS, URL, attempts=5)

    budgets = [attempts for _, attempts in recorder.calls]
    assert budgets[-1] == 5
    assert all(budget < 5 for budget in budgets[:-1])


def test_reports_that_every_modifier_failed(patched: Install) -> None:
    recorder = patched(set())

    with pytest.raises(Retryable, match="모두 실패"):
        get_archived(TS, URL)

    assert recorder.modifiers_tried() == list(REPLAY_MODIFIERS)


# ── 끊긴 전송 ────────────────────────────────────────────────────────────
#
# CommunityDragon 전량 수집에서 `15_19/sivir` 가 84,469 B 에서 끊긴 채 왔다.
# **내용이 있으니 빈 응답 검사도, 앞 600바이트를 보는 배너 검사도 통과했다.**
# 재시도 예산 3회를 줬는데 한 번도 쓰이지 않았고, 바깥의 `json.loads` 가
# 던진 `ValueError` 가 영구 실패로 집계됐다. 한 번만 더 불렀으면 됐다.

TRUNCATED = b'{"data": {"aatrox": {"spells": ['  # 앞부분은 멀쩡한 JSON 이다
WHOLE = b'{"data": {}}'

# curl 이 전송 도중 끊겼을 때 내는 코드. 실측으로 확인했다.
CURL_TIMEOUT = 28


class _Curl:
    """`subprocess.run` 대역. 호출마다 (종료 코드, 본문)을 차례로 돌려준다.

    목록이 바닥나면 마지막 것을 계속 돌려준다 — 「계속 같은 실패」를 쓰기 쉽게.
    """

    def __init__(self, replies: list[tuple[int, bytes]]) -> None:
        self.replies = replies
        self.calls = 0

    def __call__(self, *_: object, **__: object) -> subprocess.CompletedProcess[bytes]:
        code, body = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return subprocess.CompletedProcess([], code, body, b"")


Curl = Callable[[list[tuple[int, bytes]]], _Curl]


@pytest.fixture
def curl(monkeypatch: pytest.MonkeyPatch) -> Curl:
    def install(replies: list[tuple[int, bytes]]) -> _Curl:
        fake = _Curl(replies)
        monkeypatch.setattr(fetch.subprocess, "run", fake)
        monkeypatch.setattr(fetch.time, "sleep", lambda _: None)
        return fake

    return install


def test_curl_exit_code_is_a_failure_even_with_a_body(curl: Curl) -> None:
    """**이것이 sivir 를 놓친 자리다.**

    내용이 있고 앞부분이 멀쩡해도 curl 이 0 이 아니면 받은 것이 아니다.
    """
    curl([(CURL_TIMEOUT, TRUNCATED)])

    with pytest.raises(Retryable, match="curl 종료 28"):
        get("https://example.test/x.json")


def test_a_truncated_body_gets_another_attempt(curl: Curl) -> None:
    """검증이 루프 안에 있으면 재시도가 붙는다.

    curl 이 0 을 내면서 잘린 내용을 줄 수도 있다 — 서버가 잘못된
    `Content-Length` 로 깨끗이 끊는 경우다. 종료 코드만으로는 못 잡는다.
    """
    fake = curl([(0, TRUNCATED), (0, WHOLE)])

    assert (
        get_with_retry("https://example.test/x.json", attempts=3, validate=json.loads)
        == WHOLE
    )
    assert fake.calls == 2


def test_a_validator_failure_spends_the_whole_budget(curl: Curl) -> None:
    fake = curl([(0, TRUNCATED)])

    with pytest.raises(Retryable, match="3회 시도 실패"):
        get_with_retry("https://example.test/x.json", attempts=3, validate=json.loads)

    assert fake.calls == 3


def test_without_a_validator_the_broken_body_comes_back(curl: Curl) -> None:
    """**검증기를 안 주면 아무도 안 본다.** 받는 쪽이 요구해야 한다."""
    fake = curl([(0, TRUNCATED)])

    assert get_with_retry("https://example.test/x.json", attempts=3) == TRUNCATED
    assert fake.calls == 1


def test_get_archived_hands_the_validator_down(curl: Curl) -> None:
    """아카이브 쪽이 더 위험하다 — 잘린 응답을 「스냅샷 없음」으로 적게 된다.

    앞 형식(`id_`)에는 예산 2회가 주어지므로 세 번째 호출부터 `if_` 다.
    """
    fake = curl([(0, TRUNCATED), (0, TRUNCATED), (0, WHOLE)])

    assert get_archived(TS, URL, validate=json.loads) == WHOLE
    assert fake.calls == 3
