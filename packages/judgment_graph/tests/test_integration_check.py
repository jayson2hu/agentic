import pytest
from judgment_graph.scripts.integration_check import (
    check_postgres,
    check_worker_contract,
    main,
)


def test_postgres_integration_check_skips_or_passes_cleanly() -> None:
    result = check_postgres()
    assert result.status in {"PASS", "SKIP"}


@pytest.mark.asyncio
async def test_worker_contract_check_passes_without_redis() -> None:
    result = await check_worker_contract()
    assert result.status == "PASS"


def test_integration_main_strict_fails_when_services_are_missing(monkeypatch) -> None:
    monkeypatch.setenv("L2_INTEGRATION_STRICT", "1")
    monkeypatch.setenv("L2_TEST_POSTGRES_PORT", "1")
    monkeypatch.setenv("L2_TEST_REDIS_PORT", "1")
    with pytest.raises(SystemExit):
        main()
