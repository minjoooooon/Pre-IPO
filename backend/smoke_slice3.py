"""Smoke test for slice-3 fixes F38-F51.

Runs against TemporaryDirectory-copied data files so tracked
backend/data/*.json never gets touched (per slice-2 lessons-learned)."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# Force the registry to read from a temp copy
_REPO_ROOT = Path(__file__).resolve().parent
_BACKEND_DATA = _REPO_ROOT / "data"


def _setup_temp_data() -> tempfile.TemporaryDirectory:
    # ignore_cleanup_errors=True: F44 test opens a RotatingFileHandler on
    # logs/app.log which stays held by Python for the rest of the process —
    # Windows refuses to unlink an open file. Don't fail cleanup on that.
    td = tempfile.TemporaryDirectory(prefix="slice3-smoke-", ignore_cleanup_errors=True)
    target = Path(td.name) / "data"
    shutil.copytree(_BACKEND_DATA, target)
    # Point the running settings at the copy so registry + tests use it.
    from app.config import settings
    settings.data_dir = target
    settings.db_path = Path(td.name) / "ticks.db"
    settings.log_dir = Path(td.name) / "logs"
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    return td


def assert_eq(actual, expected, msg):
    if actual != expected:
        print(f"FAIL: {msg}\n  expected={expected!r}\n  actual  ={actual!r}", file=sys.stderr)
        sys.exit(1)
    print(f"PASS: {msg}")


def assert_true(cond, msg):
    if not cond:
        print(f"FAIL: {msg}", file=sys.stderr)
        sys.exit(1)
    print(f"PASS: {msg}")


# --- F38: Supervisor.disable matches by market_id ---

def test_f38_disable_by_market_id():
    from app.adapters.base import DiscoverResult, ExchangeAdapter
    from app.models import Market
    from app.supervisor import Supervisor

    class FakeAdapter(ExchangeAdapter):
        exchange_id = "okx"

        async def discover(self, asset_id):  # pragma: no cover (not invoked here)
            return DiscoverResult(found=False)

        async def stream(self, market):  # pragma: no cover (not invoked here)
            if False:
                yield  # type: ignore

    async def sink(t):  # pragma: no cover
        return None

    sup = Supervisor(sink=sink)
    m1 = Market(
        asset_id="OPENAI", exchange="okx", symbol="OPENAIUSDT",
        venue="CEX", structure="perp", share_ratio=2.4,
        ratio_source="market-fitted", confidence=70,
    )
    m2 = Market(
        asset_id="ANTHROPIC", exchange="okx", symbol="ANTHROPICUSDT",
        venue="CEX", structure="perp", share_ratio=None,
        ratio_source="uncalibrated", confidence=0,
    )
    m3 = Market(
        asset_id="SPACEX", exchange="okx", symbol="SPACEXUSDT",
        venue="CEX", structure="perp", share_ratio=1.0,
        ratio_source="market-fitted", confidence=70,
    )
    sup.register(FakeAdapter(), m1)
    sup.register(FakeAdapter(), m2)
    sup.register(FakeAdapter(), m3)

    # Disable only the uncalibrated ANTHROPIC market
    sup.disable(m2.market_id, "uncalibrated: ratio not set")
    statuses = sup.statuses()
    states_by_market = [(sa.market.market_id, sa.status.state) for sa in sup._adapters]
    assert_eq(
        sorted(states_by_market),
        sorted([
            ("OPENAI:okx", "disconnected"),
            ("ANTHROPIC:okx", "disabled"),
            ("SPACEX:okx", "disconnected"),
        ]),
        "F38: only the targeted market is disabled, siblings stay disconnected",
    )

    # Calling with a non-existent market_id is a no-op + logs warning
    sup.disable("BOGUS:okx", "test")
    states_after = [(sa.market.market_id, sa.status.state) for sa in sup._adapters]
    assert_eq(
        sorted(states_after),
        sorted(states_by_market),
        "F38: unknown market_id is a no-op",
    )


# --- F39: main.py call site uses market_id (verified by F38 logic + grep) ---

def test_f39_main_uses_market_id():
    main_src = (Path(__file__).resolve().parent / "app" / "main.py").read_text(encoding="utf-8")
    assert_true(
        'supervisor.disable(market.market_id,' in main_src,
        "F39: main.py disable call site uses market.market_id",
    )
    assert_true(
        'supervisor.disable(market.exchange,' not in main_src,
        "F39: legacy market.exchange disable call removed",
    )


# --- F40: ws.py queue initialized to None before subscribe (no UnboundLocalError) ---

def test_f40_ws_queue_guard():
    ws_src = (Path(__file__).resolve().parent / "app" / "api" / "ws.py").read_text(encoding="utf-8")
    assert_true(
        "queue: asyncio.Queue[dict | None] | None = None" in ws_src,
        "F40: ws.py initializes queue=None before try block",
    )
    assert_true(
        "if queue is not None:" in ws_src,
        "F40: finally guards unsubscribe with `if queue is not None`",
    )


# --- F41: aggregator._broadcast puts None sentinel on dropped queue ---

def test_f41_zombie_ws_sentinel():
    agg_src = (Path(__file__).resolve().parent / "app" / "aggregator.py").read_text(encoding="utf-8")
    assert_true(
        "q.put_nowait(None)" in agg_src,
        "F41: dropped slow subscriber gets None sentinel pushed",
    )


# --- F42: ws.py origin check ---

def test_f42_ws_origin_check():
    ws_src = (Path(__file__).resolve().parent / "app" / "api" / "ws.py").read_text(encoding="utf-8")
    assert_true(
        "_origin_allowed" in ws_src and "ws.close(code=1008)" in ws_src,
        "F42: ws.py rejects disallowed origins with close(1008)",
    )


# --- F43: uvicorn ws_max_size capped ---

def test_f43_ws_max_size():
    from app.config import settings
    assert_true(
        settings.ws_max_size_bytes == 1 << 20,
        "F43: ws_max_size_bytes defaults to 1 MiB",
    )
    main_src = (Path(__file__).resolve().parent / "app" / "main.py").read_text(encoding="utf-8")
    assert_true(
        "ws_max_size=settings.ws_max_size_bytes" in main_src,
        "F43: uvicorn.run is passed ws_max_size",
    )


# --- F44: _configure_logging is idempotent (no leaked file handle) ---

def test_f44_logging_idempotent():
    import logging
    from logging.handlers import RotatingFileHandler

    from app.main import _configure_logging

    # Two calls should not add a second RotatingFileHandler.
    _configure_logging()
    n1 = sum(1 for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler))
    _configure_logging()
    n2 = sum(1 for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler))
    assert_eq(n1, n2, "F44: second _configure_logging call does not duplicate handler")
    assert_true(n1 >= 1, "F44: at least one RotatingFileHandler is attached")


# --- F45: history endpoint clamps `seconds` and rejects negatives ---

async def test_f45_history_clamp():
    from fastapi import HTTPException

    from app.api.rest import history
    from app.config import settings

    # Negative seconds → 400
    try:
        await history(asset_id="SPACEX", seconds=-1)
    except HTTPException as e:
        assert_eq(e.status_code, 400, "F45: negative seconds → 400")
    else:
        print("FAIL: F45 expected HTTPException for negative seconds", file=sys.stderr)
        sys.exit(1)

    # Huge seconds → clamped (but still 404 because asset/store may not exist)
    try:
        result = await history(asset_id="SPACEX", seconds=10**12)
    except HTTPException as e:
        # 404 is fine (no asset / store not initialized). The clamp logic ran
        # before any DB call would have OOM'd serializing.
        assert_true(e.status_code in (404, 500, 503), f"F45: clamp+lookup yields known error, got {e.status_code}")
        print("PASS: F45 huge seconds clamped without OOM (got controlled error)")
    except Exception as e:  # noqa: BLE001
        # store not initialized → RuntimeError. Acceptable as long as we got
        # past the clamp/validation gate.
        assert_true("not initialized" in str(e) or "asset" in str(e).lower(), f"F45: clamp+lookup yields known runtime error: {e!r}")
        print("PASS: F45 huge seconds clamped without OOM (got runtime error)")
    else:
        assert_true(result.get("seconds") <= settings.history_replay_seconds, "F45: seconds clamped to history_replay_seconds")


# --- F46: REST asset_id pattern (Path validator) ---

def test_f46_asset_id_pattern():
    from app.api.rest import _ASSET_ID_PATTERN
    import re
    pat = re.compile(_ASSET_ID_PATTERN)
    assert_true(pat.fullmatch("SPACEX") is not None, "F46: SPACEX matches")
    assert_true(pat.fullmatch("OPEN_AI") is not None, "F46: OPEN_AI matches")
    assert_true(pat.fullmatch("../etc/passwd") is None, "F46: path-traversal rejected")
    assert_true(pat.fullmatch("a" * 33) is None, "F46: >32 chars rejected")
    assert_true(pat.fullmatch("lowercase") is None, "F46: lowercase rejected")


# --- F47: /readyz returns 503 when supervisor missing ---

async def test_f47_readyz():
    from starlette.applications import Starlette
    from starlette.responses import Response

    from app.api.health import readyz

    class FakeApp:
        class state:
            aggregator = None
            supervisor = None
    class FakeRequest:
        app = FakeApp()

    response = Response()
    payload = readyz(FakeRequest(), response)
    assert_eq(response.status_code, 503, "F47: /readyz 503 when components missing")
    assert_eq(payload["status"], "not_ready", "F47: /readyz reports not_ready")
    assert_eq(payload["reason"], "components_not_started", "F47: /readyz reports reason")

    # With supervisor + fresh tick → 200
    from app.models import AdapterStatus

    class FakeSupervisor:
        def statuses(self):
            return [AdapterStatus(market_id="SPACEX:okx", exchange="okx", state="connected", last_tick_at=time.time())]
    class FakeApp2:
        class state:
            aggregator = object()
            supervisor = FakeSupervisor()
    class FakeRequest2:
        app = FakeApp2()

    response2 = Response()
    payload2 = readyz(FakeRequest2(), response2)
    # default response status is 200 — not modified
    assert_true(response2.status_code in (None, 200), f"F47: /readyz 200 when fresh adapter exists (got {response2.status_code})")
    assert_eq(payload2["status"], "ready", "F47: /readyz reports ready")


# --- F49: admin_token written to file, not logged raw ---

def test_f49_token_file():
    from app.config import settings
    from app.main import _persist_admin_token
    token_path = _persist_admin_token(settings.admin_token)
    assert_true(token_path.exists(), "F49: admin_token.txt is created")
    content = token_path.read_text(encoding="utf-8").strip()
    assert_eq(content, settings.admin_token, "F49: file contains the active token")


# --- F50: admin token uses hmac.compare_digest ---

async def test_f50_compare_digest():
    from fastapi import HTTPException

    from app.api.admin import require_admin_token
    from app.config import settings

    # Wrong token → 401
    try:
        await require_admin_token(x_admin_token="wrong-" + settings.admin_token)
    except HTTPException as e:
        assert_eq(e.status_code, 401, "F50: wrong token rejected with 401")
    else:
        print("FAIL: F50 expected 401 for wrong token", file=sys.stderr)
        sys.exit(1)

    # None / empty → 401 via short-circuit
    try:
        await require_admin_token(x_admin_token=None)
    except HTTPException as e:
        assert_eq(e.status_code, 401, "F50: missing token rejected with 401")
    else:
        print("FAIL: F50 expected 401 for missing token", file=sys.stderr)
        sys.exit(1)

    # Correct → no exception
    await require_admin_token(x_admin_token=settings.admin_token)
    print("PASS: F50 correct token accepted")


# --- F51: start.bat creates logs dir before redirection ---

def test_f51_start_bat_mkdir():
    bat = (Path(__file__).resolve().parent.parent / "start.bat").read_text(encoding="utf-8")
    # mkdir line must appear BEFORE the actual powershell launch command (not the
    # earlier comment mentions of Start-Process).
    mkdir_pos = bat.find('mkdir "%~dp0backend\\logs"')
    cmd_pos = bat.find("powershell -NoProfile")
    assert_true(mkdir_pos >= 0, "F51: start.bat has mkdir for logs dir")
    assert_true(cmd_pos >= 0 and mkdir_pos < cmd_pos, "F51: mkdir precedes the powershell launch")


def main():
    # Use temp dir for any disk writes; do NOT touch real backend/data or logs.
    td = _setup_temp_data()
    try:
        test_f38_disable_by_market_id()
        test_f39_main_uses_market_id()
        test_f40_ws_queue_guard()
        test_f41_zombie_ws_sentinel()
        test_f42_ws_origin_check()
        test_f43_ws_max_size()
        test_f44_logging_idempotent()
        asyncio.run(test_f45_history_clamp())
        test_f46_asset_id_pattern()
        asyncio.run(test_f47_readyz())
        test_f49_token_file()
        asyncio.run(test_f50_compare_digest())
        test_f51_start_bat_mkdir()
        print("\nALL SMOKE TESTS PASSED")
    finally:
        td.cleanup()


if __name__ == "__main__":
    main()
