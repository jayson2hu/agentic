from judgment_graph.scripts.integration_check import IntegrationResult
from judgment_graph.scripts.release_check import evaluate_release


def test_release_check_is_pending_when_live_services_are_skipped() -> None:
    status = evaluate_release(
        [
            IntegrationResult("postgres", "SKIP", "not reachable"),
            IntegrationResult("redis", "SKIP", "not reachable"),
            IntegrationResult("arq-worker", "PASS", "worker ok"),
        ]
    )

    assert status.status == "PENDING_ENV"
    assert "postgres" in status.detail
    assert "redis" in status.detail


def test_release_check_passes_when_live_integration_passes() -> None:
    status = evaluate_release(
        [
            IntegrationResult("postgres", "PASS", "migration ok"),
            IntegrationResult("redis", "PASS", "ping ok"),
            IntegrationResult("arq-worker", "PASS", "worker ok"),
        ]
    )

    assert status.status == "PASS"
