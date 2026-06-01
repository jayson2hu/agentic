# L2 Issues

## Open

No open L2 acceptance blockers remain after local live integration.

## Resolved

- Live PostgreSQL/pgvector migration verification now passes locally with Docker Desktop running.
- Live Redis/Arq runtime verification now passes locally with Docker Desktop running.
- Local git repository was initialized in `D:\vscodefile\agentic`; Sprint delivery can now be committed locally.

## Resolution Path

- Run `.github/workflows/l2.yml` in CI, where the integration job provisions `pgvector/pgvector:pg16` and `redis:7-alpine` service containers with `L2_INTEGRATION_STRICT=1`.
- Or start Docker Desktop locally, then run:

```bash
docker compose -f docker-compose.integration.yml up -d
make l2-integration
docker compose -f docker-compose.integration.yml down -v
```

- Run `python -m judgment_graph.scripts.release_check` after live services are available. It prints `L2 RELEASE CHECK: PASS` only when audit and live integration checks are complete.

## Latest Local Evidence

- `python -m pytest packages/judgment_graph/tests --cov=judgment_graph --cov-report=term-missing --cov-fail-under=80`: 32 passed, coverage 87.39%; coverage gate enforced.
- `python -m judgment_graph.scripts.integration_check`: `L2 INTEGRATION: PASS`.
- `python -m judgment_graph.scripts.release_check`: `L2 RELEASE CHECK: PASS`.
