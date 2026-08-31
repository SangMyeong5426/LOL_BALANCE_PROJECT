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
from dataclasses import dataclass

from bs4 import BeautifulSoup
from bs4.element import Tag

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


# 챔피언 절의 제목. 다른 절(Items·Runes 등)은 이 프로젝트의 대상이 아니다.
_CHAMPION_HEADING = "champion"

# **핫픽스도 그 패치의 조정이다.** 「January 12th Hotfix」처럼 날짜가 붙은 별도
# 절로 나오는데, 처음에는 `Champions` 절만 읽어 통째로 놓쳤다 — 14.1 은 본절
# 16종에 핫픽스 14종이라 절반 가까이가 빠졌다. 플레이어는 그 값으로 게임을 했다.
_HOTFIX_HEADING = "hotfix"

# **위키와 Data Dragon 이 한 챔피언을 다르게 부른다.** 위키의 `data-champion` 은
# `Nunu`, Data Dragon 과 패널은 `Nunu & Willump` 다. 이름이 안 맞으면 그 챔피언의
# 조정이 통째로 「조정 안 됨」으로 기록된다 — 실제로 5개 패치가 그렇게 빠졌고,
# 라벨 4건도 패널에 못 붙었다. **연결 키를 이름으로 잡은 대가다**(CLAUDE.md
# 「버전 표기를 믿지 않는다」).
#
# `scripts/build-panel` 이 노트 이름과 패널 이름을 대조해, 여기 없는 새 어긋남이
# 생기면 그 자리에서 경고한다. **조용히 지나가지 않는다.**
_ALIASES = {"Nunu": "Nunu & Willump"}


def champion_name(wiki_name: str) -> str:
    """위키 표기를 Data Dragon · 패널 표기로 맞춘다."""
    return _ALIASES.get(wiki_name, wiki_name)


# **Arena·Doom Bots 는 협곡이 아니다.** 같은 형식으로 챔피언 변경을 담지만
# 모드 전용 수치라, 협곡 지표와 이어 붙이면 값이 튄다(CLAUDE.md 「출처를 섞지
# 않는다」). 그래서 제목으로 걸러 낸다.


@dataclass(frozen=True)
class ChangeBlock:
    """한 챔피언의 한 묶음에 딸린 변경 문장들.

    묶음은 `Stats`·`General` 이거나 스킬 하나다. 스킬이면 `ability` 가 채워진다.
    """

    champion: str
    section: str
    ability: str | None
    lines: tuple[str, ...]


# 텍스트 조각 사이에 넣는 표식. 소수점 처리 때문에 필요하다 — 아래 참조.
_JOINT = "\x00"


def _leaf_text(node: Tag) -> str:
    """리스트 항목 하나를 한 줄로 읽는다.

    조각 사이를 그냥 공백으로 이으면 **소수가 깨진다.** 위키가 소수부를
    `0.<small>67</small>` 로 감싸기 때문에 `0. 67` 이 되고, 그러면 12.5 가
    12 과 5 로 읽힌다. 반대로 아무것도 안 넣으면 `reduced to<span>60% AD</span>`
    가 `reduced to60% AD` 로 붙는다. **둘 다 실제로 겪었다.**

    그래서 표식을 넣어 두고 소수 자리에서만 지운 뒤 나머지를 공백으로 바꾼다.
    """
    text = node.get_text(_JOINT)
    text = re.sub(rf"(\d){_JOINT}*\.{_JOINT}*(\d)", r"\1.\2", text)
    return re.sub(r"\s+", " ", text.replace(_JOINT, " ")).strip()


def _own_text(item: Tag) -> str:
    """중첩 목록을 뺀, **그 항목 자신의 문장.**

    중간 노드가 자기 문장을 가진다. Aatrox 는 `First cast ...` 항목 안에
    `Second cast` · `Third cast` 가 중첩돼 있어서, 잎만 모으면 **First 계열
    네 줄이 통째로 사라진다.** 실제로 그렇게 빠뜨렸다.
    """
    nested = item.find_all("ul", recursive=False)
    for node in nested:
        node.extract()
    text = _leaf_text(item)
    for node in nested:
        item.append(node)
    return text


def _label(item: Tag) -> tuple[str, str | None]:
    """상위 항목의 묶음 이름. 스킬이면 `data-ability` 에 정확한 이름이 있다."""
    icon = item.find("span", attrs={"data-ability": True})
    if isinstance(icon, Tag):
        ability = str(icon["data-ability"])
        return ability, ability
    return _own_text(item) or "General", None


