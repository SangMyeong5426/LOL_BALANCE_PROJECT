"""원자료 수집 공통.

**받은 것은 `data/` 아래에 두고 커밋하지 않는다.** 스크립트가 있으면 언제든
다시 만들 수 있고, 원자료를 저장소에 두지 않는 것이 이 프로젝트의 규칙이다.

수집기는 **이어받기가 되어야 한다.** 웹 아카이브가 간헐적으로 죽고 요청 제한도
걸리므로, 한 번에 끝난다고 가정하면 안 된다. 이미 받은 파일은 건너뛴다.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

# 웹 아카이브가 죽었을 때 돌려주는 안내 페이지의 크기. 데이터 없음과 구분해야 한다.
ARCHIVE_OFFLINE_MARKER = b"Temporarily Offline"
RATE_LIMIT_MARKER = b"429 Too Many Requests"


class FetchError(RuntimeError):
    """받기를 포기해야 하는 상황."""


class Retryable(FetchError):
    """잠시 뒤 다시 하면 될 수 있는 상황 — 아카이브 장애, 요청 제한."""


def get(url: str, timeout: int = 120) -> bytes:
    """URL 하나를 받는다.

    `--compressed` 를 반드시 붙인다. u.gg 응답은 압축돼 오는데 이것이 없으면
    바이너리가 그대로 와서 「형식이 깨졌다」로 오판하게 된다.
    """
    result = subprocess.run(
        ["curl", "-sL", "--compressed", "-m", str(timeout), "-A", USER_AGENT, url],
        capture_output=True,
    )
    body = result.stdout
    if not body:
        raise Retryable(f"빈 응답: {url}")
    head = body[:600]
    if ARCHIVE_OFFLINE_MARKER in head:
        raise Retryable("웹 아카이브 일시 장애")
    if RATE_LIMIT_MARKER in head:
        raise Retryable("요청 제한(429)")
    return body


def get_with_retry(
    url: str,
    attempts: int = 5,
    base_delay: float = 20.0,
    timeout: int = 120,
) -> bytes:
    """되는 데까지 다시 시도한다. 간격을 두 배씩 늘린다."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return get(url, timeout=timeout)
        except Retryable as exc:
            last = exc
            if i < attempts - 1:
                time.sleep(base_delay * (2**i))
    raise Retryable(f"{attempts}회 시도 실패: {last}")


def save(path: Path, body: bytes) -> None:
    """받은 것을 저장한다. 도중에 죽어도 반쪽 파일이 남지 않도록 임시 파일을 거친다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(body)
    tmp.replace(path)
