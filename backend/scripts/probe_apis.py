"""One-off probe: hit each exchange's public REST endpoint and verify the
SpaceX symbol actually exists. Run before implementing adapters so we know
exactly what the response shape looks like.

CLAUDE.md §2: don't guess API response formats. Probe first."""

from __future__ import annotations

import json
import sys

import httpx


def probe_hyperliquid() -> None:
    print("=" * 60)
    print("HYPERLIQUID  POST https://api.hyperliquid.xyz/info {type:meta}")
    print("=" * 60)
    try:
        r = httpx.post(
            "https://api.hyperliquid.xyz/info",
            json={"type": "meta"},
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"FAILED: {e}")
        return

    universe = data.get("universe", [])
    print(f"universe size: {len(universe)}")
    spcx_matches = [u for u in universe if "SPCX" in u.get("name", "").upper()]
    spx_matches = [u for u in universe if "SP" in u.get("name", "").upper()][:10]
    print(f"SPCX exact matches: {len(spcx_matches)}")
    for m in spcx_matches:
        print(f"  {m}")
    print("first 10 'SP*' candidates:")
    for m in spx_matches:
        print(f"  {m}")

    # Also try metaAndAssetCtxs for current mark price + funding
    print()
    print("--- metaAndAssetCtxs (for mark price) ---")
    try:
        r = httpx.post(
            "https://api.hyperliquid.xyz/info",
            json={"type": "metaAndAssetCtxs"},
            timeout=10.0,
        )
        r.raise_for_status()
        ctx = r.json()
    except Exception as e:
        print(f"FAILED: {e}")
        return

    # Response is [meta, asset_ctxs] — same index mapping
    if isinstance(ctx, list) and len(ctx) >= 2:
        meta_universe = ctx[0].get("universe", [])
        asset_ctxs = ctx[1]
        for i, u in enumerate(meta_universe):
            if "SPCX" in u.get("name", "").upper():
                print(f"SPCX index={i}, meta={u}")
                if i < len(asset_ctxs):
                    print(f"  ctx={json.dumps(asset_ctxs[i], indent=2)[:500]}")


def probe_okx() -> None:
    print("=" * 60)
    print("OKX  GET https://www.okx.com/api/v5/public/instruments?instType=SWAP")
    print("=" * 60)
    try:
        r = httpx.get(
            "https://www.okx.com/api/v5/public/instruments",
            params={"instType": "SWAP"},
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"FAILED: {e}")
        return

    instruments = data.get("data", [])
    print(f"SWAP instruments: {len(instruments)}")
    spacex_matches = [
        i for i in instruments if "SPACEX" in i.get("instId", "").upper()
    ]
    print(f"SPACEX matches: {len(spacex_matches)}")
    for m in spacex_matches:
        print(f"  {json.dumps(m, indent=2)[:600]}")

    if not spacex_matches:
        # show similar
        print("no SPACEX. Showing first 5 instruments:")
        for m in instruments[:5]:
            print(f"  instId={m.get('instId')}, state={m.get('state')}")

    # Try ticker for SPACEX-USDT-SWAP
    print()
    print("--- ticker for SPACEX-USDT-SWAP ---")
    try:
        r = httpx.get(
            "https://www.okx.com/api/v5/market/ticker",
            params={"instId": "SPACEX-USDT-SWAP"},
            timeout=10.0,
        )
        print(f"status={r.status_code}")
        print(json.dumps(r.json(), indent=2)[:800])
    except Exception as e:
        print(f"FAILED: {e}")


def probe_bitget() -> None:
    print("=" * 60)
    print("BITGET  GET https://api.bitget.com/api/v2/mix/market/contracts")
    print("=" * 60)
    # Try a few productTypes
    for product in ("USDT-FUTURES", "USDC-FUTURES", "COIN-FUTURES"):
        try:
            r = httpx.get(
                "https://api.bitget.com/api/v2/mix/market/contracts",
                params={"productType": product},
                timeout=10.0,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"{product} FAILED: {e}")
            continue
        items = data.get("data", []) or []
        matches = [
            it
            for it in items
            if "SPAX" in it.get("symbol", "").upper()
            or "SPACEX" in it.get("symbol", "").upper()
        ]
        print(f"{product}: {len(items)} contracts, SpaceX matches: {len(matches)}")
        for m in matches:
            print(f"  {json.dumps(m, indent=2)[:500]}")

    # IPO Prime — try a different endpoint family
    print()
    print("--- preSPAX standalone probe ---")
    for url in (
        "https://api.bitget.com/api/v2/spot/market/tickers?symbol=preSPAXUSDT",
        "https://www.bitget.com/v1/ipo-prime/preSPAX",
    ):
        try:
            r = httpx.get(url, timeout=10.0)
            print(f"  {url}  status={r.status_code}  body[:200]={r.text[:200]}")
        except Exception as e:
            print(f"  {url}  FAILED: {e}")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "hyperliquid"):
        probe_hyperliquid()
        print()
    if which in ("all", "okx"):
        probe_okx()
        print()
    if which in ("all", "bitget"):
        probe_bitget()
