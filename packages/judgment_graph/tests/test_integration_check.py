import json
from types import SimpleNamespace

import pytest
from judgment_graph.scripts.integration_check import (
    check_completion_relay,
    check_postgres,
    check_worker_contract,
    main,
)


def test_postgres_integration_check_skips_or_passes_cleanly() -> None:
    result = check_postgres()
    assert result.status in {"PASS", "SKIP"}


def test_completion_relay_integration_check_skips_or_passes_cleanly() -> None:
    result = check_completion_relay()
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


def test_completion_relay_integration_orchestrates_publish_ack_and_cleanup(
    monkeypatch,
) -> None:
    from judgment_graph.scripts import integration_check

    calls: list[object] = []

    class FakeClient:
        def delete(self, *keys: str) -> None:
            calls.append(("delete", keys))

        def lpop(self, queue: str) -> str:
            calls.append(("lpop", queue))
            return json.dumps(
                {"idempotency_key": "content.completed:900001-r0"}
            )

        def rpush(self, queue: str, event_id: str) -> None:
            calls.append(("rpush", queue, event_id))

        def close(self) -> None:
            calls.append("client-close")

    class FakeEngine:
        def dispose(self) -> None:
            calls.append("engine-dispose")

    class FakeRepository:
        def __init__(self, engine: object) -> None:
            calls.append(("repository", engine))

        def set_status(self, content_id: int, status: str) -> None:
            calls.append(("status", content_id, status))

        def mark_completed(self, content_id: int) -> None:
            calls.append(("completed", content_id))

    class FakeStore:
        def __init__(self, engine: object) -> None:
            calls.append(("store", engine))

        def get(self, event_id: str) -> object:
            calls.append(("get", event_id))
            return SimpleNamespace(acked_at=object(), dead_lettered_at=None)

    class FakeRelay:
        count = 0

        def __init__(self, *args: object) -> None:
            calls.append(("relay", args))

        def relay_once(self) -> object:
            self.__class__.count += 1
            if self.__class__.count == 1:
                return SimpleNamespace(published=1, acknowledged=0)
            return SimpleNamespace(published=0, acknowledged=1)

    monkeypatch.setattr(integration_check, "tcp_open", lambda *_args: True)
    monkeypatch.setattr(
        integration_check, "create_engine", lambda *_args, **_kwargs: FakeEngine()
    )
    monkeypatch.setattr(integration_check, "SqlAlchemyJudgmentRepository", FakeRepository)
    monkeypatch.setattr(integration_check, "CompletionOutboxStore", FakeStore)
    monkeypatch.setattr(integration_check, "CompletionOutboxRelay", FakeRelay)
    monkeypatch.setattr(
        integration_check,
        "RedisCompletionTransport",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        integration_check.Redis,
        "from_url",
        lambda *_args, **_kwargs: FakeClient(),
    )
    monkeypatch.setattr(
        integration_check.command,
        "upgrade",
        lambda *_args, **_kwargs: calls.append("upgrade"),
    )
    monkeypatch.setattr(
        integration_check.command,
        "downgrade",
        lambda *_args, **_kwargs: calls.append("downgrade"),
    )

    result = check_completion_relay()

    assert result.status == "PASS"
    assert ("rpush", "codepick:test:l2:completion:acks", "content.completed:900001-r0") in calls
    assert calls[-3:] == ["client-close", "engine-dispose", "downgrade"]
