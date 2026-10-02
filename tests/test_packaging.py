"""재현 패키징 — Docker 이미지에 **무엇이 들어가면 안 되는지.**

[extension 3단계](../docs/extension.md)와 4절 5 · 6항이다.

    키는 `.env` 에만 둔다. 출력 · 로그 · 커밋 · 이미지 · JSON 어디에도 넣지 않는다.
    `data/` · `runs/` · `.env` 는 커밋하지 않는다. Docker 이미지에도 넣지 않고 볼륨으로 붙인다.

Docker 없이 돈다 — `Dockerfile` 과 `.dockerignore` 의 글을 읽는다. 이미지를 실제로 빌드해
테스트를 돌리고 배포 페이지를 다시 만들어 견주는 것은 `scripts/check-docker` 가 한다.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import re
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
IGNORE = ROOT / ".dockerignore"


def lines(path: Path) -> list[str]:
    """주석과 빈 줄을 뺀 줄."""
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def instructions() -> list[tuple[str, str]]:
    """`Dockerfile` 의 (명령, 나머지). 줄 끝 `\\` 로 이은 줄은 한 명령이다."""
    joined: list[str] = []
    carry = ""
    for line in lines(DOCKERFILE):
        if line.endswith("\\"):
            carry += line[:-1] + " "
            continue
        joined.append(carry + line)
        carry = ""
    return [(i.split(None, 1)[0].upper(), i.split(None, 1)[1]) for i in joined]


def test_there_is_one_dockerfile() -> None:
    """**Dockerfile 하나다**(extension 3단계). 여러 개면 무엇이 기준인지 모른다. compose ·
    K8s 도 두지 않는다(1절 4항)."""
    skip = {".venv", ".git", "node_modules", "data", "runs"}
    found = [
        p.relative_to(ROOT).as_posix()
        for pattern in (
            "Dockerfile*",
            "*.dockerfile",
            "docker-compose*",
            "compose.y*ml",
        )
        for p in ROOT.rglob(pattern)
        if not skip & set(p.relative_to(ROOT).parts)
    ]
    assert found == ["Dockerfile"]


def test_data_runs_and_keys_never_enter_the_image() -> None:
    """`COPY . .` 가 가져가지 못하게 **`.dockerignore` 가 막는다.** 자료와 키는 실행할 때
    볼륨과 환경변수로 붙인다."""
    ignored = set(lines(IGNORE))
    for must in (".env", ".env.*", "data/", "runs/", ".git/", ".venv/"):
        assert must in ignored, must
    # `.env.example` 은 키가 없는 틀이라 넣어도 된다 — 넣는다면 예외로 적혀 있어야 한다
    assert "!.env.example" in ignored


def test_the_dockerfile_carries_no_secret_and_no_data() -> None:
    """이미지에 키를 굽지 않는다 — `ENV` · `ARG` 로도, `COPY` 로도."""
    for command, rest in instructions():
        assert not re.search(r"(?i)api[_-]?key|token|secret|password", rest), rest
        if command in ("COPY", "ADD"):
            sources = rest.split()[:-1]
            for source in sources:
                assert not source.startswith((".env", "data", "runs")), rest


def test_the_image_is_pinned_like_the_project() -> None:
    """**같은 것을 다시 만들 수 있어야 한다.** 파이썬은 CI 와 같은 3.11 의 정확한 판이고,
    의존성은 고정한 요구 파일로만 깐다([ADR 0001](../docs/adr/0001-python-environment-and-tooling.md))
    — 이름만 적은 `pip install` 은 그날의 최신 판을 깐다."""
    froms = [rest for command, rest in instructions() if command == "FROM"]
    assert len(froms) == 1  # 단계가 하나다
    assert re.fullmatch(r"python:3\.11\.\d+-slim(-[a-z]+)?", froms[0].split()[0]), froms

    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert 'python-version: "3.11"' in ci

    installs = [rest for command, rest in instructions() if "pip install" in rest]
    assert installs, "의존성을 까는 줄이 없다"
    for rest in installs:
        packages = re.findall(r"pip install\s+(.*?)(?:&&|$)", rest)
        for args in packages:
            assert "-r requirements-dev.txt" in args, rest
            named = [
                a
                for a in args.split()
                if not a.startswith("-") and "requirements" not in a
            ]
            assert named == [], f"고정하지 않은 패키지: {named}"


def test_the_image_is_not_a_server() -> None:
    """**서버를 두지 않는다**(ADR 0016). 이미지는 테스트와 스크립트를 돌리는 것이지 떠
    있는 것이 아니다 — 포트를 열지 않고, 기본 명령은 테스트다."""
    commands = dict(instructions())
    assert "EXPOSE" not in commands
    assert "pytest" in commands["CMD"]


# ── 실제로 돌려 보는 스크립트 — 무엇을 새는 것으로 보나 ──────────────────


def check_docker() -> ModuleType:
    path = str(ROOT / "scripts" / "check-docker")
    loader = importlib.machinery.SourceFileLoader("check_docker", path)
    spec = importlib.util.spec_from_loader("check_docker", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_a_clean_image_has_nothing_to_report() -> None:
    found = check_docker().leaks(
        ["src", "tests", "docs", ".env.example", "Dockerfile"],
        ["PATH=/usr/local/bin", "PYTHONUNBUFFERED=1", "PIP_NO_CACHE_DIR=1"],
        "RUN pip install -r requirements-dev.txt\nCOPY --chown=app:app . .",
        "1000\n",
    )
    assert found == []


def test_leaks_are_named_but_their_values_are_not_printed() -> None:
    """이미지에 키 · 자료가 들어갔으면 **무엇이 들어갔는지**를 말한다. **값은 적지 않는다** —
    새는 것을 알리다가 화면과 로그에 키를 찍으면 안 된다(extension 4절 5항)."""
    found = check_docker().leaks(
        [".env", "data", "runs", "src"],
        ["OPENAI_API_KEY=sk-this-must-not-be-printed", "PATH=/usr/bin"],
        "ENV RIOT_API_KEY=RGAPI-this-must-not-be-printed",
        "0\n",
    )
    text = "\n".join(found)
    assert len(found) == 6  # .env · data · runs · 환경변수 · 빌드 이력 · 루트
    assert "OPENAI_API_KEY" in text and "루트" in text
    assert "must-not-be-printed" not in text


def test_the_report_table_stops_before_the_run_specific_lines() -> None:
    """`run-report` 는 끝에 「저장: runs/report-<시각>.json」을 찍는다 — 실행마다 다르다.
    견줄 것은 그 앞의 표다."""
    report = (
        "arm   R-정확도\nA7p   27.4%\n\n저장: runs/report-20261002T011239Z.json\n꼬리\n"
    )
    assert check_docker().table(report) == ["arm   R-정확도", "A7p   27.4%"]
