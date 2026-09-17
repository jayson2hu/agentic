# Real-content preview refactor

Status: implementation and backend acceptance complete, 2026-09-17. The previous 114-test result is a historical baseline.

The preview must serve real L1 snapshots without presenting FakeLLM summaries, translations or scores as real model output. Add an explicit offline heuristic provider, source/processing provenance and reading metadata. Preserve L2 ownership of judgment and L3 HTTP-only access. No paid model calls, production database changes or invented translations.

## Implementation and result

- Explicit `HeuristicProvider` / `L2_PROCESSING_MODE=heuristic`: ranking based on length, topic terms and practical signals; not validated expert judgment. The novelty value 50 is a compatibility placeholder, not an assessed quality dimension.
- No translations are generated. Companion retrieves saved matching passages; no match produces an explicit unavailable-answer message. Persisted heuristic scores select extractive companion behavior even if the HTTP mode is accidentally left at its default.
- Language, reading time, tags and provenance survive HTTP serialization. Unknown dates remain null; missing quotes remain empty rather than invented.
- Accepted version/history reads and transactional stale-product invalidation prevent new text from being paired with old judgment. HTTP checks version/status again before publishing a response. This does not make the entire worker graph one atomic transaction or remove the same-content serialization requirement.
- API root redirects to docs; unknown IDs return 404, invalid persisted snapshots 502, configuration faults non-retryable 503 and transient storage/version races retryable 503.

## Executed checks

```bash
.venv/bin/python -m pytest --cov=judgment_graph --cov-report=term-missing --cov-fail-under=80
.venv/bin/python -m ruff check packages/judgment_graph
.venv/bin/python -m mypy packages/judgment_graph/judgment_graph
.venv/bin/python -m judgment_graph.scripts.smoke
.venv/bin/python -m judgment_graph.scripts.verify_contracts
```

Result: **201 passed, coverage 85.38%**; Ruff, mypy (48 files), smoke and contracts PASS. The last 30 cases cover read-only L2 schema validation: missing HTTP tables/columns are non-retryable configuration errors, connection failures remain retryable, and no migrations or compatibility fallback are attempted. Independent Compose project `codepick-product-20260917` ran PostgreSQL 16 and Redis 7 on loopback 54329/6389: strict migrations, Redis, Arq contract and PostgreSQL completion outbox → Redis → ACK persistence PASS. M1 seven-stage and the full version/replay loop also passed on the new code. Their models remain FakeLLM; the public-source preview below does not use FakeLLM.

## Real public-source run

L1 input: `/tmp/codepick-real-preview-20260917/l1/l1.db`; L2: the adjacent `l2.db`. Ten public GitHub articles were processed through the ordinary graph; all accepted L1 revision 3 (`extractive-v3`). Five became COMPLETED, three WAIT_REVIEW and two CANCELLED. Zero translations. Replay preserved all state, scores, translations, review rows and outbox rows exactly. The ten completion events include five historical revision-1 events and five current revision-3 events; they are not ten current published articles.

The local four-service preview and read-only cross-layer checker verified exact source URLs/run identities, five completed-only L3 articles, pagination, empty search, roots and 404. Runtime reports remain outside Git. Full product/browser evidence and remaining real-model/production limitations live in [the dated platform record](../../codepick-docs/PRODUCT_ACCEPTANCE_2026-09-17.md).