def _sentences(item: Tag) -> list[str]:
    """묶음 아래의 모든 변경 문장. **깊이를 가정하지 않는다** — 시대마다 다르다."""
    lines = [t for t in (_own_text(x) for x in item.find_all("li")) if t]
    if lines:
        return lines
    # 중첩이 없으면 항목 자신이 곧 문장이다.
    own = _own_text(item)
    return [own] if own else []


def _change_sections(soup: BeautifulSoup) -> list[Tag]:
    """챔피언 변경을 담은 절들의 **부모 노드.**

    `Champions` 하나만 보면 안 된다 — 같은 패치 안에서 핫픽스가 별도 절로
    나오고, 그것도 그 패치에 실제로 적용된 조정이다. 반대로 `Arena` 나
    `Doom Bots` 는 형식이 같아도 협곡이 아니라 제외한다.
    """
    out: list[Tag] = []
    for h in soup.select("h3"):
        title = h.get_text(strip=True).lower()
        if not (title.startswith(_CHAMPION_HEADING) or _HOTFIX_HEADING in title):
            continue
        if isinstance(h.parent, Tag):
            out.append(h.parent)
    return out


def changed_champions(html: bytes) -> frozenset[str]:
    """그 패치 노트가 조정을 적은 챔피언 전부.

    **스냅샷은 노트보다 늦게 움직인다.** Data Dragon 은 패치 시작 시점에 뜨므로
    패치 P 중간에 나간 핫픽스가 P → P+1 diff 에서 처음 나타나고, cdragon 도 같은
    지연을 낸다. 그대로 두면 **P 의 조정이 P+1 의 조정으로 적힌다.**

    Corki 가 그랬다 — 13.24 핫픽스(체력 588→610 · 공격력 55→59 · 성장치 하향)가
    Data Dragon 의 14.1 diff 에 통째로 나타나, 14.1 노트가 적은 것(공격력 59→61 ·
    R 계수 상향, 명백한 버프)과 부딪혔다. **14.1 자신의 변경은 그 diff 에 아예
    없다.**

    그래서 P+1 의 기계 판정에서 **P 의 노트가 이미 적은 챔피언을 뺀다.** 그 조정은
    P 것이고 손 라벨도 거기 붙는다. 잃는 것은 「P 에서 조정됐고 P+1 에서 문서화
    없이 또 조정된」 경우뿐인데, 전수 확인에서 0건이었다.
    """
    return frozenset(b.champion for b in champion_changes(html))


def champion_changes(html: bytes) -> tuple[ChangeBlock, ...]:
    """패치 노트 HTML 에서 챔피언 변경 묶음을 뽑는다.

    **이름을 문자열에서 짐작하지 않는다.** 위키가 `data-champion` 과
    `data-ability` 속성에 정확한 이름을 넣어 두므로 그것을 쓴다. 표기가 시대마다
    달라도(산문 `reduced to X from Y` ↔ 화살표 `A ⇒ B`) 이 골격은 같다.

    문장 자체는 자연어 그대로 둔다. 「무엇이 몇에서 몇으로」를 구조화하는 것은
    LLM 의 일이고, 여기서는 **어느 챔피언의 어느 묶음인지**까지만 확정한다.
    """
    soup = BeautifulSoup(html, "html.parser")
    blocks: list[ChangeBlock] = []
    for parent in _change_sections(soup):
        champion: str | None = None
        for sib in parent.next_siblings:
            if not isinstance(sib, Tag):
                continue
            if sib.name == "div" and sib.find("h3"):
                break  # 다음 절로 넘어갔다
            if sib.name == "dl":
                icon = sib.find("span", attrs={"data-champion": True})
                champion = (
                    champion_name(str(icon["data-champion"]))
                    if isinstance(icon, Tag)
                    else None
                )
            elif sib.name == "ul" and champion:
                for item in sib.find_all("li", recursive=False):
                    section, ability = _label(item)
                    lines = tuple(_sentences(item))
                    if not lines:
                        continue
                    if lines == (section,):
                        section = "General"  # 묶음 없이 문장 하나만 있는 경우
                    blocks.append(ChangeBlock(champion, section, ability, lines))
    return tuple(blocks)
