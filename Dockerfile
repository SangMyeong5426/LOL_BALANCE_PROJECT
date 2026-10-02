# 재현 패키징 — 새 기계에서 테스트를 돌리고, 배포 페이지를 다시 만들어 커밋된 것과 견준다.
#
#   docker build -t lol-balance .
#   docker run --rm lol-balance                                   # 테스트 — 자료 없이 돈다
#   docker run --rm -v "$PWD/data:/app/data:ro" lol-balance ./scripts/make-site --check
#
# **자료와 키는 이미지에 넣지 않는다.** `.dockerignore` 가 `data/` · `runs/` · `.env` 를 막고,
# 실행할 때 볼륨과 환경변수로 붙인다(docs/extension.md 4절 5 · 6항).
# **서버가 아니다** — 포트를 열지 않고 기본 명령은 테스트다(ADR 0016).
# 빌드부터 견주기까지 한 번에 보려면 ./scripts/check-docker 를 돌린다.

# 파이썬은 CI 와 같은 3.11 의 정확한 판으로 고정한다. 올릴 때는 .github/workflows/ci.yml 과 같이 본다.
FROM python:3.11.15-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 의존성을 코드보다 먼저 깐다 — 코드를 고쳐도 다시 깔지 않는다.
# 판을 고정한 요구 파일로만 깐다(docs/adr/0001).
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

# 루트로 돌리지 않는다. 작업 폴더는 그 사용자의 것이어야 한다 — 스크립트가 `runs/` 를
# 만든다(`run-report` 가 `Permission denied: /app/runs` 로 멈춘 적이 있다).
RUN useradd --create-home --uid 1000 app && chown app:app /app
COPY --chown=app:app . .
USER app

# 캐시를 쓰지 않는다 — 읽기 전용으로 돌려도 테스트가 돈다.
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
