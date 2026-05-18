"""Registry: load and write assets.json / markets.json with in-memory cache.

Hot-reload from the admin API uses the same instance — callers should hold a
reference to the singleton and re-query after a write."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from .config import settings
from .models import Asset, Market


class Registry:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._lock = threading.RLock()
        self._assets: dict[str, Asset] = {}
        self._markets: dict[str, Market] = {}  # keyed by market_id
        self.reload()

    # ---- load ----

    def reload(self) -> None:
        with self._lock:
            assets_path = self._data_dir / "assets.json"
            markets_path = self._data_dir / "markets.json"

            with assets_path.open("r", encoding="utf-8") as f:
                raw_assets = json.load(f)
            with markets_path.open("r", encoding="utf-8") as f:
                raw_markets = json.load(f)

            self._assets = {
                a["id"]: Asset.model_validate(a) for a in raw_assets["assets"]
            }
            self._markets = {}
            for m in raw_markets["markets"]:
                market = Market.model_validate(m)
                self._markets[market.market_id] = market

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
    ) -> Asset:
        with self._lock:
            asset = self._assets.get(asset_id)
            if asset is None:
                raise KeyError(f"unknown asset_id={asset_id}")
            updated = asset.model_copy(
                update={
                    "fully_diluted_shares": fully_diluted_shares,
                    "shares_source": source,
                    "shares_confidence": confidence,
                    "shares_last_updated": datetime.now(UTC).replace(tzinfo=None).date(),
                }
            )
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
            updated = market.model_copy(
                update={
                    "share_ratio": share_ratio,
                    "ratio_source": ratio_source,
                    "ratio_reference_fdv": ratio_reference_fdv,
                    "ratio_calibrated_at": datetime.now(UTC).replace(tzinfo=None),
                    "confidence": confidence,
                }
            )
            self._markets[market_id] = updated
            self._write_markets()
            return updated

    # ---- private ----

    def _write_assets(self) -> None:
        payload = {
            "assets": [
                json.loads(a.model_dump_json(exclude_none=False))
                for a in self._assets.values()
            ]
        }
        self._atomic_write(self._data_dir / "assets.json", payload)

    def _write_markets(self) -> None:
        payload = {
            "markets": [
                json.loads(m.model_dump_json(exclude_none=False))
                for m in self._markets.values()
            ]
        }
        self._atomic_write(self._data_dir / "markets.json", payload)

    @staticmethod
    def _atomic_write(path: Path, payload: dict) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
            f.write("\n")
        tmp.replace(path)


_registry: Registry | None = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry(settings.data_dir)
    return _registry
