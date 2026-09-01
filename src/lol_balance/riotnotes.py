"""개발사 자체 패치 노트에서 **조정의 이유**를 뽑는다.

## 왜 위키가 아니라 여기인가

정답지는 위키 전사본에서 온다([`patchnotes.py`](patchnotes.py)) — 무엇이 얼마나
바뀌었는지가 거기 있다. **그런데 위키는 왜 바꿨는지를 버린다.** 개발사 노트에는
챔피언마다 한 문단이 붙는다.

    "Cassiopeia has risen to the top of pro play presence, even before our
     26.12 nerfs to her major competition."          (26.13)

    "Rek'Sai's combination of dueling power and mobility has made her too
     successful at high ranks."                       (26.13)

**우리가 못 보는 모집단을 개발사가 글로 알려 준다** — 「high ranks」는
[모집단 넷 중 둘만 본다](../../docs/results/README.md)에서 안 보인다고 적은 그
구간이다. 수치로는 못 잡는 것이 여기 문장으로 있다.

## 버전 이름이 또 갈린다

    Data Dragon · 우리 패널   16.13
    개발사 사이트 · 위키       26.13

[`patchnotes.wiki_version`](patchnotes.py) 이 이미 그 매핑을 한다. 주소 조각은
그 앞에 `V` 만 뗀 것이다.

**주소 형식이 한 가지가 아니다** — `patch-13-14-notes` 와
`league-of-legends-patch-26-13-notes` 가 섞여 있어 둘 다 시도한다.

## 챔피언이 아닌 블록이 섞인다

`patch-change-block` 은 아이템·룬·`RANKED REWARDS` 에도 붙는다. 챔피언 이름으로
거르는 것은 부르는 쪽 몫이다 — 이 모듈은 블록을 그대로 돌려준다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

from lol_balance.patchnotes import wiki_version


@dataclass(frozen=True)
class Rationale:
    """한 블록의 제목과 이유 문단. 이유가 없으면 `text` 가 빈 문자열이다."""

    title: str
    text: str


def slugs(ddragon_version: str) -> tuple[str, ...]:
    """그 패치의 노트가 있을 만한 주소 조각들. **앞에서부터 시도한다.**

    **다섯 가지가 실제로 쓰인다.** 하나만 시도하면 22패치가 빈다 — 실제로 그랬다.

        patch-13-14-notes                  기본
        league-of-legends-patch-26-13-notes  접두사가 붙은 것
        lol-patch-14-13-notes                줄인 접두사 (14.13 이 이것뿐이다)
        patch-25-05-notes                    앞의 0 을 남긴 것 (2025년)
        patch-25-s1-2-notes                  시즌 번호가 든 것 (2025년 초반)

    **그리고 없는 주소가 200 으로 온다** — 2,482 B 짜리 404 페이지다. 받는 쪽에서
    `patch-change-block` 이 있는지 봐야 다음 주소로 넘어간다.
    """
    padded = wiki_version(ddragon_version).lstrip("V").replace(".", "-")
    bare = re.sub(r"-0(\d)", r"-\1", padded)
    year, _, minor = padded.partition("-")
    names = [padded] + ([bare] if bare != padded else [])
    names += [f"{year}-s{s}-{int(minor)}" for s in (1, 2, 3)]
    return tuple(
        f"{prefix}patch-{n}-notes"
        for n in names
        for prefix in ("", "league-of-legends-", "lol-")
    )


def parse_rationale(html: bytes | str) -> tuple[Rationale, ...]:
    """`patch-change-block` 마다 제목과 `blockquote.context` 를 뽑는다."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[Rationale] = []
    for block in soup.select(".patch-change-block"):
        heading = block.select_one("h3.change-title, h3, h4")
        if heading is None:
            continue
        quote = block.select_one("blockquote.context, blockquote")
        out.append(
            Rationale(
                title=heading.get_text(" ", strip=True),
                text=quote.get_text(" ", strip=True) if quote else "",
            )
        )
    return tuple(out)


def rationale_by_patch(
    directory: Path, champions: Iterable[str]
) -> dict[tuple[str, str], str]:
    """`data/riotnotes/` → (패치, 챔피언) → 이유 문단.

    **파일 이름이 Data Dragon 버전이다** — `16.13.1.html` 이 패치 `16_13` 이다.
    본문의 `26.13` 과 다르지만 파일 이름은 우리 표기를 따른다.

    블록 제목이 챔피언 이름과 정확히 같을 때만 잇는다. `RANKED REWARDS` 나
    아이템 블록은 여기서 떨어진다.
    """
    known = {c.lower(): c for c in champions}
    out: dict[tuple[str, str], str] = {}
    for path in sorted(directory.glob("*.html")):
        major, minor, *_ = path.stem.split(".")
        patch = f"{major}_{minor}"
        for block in parse_rationale(path.read_bytes()):
            champion = known.get(block.title.lower().strip())
            if champion and block.text:
                out[(patch, champion)] = block.text
    return out
