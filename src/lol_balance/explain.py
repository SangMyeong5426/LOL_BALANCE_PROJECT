"""후보에 **근거와 경고**를 붙인다.

**순위만으로는 도구가 안 된다.** 「이 챔피언을 봐야 한다」 옆에 **왜 그런지**와
**놓치면 사고가 나는 것**이 있어야 사람이 판단할 수 있다. 목표는
[AGENTS.md](../../AGENTS.md) 의 「무엇을 만드는가」에 있다.

## 근거는 지어내지 않는다

전부 이미 잰 것에서 온다 — 규칙 12개(`rules/proposed.jsonl`), 사례 검색,
프로 픽·밴율, 그 패치 안에서의 순위. **모델이 왜 그 점수를 냈는지를 설명하는
것이 아니라, 사람이 볼 만한 사실을 모아 주는 것이다.** 둘은 다르고, 후자만
한다.

## 경고 넷

    올려 주면 프로 경기가 터진다   프로 단골 버프 시 대회 출전 +37% (그 외 ±0%)
    지나치게 내려갈 수 있다        높은 쪽 너프는 16% 가 승률 48% 아래로 간다
    표본이 얇다                   판수가 적으면 승률이 요동친다
    아이템이 크게 바뀌었다          그 패치의 승률 변화를 챔피언 조정으로만 읽으면 안 된다

**마지막 것만 챔피언이 아니라 패치에 붙는다**(`patch_notes`). 챔피언마다 띄우면
그 패치 목록 전체에 붙어 「항상 뜨는 경고」가 된다.

근거는 [results](../../docs/results/README.md).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from lol_balance.items import LOUD, Churn
from lol_balance.panel import PanelRow
from lol_balance.retrieval import Case
from lol_balance.rules import Rule

# 「프로 단골」 문턱. 평생 평균 프로 픽·밴율 상위 25% 가 이 값이다.
# 근거는 docs/glossary.md — 여기서 다시 계산하지 않고 넘겨받는다.
PRO_REGULAR = 0.182

# 그 패치에서 이 정도로 밴당하면 그 자체가 신호다. 학습 구간에서 규칙
# `A-ban` 이 쓰는 문턱과 같다.
HIGH_BAN = 0.20

# 판수가 이보다 적으면 승률을 그대로 믿지 않는다. 패널의 `MIN_MATCHES` 는
# 200 이지만 그것은 「버릴 것」의 선이고, 이것은 「주의할 것」의 선이다.
THIN_MATCHES = 20_000

# 승률이 이 폭 안이면 어느 쪽으로도 안 기운다. 규칙이 못 덮는 자리다.
FLAT = 0.005


@dataclass(frozen=True)
class Note:
    """근거 한 줄. `warn` 이면 경고로 표시한다."""

    text: str
    warn: bool = False


# 방향 넷을 화면에 적는 이름. **둘만 한글이면 나머지가 오류처럼 보인다** —
# 「실제 mixed」가 그렇게 읽혔다. 용어집이 넷을 영문 키로 정의하므로 이름은
# 그대로 두고 짧은 설명만 붙인다(`docs/glossary.md` 「용어를 새로 만들지 않는다」).
DIRECTION_NAMES = {
    "nerf": "너프",
    "buff": "버프",
    "mixed": "mixed(방향이 갈림)",
    "adjust": "adjust(방향 없음)",
}


def outcome(row: PanelRow) -> str:
    """그 챔피언이 실제로 어떻게 됐나. `predict` 와 `ask` 가 같이 쓴다."""
    if not row.adjusted_next:
        return "조정 안 됨"
    key = row.direction_next or ""
    return DIRECTION_NAMES.get(key, key or "방향 미상")


def _rank_in_patch(row: PanelRow, patch_rows: Sequence[PanelRow], field: str) -> int:
    """그 패치 안에서 몇 위인가. **절대값보다 이게 읽힌다.**"""
    values = sorted(
        (getattr(r, field) or 0.0 for r in patch_rows),
        reverse=True,
    )
    mine = getattr(row, field) or 0.0
    return values.index(mine) + 1


def reasons(
    row: PanelRow,
    patch_rows: Sequence[PanelRow],
    cases: Sequence[Case] = (),
    rules: Sequence[Rule] = (),
    lifetime_pro: float | None = None,
    considering: str | None = None,
) -> list[Note]:
    """이 챔피언을 봐야 하는 이유와 주의할 점.

    `lifetime_pro` 는 그 챔피언의 **평생 평균** 프로 픽·밴율이다. 한 패치 값이
    아니라 평생 값으로 「프로 단골인가」를 본다 — 한 패치만 튀는 것과 늘 나오는
    것은 다르고, [기전 조사](../../docs/followups.md)에서 그 둘이 갈렸다.
    """
    out: list[Note] = []
    n = len(patch_rows)

    ban = row.ban_rate or 0.0
    if ban >= HIGH_BAN:
        rank = _rank_in_patch(row, patch_rows, "ban_rate")
        out.append(Note(f"밴율 {ban:.1%} — {n}종 중 {rank}위"))

    gap = row.win_rate - 0.5
    if abs(gap) >= FLAT:
        rank = _rank_in_patch(row, patch_rows, "win_rate")
        out.append(Note(f"승률 {row.win_rate:.1%} — {n}종 중 {rank}위"))
    else:
        out.append(
            Note(f"승률 {row.win_rate:.1%} — 5할에 붙어 있어 어느 쪽으로도 안 기운다")
        )

    if row.d_win_rate is not None and abs(row.d_win_rate) >= 0.01:
        way = "오르는" if row.d_win_rate > 0 else "내리는"
        out.append(Note(f"직전 대비 {row.d_win_rate:+.1%} — {way} 중"))

    # **점수를 바꾸는 값은 화면에도 나와야 한다.** 프로 픽·밴율은 `A7p` · `B5p`
    # 의 피처라 순위를 만드는 데 쓰이는데, 한때 경고로만 나왔다. 경고는 문턱
    # (`PRO_REGULAR`)을 넘어야 뜨므로 44종에서만 보였고 **나머지 126종은 점수가
    # 조용히 달라졌다.** 근거는 문턱 없이 값이 있으면 낸다 — 「없다」와 「낮다」는
    # 다르고, 낮다는 것 자체가 판단 재료다.
    if row.pro_presence is not None:
        rank = _rank_in_patch(row, patch_rows, "pro_presence")
        out.append(Note(f"프로 픽·밴율 {row.pro_presence:.1%} — {n}종 중 {rank}위"))

    fired = [r for r in rules if r.fires(row)]
    if fired:
        out.append(Note("걸린 규칙 " + " · ".join(r.id for r in fired[:4])))

    directed = [c for c in cases if c.row.direction_next in ("nerf", "buff")]
    if directed:
        nerf = sum(c.row.direction_next == "nerf" for c in directed)
        out.append(
            Note(
                f"닮은 사례 {len(directed)}종 — 너프 {nerf} · "
                f"버프 {len(directed) - nerf}"
            )
        )

    out.extend(warnings(row, lifetime_pro, considering))
    return out


def warnings(
    row: PanelRow,
    lifetime_pro: float | None = None,
    considering: str | None = None,
) -> list[Note]:
    """**놓치면 사고가 나는 것.** 순위보다 이쪽이 도구의 값이다.

    `considering` 은 지금 무엇을 검토 중인지다(`"nerf"` · `"buff"` · `None`).
    **항상 뜨는 경고는 경고가 아니다** — 버프 후보는 정의상 승률이 5할 아래이니
    「5할 아래다」를 거기 띄우면 전부에 붙어 아무 뜻이 없어진다. 그래서 무엇을
    하려는지에 따라 걸리는 것만 낸다.
    """
    out: list[Note] = []
    regular = lifetime_pro is not None and lifetime_pro >= PRO_REGULAR

    if regular and considering != "nerf" and row.win_rate < 0.5:
        out.append(
            Note(
                f"프로 단골이다(평생 픽·밴율 {lifetime_pro:.1%}). "
                "승률이 낮아도 올려 주면 대회 출전이 크게 뛴다 — 버프 주의",
                warn=True,
            )
        )
    elif regular and considering != "buff":
        out.append(
            Note(
                f"프로 단골이다(평생 픽·밴율 {lifetime_pro:.1%}). "
                "너프하면 솔랭이 5할 아래로 밀릴 수 있다",
                warn=True,
            )
        )

    # 너프를 검토 중일 때만 뜻이 있다. 버프 후보에 띄우면 전부에 붙는다.
    if considering != "buff" and row.win_rate < 0.5:
        out.append(
            Note(
                f"이미 5할 아래다({row.win_rate:.1%}). "
                "여기서 더 내리면 균형에서 멀어진다",
                warn=True,
            )
        )

    if row.matches < THIN_MATCHES:
        out.append(
            Note(f"판수 {row.matches:,} — 표본이 얇아 승률이 요동칠 수 있다", warn=True)
        )

    return out


def patch_notes(item_churn: Churn | None) -> list[Note]:
    """**패치 전체에 붙는 경고.** 챔피언별 경고와 자리를 나눈다.

    아이템 변경은 정답지에 없다. 라벨은 패치 노트의 챔피언 절에서 오는데, 조정의
    상당수가 아이템으로 이뤄진다. Lucian–Nami 가 `14_9` 24.0% 에서 `14_10` 6.0%
    로 무너졌을 때 두 챔피언 모두 「조정 안 됨」이었고, 무너뜨린 것은
    `Essence Reaver` 의 골드가 2900 에서 3200 으로 오른 것이었다.

    **어느 챔피언이 그 아이템을 사는지는 모른다.** 그래서 「이 챔피언이 흔들린다」
    가 아니라 「이 패치를 챔피언 조정만으로 읽지 마라」까지만 말한다.
    """
    if item_churn is None or item_churn.finished < LOUD:
        return []
    return [
        Note(
            f"이 패치는 아이템이 크게 바뀌었다 — 완성템 {item_churn.finished}종 "
            f"(중앙값 1종). 승률 변화를 챔피언 조정으로만 읽으면 안 된다",
            warn=True,
        )
    ]
