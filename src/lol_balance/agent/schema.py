"""에이전트의 답 형식 — ADR 0007 을 Pydantic 으로 옮겼다. 교재 3장 Structured Output.

ADR 0007 이 이 자리를 예견했다:

    에이전트가 낼 답의 형식을 안 정하고 루프를 붙이면, 답이 자유 서술로 나오고
    그때 가서 파서를 짜게 된다.

그래서 `with_structured_output` / `response_format` 으로 **처음부터 스키마로
받는다.** 자연어에서 값을 뽑는 파서는 만들지 않는다 (ADR 0007 선택지 B 기각).

## 경고는 여기 없다

ADR 0007 저장 형식에는 `warnings[]` 가 있지만 **모델이 채우는 칸이 아니다.**
세 경고(대회 출전 · 지나치게 내려감 · 아이템)는 `explain.py` 가 규칙으로 낸다.
CLAUDE.md — *규칙으로 잡히는 것을 LLM 에 보내지 않는다.* 저장할 때 코드가 붙인다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Evidence(BaseModel):
    source: Literal["R1", "R2", "R3", "수치"] = Field(
        description=(
            "근거를 어디서 얻었나. R1=사례 검색, R2=노트 검색, R3=수치 조회, "
            "수치=처음에 받은 기준 패치 지표"
        )
    )
    text: str = Field(description="근거 한 줄. 도구가 준 숫자를 그대로 쓴다")


class Judgment(BaseModel):
    adjust_prob: int = Field(
        ge=0,
        le=100,
        description="다음 패치에 이 챔피언이 조정될 확률 (0~100 정수). ① 대상",
    )
    nerf_prob: int = Field(
        ge=0,
        le=100,
        description=(
            "조정된다면 너프일 확률 (0~100 정수). 50 은 반반이다. ② 방향. "
            "버프 쪽이 유력하면 50 보다 작게 준다"
        ),
    )
    reason: str = Field(description="판단 이유 두세 문장. 도구로 확인한 것만 쓴다")
    abstain: bool = Field(
        default=False,
        description=(
            "근거가 부족해 판단할 수 없으면 참. 그때 점수는 참고용이다. "
            "모르는 것을 50 으로 적으면 「반반이다」와 구별이 안 된다 (ADR 0007)"
        ),
    )
    evidence: list[Evidence] = Field(
        default_factory=list, description="판단에 쓴 근거. 출처를 붙인다"
    )


class StrictJudgment(Judgment):
    """**「50」을 받지 않는다 — 모르면 기권한다.**

    첫 평가에서 로컬 2B 의 답 48% 가 정확히 50 이었다. ADR 0007 이 경고한 대로
    「반반이다」와 「모른다」가 구별되지 않고 순위를 못 매긴다. 50 을 적으면 검증이
    실패하고, LangChain 이 그 오류를 모델에게 돌려줘 다시 쓰게 한다.

    **효과는 개발 표본에서 본다.** 평가 표본을 보며 고르면 그것이 누출이다.
    도구 이름이 클래스 이름에서 나오므로 `Judgment` 로 둔다 — 프롬프트가 그 이름을 쓴다.
    """

    model_config = ConfigDict(title="Judgment")

    @model_validator(mode="after")
    def _no_fifty(self) -> StrictJudgment:
        if not self.abstain and self.nerf_prob == 50:
            raise ValueError(
                "nerf_prob 50 은 쓸 수 없다 — 「반반이다」와 「모른다」가 구별되지 않는다. "
                "한쪽으로 기울면 그 값을 쓰고, 판단할 근거가 없으면 abstain 을 참으로 둔다."
            )
        return self


StrictJudgment.__name__ = "Judgment"
StrictJudgment.__qualname__ = "Judgment"


# ── 해설 — 화면이 쓰는 형식 ───────────────────────────────────────────


class Point(BaseModel):
    source: Literal["R1", "R2", "R3", "베이스라인", "수치"] = Field(
        description=(
            "어디서 얻었나. R1=사례 검색, R2=노트 검색, R3=수치 조회, "
            "베이스라인=주어진 통계 모델 점수, 수치=주어진 기준 패치 지표"
        )
    )
    text: str = Field(description="한 줄. 도구가 준 숫자를 그대로 쓴다")


class Explanation(BaseModel):
    """베이스라인의 판단을 **해설**한다. 확률을 새로 매기지 않는다.

    화면에 확률을 두지 않는 이유 — 에이전트의 조정 확률은 어디서도 검증되지
    않았고, 방향 확률은 로컬 2B 에서 B5s 보다 유의하게 나빴다. 검증된 베이스라인
    옆에 같은 무게로 놓으면 오독된다. 해설자는 **근거를 붙이는** 일을 한다.
    """

    summary: str = Field(description="베이스라인이 왜 이렇게 봤는지 한두 문장으로")
    supporting: list[Point] = Field(
        default_factory=list, description="베이스라인의 판단을 뒷받침하는 근거"
    )
    against: list[Point] = Field(
        default_factory=list,
        description="베이스라인과 **다른** 신호. 없으면 비운다. 숨기지 않는다",
    )
    baseline_side: Literal["너프", "버프"] = Field(
        description="주어진 베이스라인 점수에서 너프와 버프 중 **더 높은 쪽**을 그대로 옮겨 적는다"
    )
    tension_quote: str = Field(
        default="",
        description="tension 을 적었다면 부딪히는 경고의 **원문을 한 글자도 바꾸지 않고** 옮긴다",
    )
    tension: str = Field(
        default="",
        description=(
            "베이스라인의 판단과 **코드가 낸 경고**가 부딪히는 지점. "
            "예: 버프 후보인데 「버프 주의」 경고가 붙었다. 경고가 없으면 비운다. "
            "새 경고를 만들지 않는다"
        ),
    )
    stance: Literal["동의", "부분 동의", "반대", "판단 보류"] = Field(
        description="베이스라인이 본 조정 여부·방향에 대한 입장"
    )
