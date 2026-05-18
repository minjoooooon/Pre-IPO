"""Smoke test: connect to /ws, print first snapshot summary + first N ticks."""

from __future__ import annotations

import asyncio
import io
import json
import sys

import websockets

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")


async def main() -> None:
    n_ticks = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
        # 1) snapshot
        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        snap = json.loads(raw)
        print(f"snapshot.type={snap.get('type')}")
        print(f"  assets={len(snap.get('assets', []))}")
        print(f"  markets={len(snap.get('markets', []))}")
        print(f"  statuses={[(s['exchange'], s['state']) for s in snap.get('statuses', [])]}")
        for asset_id, ex_map in snap.get("history", {}).items():
            for ex, pts in ex_map.items():
                print(f"  history {asset_id}:{ex} = {len(pts)} points")

        # 2) wait for live ticks
        seen = 0
        while seen < n_ticks:
            raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
            msg = json.loads(raw)
            t = msg.get("type")
            if t == "tick":
                seen += 1
                q = msg["quote"]
                print(
                    f"  tick[{seen:02d}] {q['exchange']:<12} px={q['token_price']:>10.2f} "
                    f"impl_share={q['implied_share_price']}  impl_fdv={q['implied_fdv']}  "
                    f"conf={q['confidence']}  status={q['calibration_status']}"
                )
            else:
                print(f"  msg type={t}")


if __name__ == "__main__":
    asyncio.run(main())
