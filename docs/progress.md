# L2 Progress

## 2026-05-30

Implemented the first standalone L2 baseline from the four HTML specs.

- E0: package scaffold, scoring worker entrypoint, injectable `FakeLLM`, migration DDL.
- E1: `AnalysisProvider`, `StubAnalysisProvider`, fixtures, idempotent `content.analyzed` consumption.
- E2: `verticals` lens loader with `ai-coding` seed, source-filter driven relevance gate.
- E3: scoring graph with score -> reflect -> refine, six legal dimensions, taxonomy-bounded tags.
- E4: deterministic term identification and bilingual translation products.
- E5/E8: in-memory repository for owned L2 tables, review queue, status transitions, outbox `content.completed`.
- E6/E7: deterministic recommendation and SSE-style companion stream.
- E9: rubric-version reprocess entry and cost counters are represented in repository state.
- Persistence: added SQLAlchemy table metadata, `SqlAlchemyJudgmentRepository`, Alembic env/config, and migration aligned to L2-owned tables only.
- Contract API: added `judgment_graph.service.recommend(user_id, vertical, limit)` and `companion(content_id, question)` facade with injectable dependencies.
- LangGraph: added `build_scoring_graph()` for the score -> tag -> reflect -> refine path; `test_langgraph_scoring.py` now executes the real StateGraph path with `FakeLLM`.
- Offline contract gates: added `judgment_graph.scripts.verify_contracts` to check L2-owned table scope, no L3 imports, and E0-E9 feature artifacts.
- Arq worker contract: added async worker test proving `score(ctx, content_id)` consumes `content.analyzed` and writes L2 products.
- Migration verification: Alembic offline SQL generation now passes and emits PostgreSQL JSONB columns plus `CREATE EXTENSION IF NOT EXISTS vector`.
- Dependency reproducibility: raised `langgraph` lower bound to 1.0.4 and `pydantic` lower bound to 2.7.4 to avoid the local Python 3.12 forward-ref incompatibility observed with pydantic 2.5.3/langsmith 0.1.131/langchain_core 0.3.9.
- Repository abstraction: graph/review/recommend/service now depend on the `JudgmentRepository` protocol, so `InMemoryJudgmentRepository` and `SqlAlchemyJudgmentRepository` are interchangeable.
- DoD audit: added `judgment_graph.scripts.audit_dod`, which prints PASS/PENDING evidence for each final acceptance area.
- CI usability: Makefile now uses POSIX `PYTHONPATH=...` commands for `make test/lint/typecheck/l2-smoke/l2-contracts/l2-audit`; Windows PowerShell equivalents remain documented in README.
- Integration harness: added `docker-compose.integration.yml` plus `judgment_graph.scripts.integration_check` to run live PostgreSQL/pgvector migration checks, Redis connectivity checks, and packaged worker contract checks when services are available.
- Worker packaging: moved the scoring worker into `judgment_graph.workers.scoring.worker`; the legacy `packages/judgment_graph/workers/scoring/worker.py` now re-exports the packaged entrypoint.
- CI workflow: added `.github/workflows/l2.yml` with quality gates plus live PostgreSQL/pgvector and Redis service-container integration checks.
- Integration configurability: `integration_check` now reads `L2_TEST_POSTGRES_HOST/PORT` and `L2_TEST_REDIS_HOST/PORT`, so it works with local Docker Compose and CI service containers.
- Schema consistency: removed the extra SQLAlchemy-only `content_translations.terms` column and now stores translation terms inside `fields._terms`, keeping metadata aligned with the frozen Alembic/DDL contract.
- Lens seed: added `judgment_graph.scripts.seed_verticals` to import the `ai-coding` lens JSON into the `verticals` table; SQLAlchemy tests verify seed write/read.
- Arq enqueue: added `judgment_graph.workers.scoring.enqueue.ScoreEnqueuer` to map `content.analyzed` events into one idempotent `score(content_id)` Arq job; tests cover duplicate suppression without Redis.
- Integration strict mode: `integration_check` now honors `L2_INTEGRATION_STRICT=1`, so CI fails if PostgreSQL/Redis checks are skipped while local runs can still report PARTIAL.
- L1 provider switch: added `judgment_graph.input.create_analysis_provider` plus a read-only SQLAlchemy adapter for `content_items` and `content_base_analysis`, selected by `L2_ANALYSIS_PROVIDER=stub|sqlalchemy` and `L2_L1_DATABASE_URL`.

Verification completed:

