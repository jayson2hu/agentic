.PHONY: l2-smoke l2-contracts l2-audit l2-feature-matrix l2-integration l2-release-check l2-seed-verticals test lint typecheck

l2-smoke:
	PYTHONPATH=$(CURDIR)/packages/judgment_graph python -m judgment_graph.scripts.smoke

l2-contracts:
	PYTHONPATH=$(CURDIR)/packages/judgment_graph python -m judgment_graph.scripts.verify_contracts

l2-audit:
	PYTHONPATH=$(CURDIR)/packages/judgment_graph python -m judgment_graph.scripts.audit_dod

l2-feature-matrix:
	PYTHONPATH=$(CURDIR)/packages/judgment_graph python -m judgment_graph.scripts.feature_matrix

l2-integration:
	PYTHONPATH=$(CURDIR)/packages/judgment_graph python -m judgment_graph.scripts.integration_check

l2-release-check:
	PYTHONPATH=$(CURDIR)/packages/judgment_graph python -m judgment_graph.scripts.release_check

l2-seed-verticals:
	PYTHONPATH=$(CURDIR)/packages/judgment_graph python -m judgment_graph.scripts.seed_verticals

test:
	PYTHONPATH=$(CURDIR)/packages/judgment_graph python -m pytest packages/judgment_graph/tests --cov=judgment_graph --cov-report=term-missing --cov-fail-under=80

lint:
	PYTHONPATH=$(CURDIR)/packages/judgment_graph python -m ruff check packages/judgment_graph

typecheck:
	PYTHONPATH=$(CURDIR)/packages/judgment_graph python -m mypy packages/judgment_graph/judgment_graph
