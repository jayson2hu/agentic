# Versioned reads and product invalidation

Date: 2026-09-17. This note covers a bounded L2 consistency fix; the product preview integration is recorded separately.

## Problem and behavior

HTTP previously read the latest L1 projection even when L2's completed score belonged to an older accepted run. Updating L1 before L2 consumed the event could combine a new title/body with an old judgment. A second problem occurred when a new revision did not generate translations: old language rows and scores for other verticals remained keyed only by content ID.

The repository now exposes `get_source_version(content_id) -> tuple[str, int] | None`. The tuple contains the accepted L1 run ID and revision. Missing records and legacy unversioned records return `None`; incomplete version identities raise an error.

The SQL provider exposes `get_document_for_run(content_id, run_id)`. It builds the document from that exact processing-run snapshot, including title, source URL, quotes and publication time. Missing or wrong-owner history never falls back to the current projection. Existing `get_for_run` uses the same history adapter.

Accepting a strictly newer revision invalidates every previous score and translation for that content in the same SQL transaction as the accepted version/state update. Other content is untouched. Duplicate or stale revisions return before cleanup. Existing revision write guards continue to reject late worker writes. The in-memory repository follows the same observable invalidation rules.

Missing publication time remains `None`; processing time is no longer represented as publication time. Invalid non-null timestamps still fail explicitly.

## HTTP integration

Use the accepted version to choose the document: legacy unversioned rows can use `get_document`; versioned rows must use `get_document_for_run`. Companion context must use the same accepted document, not a separate read of the latest projection.

These repository methods do not by themselves create an atomic HTTP bundle. A concurrent L2 version change between independent reads of score, version and status still requires a consistent repository read or a before/after version and status guard. They also do not make the entire scoring/translation graph a single transaction.

## Checks

- 55 tests passed across `test_versioned_documents.py`, `test_sqlalchemy_repository.py` and `test_snapshot_provider.py`.
- The new file contains 12 parameter-expanded cases covering both repositories, stale/duplicate revisions, preservation of other content, legacy rows, SQL rollback during cleanup, historical document identity and unknown publication time.
- Ruff passed for the five modified implementation modules and the new test file.
- mypy passed for the five modified implementation modules.
- The first run found one incorrect exception expectation in the new test (zero is an invalid ID, not missing content); it was corrected and all 55 checks passed.
- Final integrating-agent gate subsequently passed: 201 full L2 tests, 85.38% coverage, Ruff, mypy (48 files), smoke/contracts, M1 and the Redis/Arq version loop. HTTP integration now captures the accepted identity before reading products/history and rechecks it plus COMPLETED status before returning; concurrent changes yield `503 content_version_changed` with Retry-After. The dedicated HTTP consistency suite adds 37 parameter-expanded cases. Real public-source revision 3 was served through L3 with no mixed historical artifacts.

Files: `input/provider.py`, `input/sqlalchemy_provider.py`, `persist/contracts.py`, `persist/repository.py`, `persist/sqlalchemy_repository.py`, and `tests/test_versioned_documents.py` under `packages/judgment_graph`.