- `python -m pytest packages/judgment_graph/tests --cov=judgment_graph --cov-report=term-missing`: 23 passed, coverage 85%.
- `python -m ruff check packages/judgment_graph`: passed.
- `python -m mypy packages/judgment_graph/judgment_graph`: passed.
- `python -m judgment_graph.scripts.smoke`: `L2 PIPELINE: PASS`.
- `python -m judgment_graph.scripts.verify_contracts`: `L2 CONTRACTS: PASS`.
- `python -m judgment_graph.scripts.audit_dod`: PASS for implemented/offline-verifiable items including LangGraph execution; PENDING_ENV for external PostgreSQL and Redis Arq runtime.
- `python -m judgment_graph.scripts.integration_check`: PARTIAL on this host; PostgreSQL/Redis ports are not reachable, packaged worker check PASS.
- Docker/Compose check: Docker CLI and Compose are installed, but Docker Desktop Linux engine is not running. Attempting to start `com.docker.service` failed due current session/service permissions, so live PostgreSQL/Redis integration could not be executed here.
- `.github/workflows/l2.yml`: added quality job and integration job with pgvector PostgreSQL and Redis service containers; not executed locally.
- `python -m alembic -c alembic.ini upgrade head --sql`: passed, generated PostgreSQL DDL for the four L2-owned tables.
- `make l2-smoke`: not runnable on this Windows host because `make` is not installed; Makefile target is present and maps to the passing smoke command.

Remaining before final DoD:

- Run `.github/workflows/l2.yml` in CI or start Docker Desktop locally, then execute `docker compose -f docker-compose.integration.yml up -d` and `make l2-integration` to collect live PostgreSQL/Redis evidence.

## 2026-06-01

Continued toward final acceptance with integration-readiness work.

- Added `judgment_graph.input.create_analysis_provider` as the runtime switch for `stub` and `sqlalchemy` providers.
- Added read-only `SqlAlchemyAnalysisProvider` for L1 `content_items` + `content_base_analysis`; it reflects existing tables and maps common L1 fields into `BaseAnalysis`.
- Updated service and Arq worker defaults to use `create_analysis_provider()`, so `L2_ANALYSIS_PROVIDER=sqlalchemy` can switch runtime L1 access without graph changes.
- Hardened SQLAlchemy provider semantics: missing `content_base_analysis` now raises `KeyError` instead of producing an empty analysis.
- Added tests for provider factory selection, SQLAlchemy row mapping, missing content/base-analysis cases, and SELECT-only execution.
- Updated README, contract markers, and DoD audit evidence for the L1 provider switch.
- Added `judgment_graph.scripts.feature_matrix` and `make l2-feature-matrix` so E0-E9 feature implementation/self-test evidence is executable and wired into contract/audit verification.
- Added contract verification for "no real model calls in CI/dev": the verifier rejects real LLM SDK dependencies/imports and non-fake vertical `model_profile` entries.
- Tightened `.github/workflows/l2.yml`: README/docs changes now trigger L2 CI, and `make l2-feature-matrix` is an explicit quality-gate step before contract/audit.
- Added `judgment_graph.scripts.release_check` and `make l2-release-check` to aggregate DoD audit plus live integration results; it reports `PENDING_ENV` until PostgreSQL and Redis checks pass.
- Added `docs/issues.md` to track live integration and local git-workspace blockers separately from progress notes.
- Enforced the coverage DoD in the test gate with `--cov-fail-under=80` for `make test` and the documented Windows pytest command.
- Started Docker Desktop via `docker desktop start` and ran live PostgreSQL/pgvector plus Redis integration locally.
- Fixed `integration_check` to use absolute Alembic config/script paths, so migration checks no longer depend on the current working directory.
- Fixed package discovery in `pyproject.toml` so `python -m pip install -e D:\vscodefile\agentic[dev]` works in CI/local environments.
- Made `audit_dod` reflect live PostgreSQL/Redis status dynamically and fixed `release_check` to reuse integration results without nested event-loop failures.

Verification completed:

- `python -m pytest packages/judgment_graph/tests/test_analysis_provider_factory.py packages/judgment_graph/tests/test_worker_contract.py packages/judgment_graph/tests/test_service_contract.py -q`: 9 passed.
- `python -m pytest packages/judgment_graph/tests --cov=judgment_graph --cov-report=term-missing --cov-fail-under=80`: 32 passed, coverage 87.39%.
- `python -m ruff check packages/judgment_graph`: passed.
- `python -m mypy packages/judgment_graph/judgment_graph`: passed.
- `python -m judgment_graph.scripts.feature_matrix`: `L2 FEATURE MATRIX: PASS`.
- `python -m judgment_graph.scripts.smoke`: `L2 PIPELINE: PASS`.
- `python -m judgment_graph.scripts.verify_contracts`: `L2 CONTRACTS: PASS`.
- `python -m judgment_graph.scripts.audit_dod`: all listed DoD items PASS or PASS_OFFLINE; live PostgreSQL/Redis items PASS.
- `python -m judgment_graph.scripts.integration_check`: `L2 INTEGRATION: PASS`.
- `python -m judgment_graph.scripts.release_check`: `L2 RELEASE CHECK: PASS`.
- `.github/workflows/l2.yml`: quality job now runs test/lint/typecheck/smoke/feature-matrix/contracts/audit/offline Alembic; integration job runs strict PostgreSQL/pgvector + Redis checks with service containers. Not executed in this local workspace.

Current issues:

- Local git repository initialized in `D:\vscodefile\agentic`; initial L2 delivery commit prepared after final verification.
