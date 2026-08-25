"""패치 노트 — **정답지**.

라이엇 공식 위키(`wiki.leagueoflegends.com`)에서 받는다. 뉴스 사이트가 아니라
위키를 쓰는 이유는 **주소 규칙이 일관되기 때문**이다.

뉴스 사이트는 같은 시기 안에서도 형태가 네 갈래로 갈렸다.

```
patch-13-14-notes                     13.x · 14.x
patch-25-s1-1-notes                   15.1 · 15.2
patch-25-04-notes                     15.4 ~ 15.9
league-of-legends-patch-26-9-notes    16.4 이후
```

그리고 14.13 과 15.3 은 **어느 형태로도 없었다.** 위키는 74개 패치 전부 있다.
"""

from __future__ import annotations

import re

WIKI_BASE = "https://wiki.leagueoflegends.com/en-us"

# Data Dragon 이 옛 번호를 유지하는 동안 라이엇은 2025 년부터 연도 기반으로
# 바꿨다. 15.x ↔ 25.x, 16.x ↔ 26.x.
_YEAR_SHIFT_FROM = 15
_YEAR_SHIFT = 10


def wiki_version(ddragon_version: str) -> str:
    """Data Dragon 버전을 위키 문서 이름으로 바꾼다.

    >>> wiki_version("13.14.1")
    'V13.14'
    >>> wiki_version("14.1.1")
    'V14.1'
    >>> wiki_version("15.3.1")
    'V25.03'
    >>> wiki_version("16.15.1")
    'V26.15'
    """
    m = re.match(r"^(\d+)\.(\d+)", ddragon_version)
    if not m:
        raise ValueError(f"버전 형식이 아니다: {ddragon_version!r}")
    major, minor = int(m.group(1)), int(m.group(2))
    if major < _YEAR_SHIFT_FROM:
        return f"V{major}.{minor}"
    return f"V{major + _YEAR_SHIFT}.{minor:02d}"


def wiki_url(ddragon_version: str) -> str:
    return f"{WIKI_BASE}/{wiki_version(ddragon_version)}"
