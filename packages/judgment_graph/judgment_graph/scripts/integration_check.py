from __future__ import annotations

import asyncio
import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from judgment_graph.input.stub import StubAnalysisProvider
from judgment_graph.persist.sqlalchemy_repository import SqlAlchemyJudgmentRepository
from judgment_graph.scripts.seed_verticals import seed_ai_coding_lens
from judgment_graph.workers.scoring.worker import score

EXPECTED_TABLES = {
    "verticals",
    "content_vertical_scores",
    "content_translations",
    "review_queue",
    "content_judgment_state",
    "judgment_outbox",
}
ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class IntegrationResult:
    name: str
    status: str
    detail: str


def tcp_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def docker_engine_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    output = f"{result.stdout}\n{result.stderr}".lower()
    return (
        result.returncode == 0
        and bool(result.stdout.strip())
        and "error during connect" not in output
    )


def postgres_url() -> str:
    host = os.getenv("L2_TEST_POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("L2_TEST_POSTGRES_PORT", "54329")
    return os.getenv(
        "L2_TEST_DATABASE_URL",
        f"postgresql+psycopg://codepick:codepick@{host}:{port}/codepick_l2",
    )


def redis_url() -> str:
    host = os.getenv("L2_TEST_REDIS_HOST", "127.0.0.1")
    port = os.getenv("L2_TEST_REDIS_PORT", "6389")
    return os.getenv("L2_TEST_REDIS_URL", f"redis://{host}:{port}/0")


def postgres_host_port() -> tuple[str, int]:
    return os.getenv("L2_TEST_POSTGRES_HOST", "127.0.0.1"), int(
        os.getenv("L2_TEST_POSTGRES_PORT", "54329")
    )


def redis_host_port() -> tuple[str, int]:
    return os.getenv("L2_TEST_REDIS_HOST", "127.0.0.1"), int(
        os.getenv("L2_TEST_REDIS_PORT", "6389")
    )


def check_postgres() -> IntegrationResult:
    host, port = postgres_host_port()
    endpoint = f"{host}:{port}"
    if not tcp_open(host, port):
        if not docker_engine_available():
            return IntegrationResult(
                "postgres",
                "SKIP",
                f"{endpoint} is not reachable and Docker engine is not running",
            )
        return IntegrationResult("postgres", "SKIP", f"{endpoint} is not reachable")
    url = postgres_url()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "db" / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(url, future=True)
    repository = SqlAlchemyJudgmentRepository(engine)
    seed_ai_coding_lens(repository)
    lens = repository.load_lens("ai-coding")
    if lens.rubric_version != "aic-v1":
        raise AssertionError(f"unexpected ai-coding seed version: {lens.rubric_version}")
    with engine.begin() as conn:
        conn.execute(text("SELECT 1"))
    tables = set(inspect(engine).get_table_names())
    missing = EXPECTED_TABLES - tables
    if missing:
        raise AssertionError(f"missing L2 tables after migration: {sorted(missing)}")
    command.downgrade(config, "base")
    return IntegrationResult("postgres", "PASS", "alembic upgrade/downgrade succeeded")


async def check_redis() -> IntegrationResult:
    host, port = redis_host_port()
    endpoint = f"{host}:{port}"
    if not tcp_open(host, port):
        if not docker_engine_available():
            return IntegrationResult(
                "redis",
                "SKIP",
                f"{endpoint} is not reachable and Docker engine is not running",
            )
        return IntegrationResult("redis", "SKIP", f"{endpoint} is not reachable")
    from arq.connections import RedisSettings, create_pool

    settings = RedisSettings(host=host, port=port, database=0)
    redis = await create_pool(settings)
    try:
        pong = await redis.ping()
        if pong is not True:
            raise AssertionError(f"unexpected redis ping: {pong!r}")
    finally:
        await redis.aclose()
    return IntegrationResult("redis", "PASS", "redis ping succeeded")


async def check_worker_contract() -> IntegrationResult:
    await score({}, 1001)
    StubAnalysisProvider().get(1001)
    return IntegrationResult(
        "arq-worker", "PASS", "score(ctx, content_id) consumed fixture content"
    )


async def run_checks() -> list[IntegrationResult]:
    results = [check_postgres(), await check_redis(), await check_worker_contract()]
    return results


def main() -> None:
    results = asyncio.run(run_checks())
    for result in results:
        print(f"{result.status}: {result.name} - {result.detail}")
    has_skip = any(result.status == "SKIP" for result in results)
    if has_skip:
        print("L2 INTEGRATION: PARTIAL")
        if os.getenv("L2_INTEGRATION_STRICT") == "1":
            raise SystemExit(1)
    else:
        print("L2 INTEGRATION: PASS")


if __name__ == "__main__":
    main()
