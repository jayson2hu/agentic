from __future__ import annotations

import asyncio
from dataclasses import dataclass

from judgment_graph.scripts.audit_dod import audit_items
from judgment_graph.scripts.integration_check import IntegrationResult, run_checks


@dataclass(frozen=True)
class ReleaseStatus:
    status: str
    detail: str


def evaluate_release(integration_results: list[IntegrationResult]) -> ReleaseStatus:
    by_name = {result.name: result for result in integration_results}
    audit = audit_items(
        postgres_result=by_name.get("postgres"),
        redis_result=by_name.get("redis"),
    )
    failures = [item for item in audit if item.status not in {"PASS", "PASS_OFFLINE", "PENDING_ENV"}]
    if failures:
        return ReleaseStatus(
            "FAIL",
            "audit failures: " + ", ".join(f"{item.requirement}={item.status}" for item in failures),
        )

    skipped = [result for result in integration_results if result.status == "SKIP"]
    failed = [result for result in integration_results if result.status not in {"PASS", "SKIP"}]
    if failed:
        return ReleaseStatus(
            "FAIL",
            "integration failures: " + ", ".join(f"{item.name}={item.status}" for item in failed),
        )
    if skipped:
        return ReleaseStatus(
            "PENDING_ENV",
            "waiting for live services: " + ", ".join(f"{item.name}: {item.detail}" for item in skipped),
        )
    return ReleaseStatus("PASS", "all DoD and live integration checks passed")


async def run_release_check() -> ReleaseStatus:
    return evaluate_release(await run_checks())


def main() -> None:
    status = asyncio.run(run_release_check())
    print(f"L2 RELEASE CHECK: {status.status} - {status.detail}")
    if status.status == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
