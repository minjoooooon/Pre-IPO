import os
import secrets
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    # `HOST` / `PORT` env vars are honored at import so the VPS deployment can
    # listen on 0.0.0.0:<port> without editing source. Default stays loopback —
    # exposing publicly is an explicit operator decision. (Full BaseSettings
    # migration is deferred — only the network-binding knobs are wired here.)
    host: str = Field(default_factory=lambda: os.environ.get("HOST") or "127.0.0.1")
    port: int = Field(default_factory=lambda: int(os.environ.get("PORT") or 8000))

    # /api/admin/* is gated behind an X-Admin-Token header. The default is
    # taken from the ADMIN_TOKEN env var; if unset, a random token is
    # generated at import time and logged at startup so the operator can
    # paste it into the FE. Without this, any browser tab in the user's
    # session can POST and corrupt share_ratio (CORS allow_origins=*).
    admin_token: str = Field(
        default_factory=lambda: os.environ.get("ADMIN_TOKEN") or secrets.token_urlsafe(24)
    )

    # CORS allow-list. Default covers the prototype served by FastAPI on :8000,
    # the legacy static :8080 dev server, and a file:// origin (which sends the
    # literal string "null"). Anything else gets refused at the preflight stage
    # so a malicious browser tab can't reach /api/admin/* even before the token
    # check runs.
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: (
            [o.strip() for o in os.environ["CORS_ALLOW_ORIGINS"].split(",") if o.strip()]
            if os.environ.get("CORS_ALLOW_ORIGINS")
            else [
                "http://localhost:8000",
                "http://127.0.0.1:8000",
                "http://localhost:8080",
                "http://127.0.0.1:8080",
                "null",
            ]
        )
    )

    # paths
    base_dir: Path = Path(__file__).resolve().parent.parent
    data_dir: Path = Path(__file__).resolve().parent.parent / "data"
    db_path: Path = Path(__file__).resolve().parent.parent / "ticks.db"
    log_dir: Path = Path(__file__).resolve().parent.parent / "logs"

    # adapter behavior
    stale_threshold_seconds: int = 60
    reconnect_initial_seconds: float = 1.0
    reconnect_max_seconds: float = 60.0
    heartbeat_interval_seconds: int = 30
    # OKX server-side WS timeout is 25s; ping every 20s.
    okx_heartbeat_interval_seconds: int = 20
    # Ticks with exchange-reported timestamps further in the future than this
    # are treated as stale (likely clock skew) rather than fresh.
    max_clock_skew_seconds: int = 30

    # persistence
    tick_batch_flush_seconds: int = 60
    # First flush after boot happens sooner so warm-up data survives a crash.
    tick_batch_first_flush_seconds: int = 5
    # Hard cap on the in-memory pending-flush queue. If exceeded (e.g.
    # persistent SQLite errors) the oldest entries are dropped with a warn log.
    pending_flush_max: int = 10_000
    history_retention_days: int = 7
    history_replay_seconds: int = 86_400  # 24h
    # Hard ceiling on rows returned by /api/history/{asset_id}. Without this a
    # caller passing an unbounded `seconds=` could serialize hundreds of
    # thousands of rows into one JSON response.
    history_max_rows: int = 100_000
    # Period between TickStore retention sweeps (6h).
    retention_cleanup_interval_seconds: int = 21_600
    # Period between adapter-status broadcasts to WS subscribers. Slice 5
    # F68: previously statuses only flowed on snapshot/config-update, so
    # the FeedsCell badge could sit stale for hours after a disconnect.
    status_broadcast_interval_seconds: int = 30

    # ring buffer
    ring_buffer_points: int = 96

    # uvicorn WS frame cap. Browser pages should never need to send big frames
    # (the FE is read-mostly); cap inbound frames at 1 MiB so a malicious local
    # page can't pin memory by streaming huge text frames.
    ws_max_size_bytes: int = 1 << 20


settings = Settings()
