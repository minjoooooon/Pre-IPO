"""Registry: load and write assets.json / markets.json with in-memory cache.

Hot-reload from the admin API uses the same instance — callers should hold a
reference to the singleton and re-query after a write."""

from __future__ import annotations

import json
import os
import threading
import time as time_mod
from datetime import UTC, datetime
from pathlib import Path

import structlog

from .config import settings
from .models import Asset, Market

log = structlog.get_logger("app.registry")

_WRITE_RETRY_DELAYS = (0.01, 0.05, 0.15, 0.5, 1.0)


def safe_write_json(path: Path, payload: dict) -> None:
    """Atomic-ish JSON write hardened against the failure modes that
    `Path.write_text` does not handle:

    - `f.flush()` + `os.fsync(fd)` so power loss between write and replace
      doesn't leave a zero-byte tmp.
    - On Windows, `Path.replace` raises `PermissionError` /
      `ERROR_SHARING_VIOLATION` if a reader (e.g. another process snapshotting
      the file) has the destination open. Retry with backoff before giving up.

    The temp file lives next to the destination so `replace` stays atomic
    (same volume requirement)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
        f.write("\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            # fsync can fail on some filesystems (e.g. WSL2 9p, network shares);
            # the replace below is still safer than a non-atomic write.
            pass
    last_err: Exception | None = None
    for delay in _WRITE_RETRY_DELAYS:
        try:
            tmp.replace(path)
            return
        except PermissionError as e:  # Windows ERROR_SHARING_VIOLATION etc.
            last_err = e
            time_mod.sleep(delay)
    # final attempt without sleep — surface the error if it still fails
    tmp.replace(path)
    if last_err is not None:
        log.warning("registry_write_recovered_after_retry", path=str(path), error=repr(last_err))


class Registry:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._lock = threading.RLock()
        self._assets: dict[str, Asset] = {}
        self._markets: dict[str, Market] = {}  # keyed by market_id
        self.reload()

    # ---- load ----

    def reload(self) -> None:
        """Parse both data files into fresh dicts and only commit on full
        success — a single bad row no longer leaves the registry half-loaded.
        Also enforces the foreign key: every `Market.asset_id` must point at
        a known asset, and no duplicate asset/market ids."""
        with self._lock:
            assets_path = self._data_dir / "assets.json"
            markets_path = self._data_dir / "markets.json"

            with assets_path.open("r", encoding="utf-8") as f:
                raw_assets = json.load(f)
            with markets_path.open("r", encoding="utf-8") as f:
                raw_markets = json.load(f)

            new_assets: dict[str, Asset] = {}
            for a in raw_assets["assets"]:
                asset = Asset.model_validate(a)
                if asset.id in new_assets:
                    raise ValueError(f"duplicate asset.id={asset.id} in assets.json")
                new_assets[asset.id] = asset

            new_markets: dict[str, Market] = {}
            for m in raw_markets["markets"]:
                market = Market.model_validate(m)
                if market.asset_id not in new_assets:
                    raise ValueError(
                        f"market {market.market_id} references unknown asset_id={market.asset_id}"
                    )
                if market.market_id in new_markets:
                    raise ValueError(f"duplicate market_id={market.market_id} in markets.json")
                new_markets[market.market_id] = market

            # Both passes succeeded — commit atomically.
            self._assets = new_assets
            self._markets = new_markets

    # ---- read ----

    def list_assets(self) -> list[Asset]:
        with self._lock:
            return list(self._assets.values())

    def get_asset(self, asset_id: str) -> Asset | None:
        with self._lock:
            return self._assets.get(asset_id)

    def list_markets(self, asset_id: str | None = None) -> list[Market]:
        with self._lock:
            markets = list(self._markets.values())
            if asset_id is not None:
                markets = [m for m in markets if m.asset_id == asset_id]
            return markets

    def get_market(self, market_id: str) -> Market | None:
        with self._lock:
            return self._markets.get(market_id)

    # ---- write (admin) ----

    def update_asset_shares(
        self,
        asset_id: str,
        fully_diluted_shares: float,
        source: str,
        confidence: int,
        source_url: str | None = None,
    ) -> Asset:
        """Update share count with full Pydantic re-validation. Provenance
        rules:

        - `shares_last_updated` only advances when the count actually changed
          (an admin re-saving the same number must not rewrite the date,
          which would destroy "when was this researched?" history).
        - `shares_source_url` only overwritten when the caller passes one
          explicitly. None means "keep existing"."""
        with self._lock:
            asset = self._assets.get(asset_id)
            if asset is None:
                raise KeyError(f"unknown asset_id={asset_id}")

            data = asset.model_dump()
            shares_changed = data.get("fully_diluted_shares") != fully_diluted_shares
            data["fully_diluted_shares"] = fully_diluted_shares
            data["shares_source"] = source
            data["shares_confidence"] = confidence
            if source_url is not None:
                data["shares_source_url"] = source_url
            if shares_changed:
                data["shares_last_updated"] = datetime.now(UTC).date()

            # model_validate (not model_copy) re-runs every validator so a
            # body-level bypass of body validation still fails here.
            updated = Asset.model_validate(data)
            self._assets[asset_id] = updated
            self._write_assets()
            return updated

    def update_market_ratio(
        self,
        asset_id: str,
        exchange: str,
        share_ratio: float,
        ratio_source: str,
        ratio_reference_fdv: float | None,
        confidence: int,
    ) -> Market:
        market_id = f"{asset_id}:{exchange}"
        with self._lock:
            market = self._markets.get(market_id)
            if market is None:
                raise KeyError(f"unknown market={market_id}")
            if asset_id not in self._assets:
                # Defence in depth — the FK is enforced on reload too, but
                # admin paths shouldn't be able to break it from memory either.
                raise KeyError(f"asset_id={asset_id} not in registry")

            data = market.model_dump()
            data["share_ratio"] = share_ratio
            data["ratio_source"] = ratio_source
            data["ratio_reference_fdv"] = ratio_reference_fdv
            # Timezone-aware UTC. Naive datetimes silently look like local time
            # to anyone deserializing on a non-UTC host (this one is KST).
            data["ratio_calibrated_at"] = datetime.now(UTC)
            data["confidence"] = confidence

            # Re-validate so RatioSource literal / numeric finite-ness fail here
            # rather than at the next reload().
            updated = Market.model_validate(data)
            self._markets[market_id] = updated
            self._write_markets()
            return updated

    # ---- private ----

    def _write_assets(self) -> None:
        # `mode="json"` produces JSON-native types directly (no double
        # serialize/parse) and `extra="allow"` carries unknown fields through.
        payload = {
            "assets": [a.model_dump(mode="json", exclude_none=False) for a in self._assets.values()]
        }
        safe_write_json(self._data_dir / "assets.json", payload)

    def _write_markets(self) -> None:
        payload = {
            "markets": [m.model_dump(mode="json", exclude_none=False) for m in self._markets.values()]
        }
        safe_write_json(self._data_dir / "markets.json", payload)


_registry: Registry | None = None
_registry_init_lock = threading.Lock()


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        with _registry_init_lock:
            if _registry is None:
                _registry = Registry(settings.data_dir)
    return _registry
