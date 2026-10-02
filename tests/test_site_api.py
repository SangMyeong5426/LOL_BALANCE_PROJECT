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


# ── 다시 만들어 견준다 — 쓰지 않고 본다 ─────────────────────────────────


def small_api() -> dict[str, Any]:
    summary = site_api.PatchSummary(id="15_14", next="15_15", candidates=3)
    return {"patches.json": site_api.PatchList(split="15_13", patches=[summary])}


def test_a_fresh_write_is_never_stale(tmp_path: Path) -> None:
    """**같은 자료로 다시 만들면 바이트까지 같은가**(extension 3단계). 방금 쓴 것을 다시
    만들어 견주면 다른 것이 없어야 한다 — 응답도 스키마도."""
    written = site_api.write(tmp_path, small_api())
    assert site_api.stale(tmp_path, small_api()) == []
    assert {p.relative_to(tmp_path / site_api.API_DIR).as_posix() for p in written} == {
        "patches.json",
        *(f"schema/{name}.json" for name in site_api.SCHEMAS),
    }


def test_stale_names_what_differs_and_writes_nothing(tmp_path: Path) -> None:
    """다른 것을 **종류별로 말하고, 디스크는 건드리지 않는다.** 검사가 고쳐 버리면 무엇이
    달랐는지 남지 않는다 — 자료를 읽기 전용으로 붙인 컨테이너에서도 돌아야 한다."""
    site_api.write(tmp_path, small_api())
    root = tmp_path / site_api.API_DIR
    changed = root / "patches.json"
    changed.write_bytes(changed.read_bytes().replace(b"15_14", b"15_12"))
    (root / "old.json").write_text("{}\n", encoding="utf-8")
    (root / "schema" / "patch.json").unlink()
    before = {p: p.read_bytes() for p in root.rglob("*.json")}

    problems = site_api.stale(tmp_path, small_api())

    assert problems == [
        "다르다: patches.json",
        "없다: schema/patch.json",
        "남았다: old.json",
    ]
    assert {p: p.read_bytes() for p in root.rglob("*.json")} == before


def test_writing_removes_what_is_no_longer_made(tmp_path: Path) -> None:
    """없어진 응답과 **없어진 스키마**를 지운다 — 남아 있으면 「다시 만들어도 같다」가 늘
    거짓이 된다."""
    site_api.write(tmp_path, small_api())
    root = tmp_path / site_api.API_DIR
    (root / "patches").mkdir()
    (root / "patches" / "13_1.json").write_text("{}\n", encoding="utf-8")
    (root / "schema" / "gone.json").write_text("{}\n", encoding="utf-8")

    site_api.write(tmp_path, small_api())

    assert not (root / "patches" / "13_1.json").exists()
    assert not (root / "schema" / "gone.json").exists()
    assert site_api.stale(tmp_path, small_api()) == []


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


def test_the_page_carries_the_riot_legal_notice() -> None:
    """Riot 일반 정책은 제품 화면에 법적 고지를 **보이게** 싣도록 한다.

    Production 키를 신청하려면 먼저 있어야 하고, 받은 뒤에도 빠지면 안 된다.
    문구는 Riot 이 준 그대로다 — 우리 말로 바꾸지 않는다.
    """
    page = (ROOT / "docs" / "site" / "index.html").read_text(encoding="utf-8")
    assert "LOL Balance Project isn't endorsed by Riot Games" in page
    assert "registered trademarks of Riot Games, Inc." in page


def test_the_riot_verification_file_holds_only_the_code() -> None:
    """Riot 개발자 포털의 제품 확인 파일. **코드 말고 아무것도 없어야 한다** — 줄바꿈도.

    포털이 `…/LOL_BALANCE_PROJECT/riot.txt` 를 읽어 제품 주인을 확인한다. 편집기나
    커밋 훅이 끝에 줄바꿈을 붙이면 확인이 깨질 수 있어 여기서 막는다.
    """
    body = (ROOT / "docs" / "site" / "riot.txt").read_bytes()
    assert re.fullmatch(rb"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", body)


def test_the_api_fits_the_size_budget() -> None:
    sizes = [p.stat().st_size for p in API.rglob("*.json")]
    assert sum(sizes) <= 5 * 1024 * 1024
    assert max(sizes) <= 500 * 1024
