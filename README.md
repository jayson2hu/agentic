# CodePick L2 Judgment & Agents

[异地开发指南](DEVELOPMENT.md) · [平台总文档与关联仓库](https://github.com/jayson2hu/codepick-docs)

This repository implements the L2 service against the frozen contract:

- input event: `content.analyzed {content_id}`
- L1 access: `AnalysisProvider.get(content_id) -> BaseAnalysis`
- products: `content_vertical_scores`, `content_translations`, `content.completed`
- request-time APIs: `recommend(user_id, vertical, limit)`, `companion(content_id, question)`

Development is standalone. The default path uses `StubAnalysisProvider` fixtures and `FakeLLM`; it does not call real L1 or a real model.

## L1 Provider Switch

The L1 boundary is selected by `judgment_graph.input.create_analysis_provider`.
By default it returns `StubAnalysisProvider`. To read a real L1 database at runtime,
set:

```bash
L2_ANALYSIS_PROVIDER=sqlalchemy
L2_L1_DATABASE_URL=postgresql+psycopg://...
```

The SQLAlchemy adapter is read-only and reflects the existing L1 `content_items`
and `content_base_analysis` tables into the frozen `BaseAnalysis` contract.

## Commands

Windows PowerShell:

```bash
$env:PYTHONPATH='D:\vscodefile\agentic\packages\judgment_graph'; python -m pytest D:\vscodefile\agentic\packages\judgment_graph\tests --cov=judgment_graph --cov-report=term-missing --cov-fail-under=80
$env:PYTHONPATH='D:\vscodefile\agentic\packages\judgment_graph'; python -m ruff check D:\vscodefile\agentic\packages\judgment_graph
$env:PYTHONPATH='D:\vscodefile\agentic\packages\judgment_graph'; python -m mypy D:\vscodefile\agentic\packages\judgment_graph\judgment_graph
$env:PYTHONPATH='D:\vscodefile\agentic\packages\judgment_graph'; python -m judgment_graph.scripts.smoke
$env:PYTHONPATH='D:\vscodefile\agentic\packages\judgment_graph'; python -m judgment_graph.scripts.feature_matrix
$env:PYTHONPATH='D:\vscodefile\agentic\packages\judgment_graph'; python -m judgment_graph.scripts.verify_contracts
$env:PYTHONPATH='D:\vscodefile\agentic\packages\judgment_graph'; python -m judgment_graph.scripts.audit_dod
$env:PYTHONPATH='D:\vscodefile\agentic\packages\judgment_graph'; python -m alembic -c D:\vscodefile\agentic\alembic.ini upgrade head --sql
```

POSIX/CI with `make`:

```bash
make test
make lint
make typecheck
make l2-smoke
make l2-feature-matrix
make l2-contracts
make l2-audit
make l2-integration
make l2-release-check
```

`make l2-smoke` prints `L2 PIPELINE: PASS` when the full offline pipeline passes.

## Persistence

The SQLAlchemy metadata and Alembic migration declare only L2-owned tables:

- `verticals`
- `content_vertical_scores`
- `content_translations`
- `review_queue`

The default smoke path uses `InMemoryJudgmentRepository`. Integration can switch to
`SqlAlchemyJudgmentRepository` without changing graph or L1 provider code.

`verify_contracts` checks that only the allowed L2 table names are declared, no L3 imports
exist in `judgment_graph`, and every E0-E9 feature has implementation and self-test
artifacts listed in `judgment_graph.scripts.feature_matrix`.

`audit_dod` prints PASS/PENDING status per DoD item. Current pending items require external
PostgreSQL/pgvector and Redis/Arq runtime services.

`release_check` aggregates DoD audit and integration results. It prints
`L2 RELEASE CHECK: PASS` only when live PostgreSQL and Redis checks also pass;
otherwise it reports the remaining `PENDING_ENV` services.

## Integration Environment

Use Docker Compose to run the optional integration services:

```bash
docker compose -f docker-compose.integration.yml up -d
make l2-integration
docker compose -f docker-compose.integration.yml down -v
```

The integration script checks:

- live Alembic upgrade/downgrade against PostgreSQL with pgvector
- Redis connectivity for Arq runtime readiness
- packaged Arq worker contract for `score(ctx, content_id)`

In CI, `.github/workflows/l2.yml` runs the same check with service containers. Override
service endpoints with `L2_TEST_POSTGRES_HOST`, `L2_TEST_POSTGRES_PORT`,
`L2_TEST_REDIS_HOST`, and `L2_TEST_REDIS_PORT` when needed. Set
`L2_INTEGRATION_STRICT=1` to fail when PostgreSQL or Redis checks are skipped.

## Request-Time Contract

The frozen request APIs are exposed from `judgment_graph.service`:

```python
recommend(user_id: int, vertical: str, limit: int)
companion(content_id: int, question: str)
```

Use `configure_service(...)` to inject the real provider, LLM/router, and repository at runtime.
