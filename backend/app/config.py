from pathlib import Path

from pydantic import BaseModel


class Settings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000

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
    # Period between TickStore retention sweeps (6h).
    retention_cleanup_interval_seconds: int = 21_600

    # ring buffer
    ring_buffer_points: int = 96


settings = Settings()
