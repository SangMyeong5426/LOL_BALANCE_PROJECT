"""Data Dragon 버전 → 라이엇 위키 문서 이름 변환 테스트.

**2025년부터 라이엇이 패치 번호를 연도 기반으로 바꿨는데 Data Dragon 은 옛
번호를 유지한다.** 그래서 같은 패치가 두 이름을 갖는다. 이 변환이 틀리면
승률과 패치 노트가 엉뚱하게 짝지어지고, 그것은 조용히 지나간다.
"""

from __future__ import annotations

import pytest

from patchlens.patchnotes import wiki_url, wiki_version


@pytest.mark.parametrize(
    ("ddragon", "wiki"),
    [
        ("13.14.1", "V13.14"),
        ("13.24.1", "V13.24"),
        ("14.1.1", "V14.1"),
        ("14.13.1", "V14.13"),
        ("14.24.1", "V14.24"),
        # 여기서부터 +10 하고 0 을 채운다
        ("15.1.1", "V25.01"),
        ("15.3.1", "V25.03"),
        ("15.9.1", "V25.09"),
        ("15.10.1", "V25.10"),
        ("15.24.1", "V25.24"),
        ("16.1.1", "V26.01"),
        ("16.9.1", "V26.09"),
        ("16.15.1", "V26.15"),
    ],
)
def test_maps_ddragon_version_to_wiki_page(ddragon: str, wiki: str) -> None:
    assert wiki_version(ddragon) == wiki


def test_single_digit_minor_is_padded_only_after_the_rename() -> None:
    """14.1 은 `V14.1`, 15.1 은 `V25.01`. 규칙이 시점에 따라 갈린다."""
    assert wiki_version("14.1.1") == "V14.1"
    assert wiki_version("15.1.1") == "V25.01"


def test_url_is_built_from_the_wiki_name() -> None:
    assert wiki_url("16.15.1").endswith("/V26.15")


@pytest.mark.parametrize("bad", ["", "abc", "16", "v16.15"])
def test_rejects_a_non_version_string(bad: str) -> None:
    with pytest.raises(ValueError, match="버전 형식"):
        wiki_version(bad)
