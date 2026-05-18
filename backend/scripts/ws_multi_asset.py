"""Sample first tick per asset+exchange, print impl_fdv summary."""
import asyncio, io, json, sys, websockets

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")


async def main():
    seen = {}
    async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
        snap = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
        print(f"snapshot: assets={len(snap['assets'])} markets={len(snap['markets'])}")
        for h_asset, h_ex_map in snap.get("history", {}).items():
            for h_ex, pts in h_ex_map.items():
                print(f"  history {h_asset:<10} {h_ex:<10} = {len(pts)} pts")

        deadline = asyncio.get_event_loop().time() + 60
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
            except asyncio.TimeoutError:
                continue
            if msg.get("type") != "tick":
                continue
            q = msg["quote"]
            key = f"{q['asset_id']}:{q['exchange']}"
            if key in seen:
                continue
            seen[key] = q
            print(
                f"  TICK {key:<22} px={q['token_price']:>10.2f} "
                f"impl_share=${q['implied_share_price']:>10.2f} "
                f"impl_fdv=${q['implied_fdv']/1e12:>6.2f}T  conf={q['confidence']}"
            )
            if len(seen) >= 5:
                break

asyncio.run(main())
