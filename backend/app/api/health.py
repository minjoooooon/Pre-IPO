"""Health probes split into liveness and readiness.

`/livez` is a constant 200 — the process is up.
`/readyz` checks that aggregator + supervisor exist AND at least one adapter
ticked within `stale_threshold_seconds`. Returns 503 otherwise so monitoring
can detect "process running but feed dead" without paging on transient gaps.

`/health` is kept as an alias of `/livez` for back-compat with anything that
was already scraping it."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request, Response, status

from ..config import settings

router = APIRouter()


def _livez_payload() -> dict:
    return {"status": "ok"}


@router.get("/livez")
def livez() -> dict:
    return _livez_payload()


@router.get("/health")
def health() -> dict:
    # Back-compat alias for any operator/monitoring already wired to /health.
    return _livez_payload()


@router.get("/readyz")
def readyz(request: Request, response: Response) -> dict:
    """Return 200 only if the data pipeline is actually serving fresh ticks.

    Failure modes this catches:
      - lifespan startup raised between aggregator.start() and supervisor.start()
        → one of them is None
      - all adapters in stale/reconnecting/disabled → feed is dead but the
        process is up
    """
    aggregator = getattr(request.app.state, "aggregator", None)
    supervisor = getattr(request.app.state, "supervisor", None)
    if aggregator is None or supervisor is None:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "not_ready",
            "reason": "components_not_started",
            "aggregator": aggregator is not None,
            "supervisor": supervisor is not None,
        }
    statuses = supervisor.statuses()
    now = time.time()
    fresh_cutoff = now - settings.stale_threshold_seconds
    fresh_count = sum(
        1
        for s in statuses
        if s.last_tick_at is not None and s.last_tick_at >= fresh_cutoff
    )
    if fresh_count == 0:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "not_ready",
            "reason": "no_fresh_adapter",
            "adapters": len(statuses),
            "fresh_within_seconds": settings.stale_threshold_seconds,
        }
    return {
        "status": "ready",
        "adapters": len(statuses),
        "fresh": fresh_count,
    }
