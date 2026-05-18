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

    # persistence
    tick_batch_flush_seconds: int = 60
    history_retention_days: int = 7
    history_replay_seconds: int = 86_400  # 24h

    # ring buffer
    ring_buffer_points: int = 96


settings = Settings()
