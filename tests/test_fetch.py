"""수집 공통 테스트.

**네트워크를 쓰지 않는다.** `get_with_retry` 를 가짜로 갈아 끼워 `get_archived`
의 판단만 본다 — 어느 replay 형식을 어떤 순서로 몇 번의 예산으로 시도하는가.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from lol_balance import fetch
from lol_balance.fetch import REPLAY_MODIFIERS, Retryable, get_archived

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
