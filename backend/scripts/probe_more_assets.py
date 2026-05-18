"""Probe OKX SWAP + Bitget SPOT for additional pre-IPO assets.

Reports which target tickers actually exist on each venue (probe-first, no
assumptions). Output drives manual additions to assets.json + markets.json."""

from __future__ import annotations

import io
import sys

import httpx

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")

# Candidate ticker fragments to search for on each venue.
TARGETS = [
    "OPENAI",
    "AI",  # too broad - filter by context
    "ANTHROPIC",
    "STRIPE",
    "NEURALINK",
    "FIGURE",
    "DATABRICKS",
    "XAI",
    "PERPLEXITY",
    "MISTRAL",
    "SHEIN",
    "DISCORD",
    "REDDIT",  # already public but may still trade
    "EPIC",
    "BYTEDANCE",
    "TIKTOK",
]

# Filter out obvious false positives (existing crypto names containing target substrings)
EXCLUDE_PREFIXES = {"ADA", "AIDOGE", "ARB", "ARC", "BTT", "DOGE", "ETH", "GRAIL", "INJ"}


def has_target(symbol: str) -> str | None:
    s = symbol.upper()
    for t in TARGETS:
        if t == "AI" and len(s) > 5:
            # avoid matching e.g. AIDOGE; require AI to be a standalone token
            if s.startswith("AI-") or s.startswith("AI_") or s.startswith("AIUSDT"):
                return t
            continue
        if t in s:
            return t
    return None


def probe_okx() -> None:
    print("=" * 60)
    print("OKX SWAP /public/instruments — pre-IPO style listings")
    print("=" * 60)
    r = httpx.get(
        "https://www.okx.com/api/v5/public/instruments",
        params={"instType": "SWAP"},
        timeout=15.0,
    )
    r.raise_for_status()
    instruments = r.json().get("data", []) or []
    hits = []
    for inst in instruments:
        inst_id = inst.get("instId", "")
        target = has_target(inst_id)
        if target is None:
            continue
        hits.append((target, inst))
    print(f"total SWAP instruments: {len(instruments)}, pre-IPO matches: {len(hits)}")
    for target, inst in hits:
        # fetch current ticker
        try:
            tr = httpx.get(
                "https://www.okx.com/api/v5/market/ticker",
                params={"instId": inst["instId"]},
                timeout=10.0,
            )
            tdata = tr.json().get("data", [])
            last = tdata[0].get("last") if tdata else None
            vol = tdata[0].get("vol24h") if tdata else None
        except Exception as e:  # noqa: BLE001
            last = vol = f"err:{e!r}"
        print(
            f"  [{target}] instId={inst['instId']:<25} "
            f"ctVal={inst.get('ctVal')} ctValCcy={inst.get('ctValCcy')} "
            f"last={last} vol24h={vol} state={inst.get('state')}"
        )


def probe_bitget() -> None:
    print("=" * 60)
    print("BITGET SPOT /api/v2/spot/public/symbols — pre-IPO style listings")
    print("=" * 60)
    r = httpx.get(
        "https://api.bitget.com/api/v2/spot/public/symbols",
        timeout=15.0,
    )
    r.raise_for_status()
    body = r.json()
    items = body.get("data", []) or []
    hits = []
    for it in items:
        sym = it.get("symbol", "")
        target = has_target(sym)
        if target is None:
            continue
        # Also catch "PRE<X>USDT" pattern explicitly
        hits.append((target, it))
    # Extra targeted check for "PRE..." symbols
    for it in items:
        sym = it.get("symbol", "")
        if sym.startswith("PRE") and sym not in [h[1].get("symbol") for h in hits]:
            hits.append(("PRE_*", it))
    print(f"total spot symbols: {len(items)}, pre-IPO style matches: {len(hits)}")
    for target, it in hits:
        sym = it.get("symbol")
        try:
            tr = httpx.get(
                "https://api.bitget.com/api/v2/spot/market/tickers",
                params={"symbol": sym},
                timeout=10.0,
            )
            tdata = tr.json().get("data", [])
            last = tdata[0].get("lastPr") if tdata else None
            qvol = tdata[0].get("quoteVolume") if tdata else None
        except Exception as e:  # noqa: BLE001
            last = qvol = f"err:{e!r}"
        print(
            f"  [{target}] symbol={sym:<22} "
            f"baseCoin={it.get('baseCoin')} quoteCoin={it.get('quoteCoin')} "
            f"status={it.get('status')} last={last} quoteVol24={qvol}"
        )


if __name__ == "__main__":
    probe_okx()
    print()
    probe_bitget()
