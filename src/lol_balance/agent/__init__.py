"""에이전트 — 조회→검색→판단 루프를 모델이 돈다. arm `B8`.

LangChain 으로 만들고 로컬 모델(Ollama)로 돌린다 — **API 키가 필요 없다.**
근거는 `docs/adr/0009-agent-framework-and-local-model.md`, 쓰는 법은
`docs/agent.md`.

    data      패널·노트·규칙을 한 번만 싣는다
    tools     R1·R2·R3 를 `@tool` 로 감싼다. 경계(`as_of`)는 만들 때 박는다
    schema    답 형식 — ADR 0007 을 Pydantic 으로 옮겼다
    judge     판단 에이전트와 해설(체인 · 에이전트)
    evaluate  ② 방향을 `B5s` · `B6` 과 같은 표본에서 잰다
    followup  해설에 대해 더 묻는다 — 대화가 파일에 남는다

저장소 밖(`skala-langchain`)에서 만들어 재고 2026-09-18 에 들여왔다. 밖에 있는
동안 이 패키지의 모듈 13개를 `sys.path` 로 끌어다 썼고, 그 결합이 들여온 이유다.
"""
