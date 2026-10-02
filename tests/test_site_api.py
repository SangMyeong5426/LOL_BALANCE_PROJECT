"""정적 API — 모양, 공개 범위, 공개된 목록이 지켜지는지.

두 무리다.

- **만드는 법** — 작은 가짜 패널로 효과 · 이력 · 공개 범위 검사를 본다
- **계약** — 커밋된 `docs/site/api/v1/` 전부를 모델로 읽고, 공개 범위를 넘는 자료가
  없는지, 공개한 목록이 그대로인지, 크기 예산 안인지 본다. CI 에는 `data/` 가 없어
  다시 만들 수 없으므로 **커밋된 파일**을 검사한다([extension 2절](../docs/extension.md))
"""

from __future__ import annotations

import json
import math
import random
import re
from collections.abc import Callable, Iterator
from decimal import ROUND_HALF_UP, Decimal
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


# ── 값을 줄이는 법 — 페이지가 다시 줄여도 한 번 줄인 것과 같게 ───────────
#
# 페이지의 글자 꾸미기(`docs/site/index.html` 의 `cut` · `pc` · `pp` · `two`)를 **한 줄씩
# 그대로** 옮긴 것이다. JS 의 `toFixed` 는 이진수의 정확한 값을 줄이고 딱 중간이면 올린다.


