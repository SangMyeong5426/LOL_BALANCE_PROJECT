"""정적 API — 모양, 공개 범위, 공개된 목록이 지켜지는지.

두 무리다.

- **만드는 법** — 작은 가짜 패널로 효과 · 이력 · 공개 범위 검사를 본다
- **계약** — 커밋된 `docs/site/api/v1/` 전부를 모델로 읽고, 공개 범위를 넘는 자료가
  없는지, 공개한 목록이 그대로인지, 크기 예산 안인지 본다. CI 에는 `data/` 가 없어
  다시 만들 수 없으므로 **커밋된 파일**을 검사한다([extension 2절](../docs/extension.md))
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lol_balance import site_api
from lol_balance.effect import outcomes
from lol_balance.explain import outcome
from lol_balance.panel import PanelRow

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "docs" / "site" / "api" / "v1"

# 2026-09-30 에 배포 페이지가 공개하던 `16_13 → 16_14` 목록이다. **바뀌면 걸린다** —
# 코드를 고치다 공개한 목록이 조용히 달라지는 것을 막는다.
PUBLISHED_NERF = [
    "Senna",
    "Naafiri",
    "Syndra",
    "Nocturne",
    "Sylas",
    "Nasus",
    "Seraphine",
    "Orianna",
    "Poppy",
    "Nautilus",
]
PUBLISHED_BUFF = [
    "Azir",
    "Trundle",
    "K'Sante",
    "Mel",
    "Qiyana",
    "Volibear",
    "Rengar",
    "Yuumi",
    "Amumu",
    "Yunara",
]

Row = Callable[..., PanelRow]


# ── 만드는 법 ────────────────────────────────────────────────────────────


def test_effects_carry_the_same_numbers_as_run_effect(make_row: Row) -> None:
    """조정 효과는 `run-effect` 와 **같은 함수**로 잰다 — 두 곳에서 따로 계산하지 않는다."""
    before = (
        make_row("14_6", 1, win_rate=0.55, adjusted_next=True, direction_next="nerf"),
        make_row("14_6", 2, win_rate=0.50),
        make_row("14_6", 3, win_rate=0.48),
    )
    after = (
        make_row("14_7", 1, win_rate=0.52),
        make_row("14_7", 2, win_rate=0.505),
        make_row("14_7", 3, win_rate=0.485),
    )
    effect = site_api.champion_files(before + after, names={})[1].effects[0]
    reference = outcomes(before, after)[0]

    assert (effect.patch, effect.next, effect.direction) == ("14_6", "14_7", "nerf")
    assert effect.effect == pytest.approx(reference.adjusted_shift, abs=1e-4)
    assert effect.control_shift == pytest.approx(reference.baseline_shift, abs=1e-4)
    assert effect.intended is reference.worked
    assert effect.closer is reference.closer


def test_history_keeps_every_patch_in_order(make_row: Row) -> None:
    rows = (
        make_row("14_8", 1, win_rate=0.50),
        make_row("14_6", 1, adjusted_next=True, direction_next="buff"),
        make_row("14_7", 1),
    )
    champion = site_api.champion_files(rows, names={"C1": "하나"})[1]

    assert [h.patch for h in champion.history] == ["14_6", "14_7", "14_8"]
    assert champion.history[0].adjusted == outcome(rows[1])  # 실제 결과는 같은 말로
    assert champion.ko == "하나"


def test_the_public_range_check_stops_later_patches() -> None:
    """**직접 집계가 섞이면 멈춘다.** 공개 자료는 u.gg 패널 구간(`16_13`)까지다."""
    site_api.check_public_range(
        {
            "champions/1.json": {"history": [{"patch": "16_13"}]},
            "x.json": {"next": "16_14"},
        }
    )
    with pytest.raises(ValueError, match="공개 범위"):
        site_api.check_public_range(
            {"champions/1.json": {"history": [{"patch": "16_16"}]}}
        )
    with pytest.raises(ValueError, match="공개 범위"):
        site_api.check_public_range({"patches/16_16.json": {}})
    with pytest.raises(ValueError, match="공개 범위"):
        site_api.check_public_range({"x.json": {"next": "16_17"}})
    with pytest.raises(ValueError, match="공개 범위"):  # 글 속에 섞여도
        site_api.check_public_range({"x.json": {"text": "16_16 패치에서 너프"}})


def test_evidence_takes_only_the_fixed_sources() -> None:
    """근거 출처는 ADR 0007 의 값뿐이다 — 직접 집계 같은 새 출처가 끼면 멈춘다."""
    site_api.Evidence(source="R3", text="밴율 23.7%")
    with pytest.raises(ValidationError):
        site_api.Evidence(source="riot", text="직접 집계")  # type: ignore[arg-type]


def test_scores_are_not_called_probabilities() -> None:
    """**점수는 확률이 아니다.** 패치 안에서 줄 세우는 값이라 `score` 로 적는다."""
    fields = set(site_api.Candidate.model_fields)
    assert "score" in fields
    assert not {"prob", "probability"} & fields


# ── 계약: 커밋된 파일 ────────────────────────────────────────────────────


def committed() -> dict[str, Any]:
    return {
        p.relative_to(API).as_posix(): json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(API.rglob("*.json"))
        if "schema" not in p.relative_to(API).parts
    }


def test_the_api_is_committed() -> None:
    files = committed()
    for need in ("index.json", "patches.json", "champions.json", "effects.json"):
        assert need in files, need
    assert any(name.startswith("patches/") for name in files)
    assert any(name.startswith("champions/") for name in files)


def test_the_index_lists_every_file() -> None:
    files = committed()
    assert set(files["index.json"]["paths"]) == set(files) - {"index.json"}


def test_every_committed_file_matches_its_model() -> None:
    for path, data in committed().items():
        site_api.model_for(path).model_validate(data)


def test_schema_files_match_the_models() -> None:
    """스키마 파일은 모델에서 나온다 — 모델을 고치고 스키마를 안 고치면 걸린다."""
    for name, model in site_api.SCHEMAS.items():
        path = API / "schema" / f"{name}.json"
        assert json.loads(path.read_text(encoding="utf-8")) == model.model_json_schema()


def test_nothing_beyond_the_public_range_is_committed() -> None:
    files = committed()
    site_api.check_public_range(files)
    assert files["index.json"]["last"] == site_api.PUBLIC_LAST
    # 출처는 이 셋뿐이다. **직접 집계 출처가 끼면 여기서 걸린다**(extension 2절 4항)
    assert set(files["index.json"]["sources"]) == {"solo", "pro", "answers"}


def test_the_16_13_list_is_what_the_page_published() -> None:
    view = committed()["patches/16_13.json"]
    assert (view["next"], view["candidates"]) == ("16_14", 173)
    assert [c["champion"] for c in view["nerf"]] == PUBLISHED_NERF
    assert [c["champion"] for c in view["buff"]] == PUBLISHED_BUFF


def test_the_page_reads_only_the_api() -> None:
    """**배포 페이지는 `api/v1` 만 읽는다.** 옛 `data.json` 으로 돌아가면 걸린다.

    점수를 「확률」로 적지 않는다 — 「확률이 아닙니다」라고 말할 때만 쓴다.
    """
    page = (ROOT / "docs" / "site" / "index.html").read_text(encoding="utf-8")
    assert "const API = 'api/v1/'" in page
    assert "data.json" not in page
    assert not (ROOT / "docs" / "site" / "data.json").exists()
    assert "prob" not in page
    assert re.findall(r"확률(?!이 아닙니다)", page) == []


def test_the_api_fits_the_size_budget() -> None:
    sizes = [p.stat().st_size for p in API.rglob("*.json")]
    assert sum(sizes) <= 5 * 1024 * 1024
    assert max(sizes) <= 500 * 1024