def js_fixed(y: float, places: int) -> str:
    """`Number.prototype.toFixed`."""
    return str(Decimal(y).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def cut(y: float, places: int) -> str:
    odd = y * 2 ** (places + 1)
    if odd != int(odd) or int(odd) % 2 == 0:
        return js_fixed(y, places)
    low = math.floor(y * 10**places)
    return js_fixed((low + 1 if low % 2 else low) / 10**places, places)


def pc(x: float) -> str:
    return cut(x * 100, 1) + "%"


def pp(x: float) -> str:
    return ("−" if x < 0 else "+") + cut(abs(x * 100), 1) + "%p"


def two(x: float) -> str:
    return cut(x, 2)


# 로컬 화면과 도구가 같은 값을 적는 법 — 파이썬의 글자 꾸미기다


def py_pc(v: float) -> str:
    return f"{v:.1%}"


def py_pp(v: float) -> str:
    return ("−" if v < 0 else "+") + f"{abs(v * 100):.1f}%p"


def py_two(v: float) -> str:
    return f"{v:.2f}"


def test_values_are_cut_once_not_twice() -> None:
    """**두 번 줄이면 틀린다.** API 가 0.48951 을 넷째 자리로 줄여 0.4895 로 주면, 페이지가
    그것을 `%` 첫째 자리로 다시 줄일 때 48.9% 와 49.0% 의 딱 중간이다 — 원래 값을 한 번
    줄이면 49.0% 인데 페이지에는 48.9% 가 나왔다. 공개 페이지의 값 4.5% 가 그렇게 0.1%p
    어긋나 있었다(2026-10-02, 38,566개 중 1,741개).

    줄인 값이 중간에 걸리면 **자리를 늘려** 원래 값이 어느 쪽인지 남긴다."""
    kept = site_api.keep(0.48951686417502277, site_api.RATE)
    assert kept == 0.48952 and pc(kept) == "49.0%" == py_pc(0.48951686417502277)
    assert js_fixed(0.4895 * 100, 1) == "48.9"  # 고치기 전에 페이지가 보이던 값

    below, above = 0.74498, 0.74502  # 넷째 자리로 줄이면 둘 다 0.745 다
    assert two(site_api.keep(below, site_api.SCORE)) == "0.74"
    assert two(site_api.keep(above, site_api.SCORE)) == "0.75"

    # 중간에 걸리지 않으면 넷째 자리 그대로다 — 공개한 값의 대부분은 바뀌지 않는다
    assert site_api.keep(0.53412, site_api.RATE) == 0.5341
    assert site_api.keep(0.9127, site_api.SCORE) == 0.9127

    # 0 으로 줄면 부호가 사라진다. 조금 내린 것은 내린 것으로 남긴다
    tiny = site_api.keep(-0.0000236, site_api.RATE)
    assert tiny == -0.00002 and pp(tiny) == "−0.0%p" and pp(-0.0) == "+0.0%p"
    assert site_api.keep(0.0, site_api.RATE) == 0.0


def test_the_page_shows_what_the_local_tools_show() -> None:
    """어떤 값이든 **페이지가 보이는 것 = 파이썬이 원래 값을 한 번 줄여 적은 것**이다 — 로컬
    화면 · 도구 · 근거 글과 같다. 중간 근처를 일부러 많이 넣는다. 틀리는 곳이 거기뿐이다."""
    rng = random.Random(20260824)
    values = [rng.random() for _ in range(5000)]
    values += [
        k / 1000 + 0.0005 + nudge
        for k in range(0, 1000, 7)
        for nudge in (-6e-5, -3e-6, -4e-7, -2e-9, 0.0, 2e-9, 4e-7, 3e-6, 4e-5)
    ]
    # 개수의 비 — 원래 값이 딱 중간일 수 있다(1/80 = 1.25%). 프로 픽·밴율이 이런 값이다
    values += [k / n for n in (16, 40, 80, 160, 200, 400, 2000) for k in range(n)]
    for v in values:
        assert pc(site_api.keep(v, site_api.RATE)) == py_pc(v), v
        assert pp(site_api.keep(-v / 5, site_api.RATE)) == py_pp(-v / 5), v
        assert two(site_api.keep(v, site_api.SCORE)) == py_two(v), v


def test_an_exact_half_goes_to_the_even_side_like_python() -> None:
    """원래 값이 **딱 중간**이면(1/80 = 1.25%) 줄일 자리를 늘려도 중간이다. `toFixed` 는 그것을
    늘 올려 1.3% 로 적고 파이썬은 짝수 쪽인 1.2% 로 적는다 — 같은 페이지의 근거 글(파이썬이
    적었다)과 표가 달랐다. 페이지가 파이썬을 따른다."""
    assert site_api.keep(1 / 80, site_api.RATE) == 0.0125  # 자리를 늘려도 그대로다
    assert js_fixed(0.0125 * 100, 1) == "1.3" and py_pc(1 / 80) == "1.2%"
    assert pc(1 / 80) == "1.2%" and pc(3 / 80) == "3.8%" and pc(27 / 80) == "33.8%"
    assert two(0.125) == py_two(0.125) == "0.12" and two(0.375) == "0.38"
    # 중간처럼 보여도 이진수로는 한쪽에 있는 값 — 파이썬도 `toFixed` 도 그쪽으로 간다
    assert pc(0.0115) == py_pc(0.0115) == "1.1%"
    assert pc(0.0135) == py_pc(0.0135) == "1.4%"


def test_the_page_cuts_where_the_api_expects() -> None:
    """API 는 **페이지가 어느 자리로 줄이는지**를 알고 그 중간을 피한다. 페이지가 자리나 줄이는
    법을 바꾸면 여기가 깨진다 — `site_api.RATE` · `SCORE` 와 위의 옮겨 적은 함수를 같이 고친다."""
    page = (ROOT / "docs" / "site" / "index.html").read_text(encoding="utf-8")
    for line in (
        "const cut = (y, places) => {",
        "  const odd = y * 2 ** (places + 1);",
        "  if (!Number.isInteger(odd) || odd % 2 === 0) return y.toFixed(places);",
        "  const low = Math.floor(y * 10 ** places);",
        "  return ((low % 2 ? low + 1 : low) / 10 ** places).toFixed(places);",
        "const pc = x => x == null ? '—' : cut(x * 100, 1) + '%';",
        "const pp = x => (x < 0 ? '−' : '+') + cut(Math.abs(x * 100), 1) + '%p';",
        "const two = x => cut(x, 2);",
    ):
        assert line in page, line
    assert (site_api.RATE, site_api.SCORE) == (3, 2)


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


def decimals(data: Any, key: str = "") -> Iterator[tuple[str, float]]:
    """응답 안의 (칸 이름, 소수 값) 전부."""
    if isinstance(data, dict):
        for name, value in data.items():
            yield from decimals(value, name)
    elif isinstance(data, list):
        for value in data:
            yield from decimals(value, key)
    elif isinstance(data, float):
        yield key, data


def test_committed_values_were_cut_once() -> None:
    """커밋된 비율 · 점수 가운데 **페이지가 줄이는 자리의 딱 중간에 있는 것은 27개뿐이다** —
    전부 원래 값이 딱 중간인 것이다(개수의 비: 1/80 = 1.25%). 넷째 자리로 뭉텅 줄이던 때에는
    3,654개가 중간에 걸려 있었고 그 절반쯤이 0.1%p 어긋나 보였다. 두 번 줄인 값이 다시
    공개되는 것을 자료 없이도 막는다."""
    seen, halfway = 0, []
    for path, data in committed().items():
        for key, value in decimals(data):
            shown = site_api.SHOWN[key]  # 모르는 소수 칸이 생기면 여기서 멈춘다
            seen += 1
            if site_api.on_boundary(value, shown):
                halfway.append((path, key, value))
    assert seen == 38_566
    assert len(halfway) == 27, halfway[:5]


def test_the_api_fits_the_size_budget() -> None:
    sizes = [p.stat().st_size for p in API.rglob("*.json")]
    assert sum(sizes) <= 5 * 1024 * 1024
    assert max(sizes) <= 500 * 1024
