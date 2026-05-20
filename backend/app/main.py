from __future__ import annotations

import contextlib
import logging
import os
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .adapters.bingx import BingXAdapter
from .adapters.bitget import BitgetAdapter
from .adapters.gate import GateAdapter
from .adapters.hyperliquid import HyperliquidAdapter
from .adapters.okx import OkxAdapter
from .aggregator import Aggregator
from .api import admin as admin_api
from .api import health as health_api
from .api import rest as rest_api
from .api import ws as ws_api
from .config import settings
from .registry import get_registry
from .store import get_store
from .supervisor import Supervisor


def _configure_logging() -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Guard BEFORE constructing the handler — otherwise the second call (from
    # lifespan, after __main__ already ran us) opens a second file handle to
    # app.log that the early-return strands. On Windows that handle locks the
    # file and breaks log rotation.
    target_path = str((settings.log_dir / "app.log").resolve())
    have_file_handler = any(
        getattr(h, "baseFilename", None) == target_path for h in root.handlers
    )
    if not have_file_handler:
        handler = RotatingFileHandler(
            settings.log_dir / "app.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in root.handlers):
        root.addHandler(logging.StreamHandler())

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


log = structlog.get_logger("app.main")


def _persist_admin_token(token: str) -> Path:
    """Write the active admin token to backend/logs/admin_token.txt.

    Previously the token was logged at INFO into the rotating app.log; that
    file backs up, syncs to OneDrive/Dropbox, etc. Writing to a separate file
    gives the operator one retrieval path and keeps the secret out of the
    log archive. On POSIX we tighten the file to 0o600; Windows ACLs are
    left at the inherited default (real lockdown there would require
    win32security — tracked as a follow-up)."""
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    target = settings.log_dir / "admin_token.txt"
    target.write_text(token + "\n", encoding="utf-8")
    if os.name == "posix":
        with contextlib.suppress(OSError):
            os.chmod(target, 0o600)
    return target


# Wiring registry: which adapter handles which exchange_id
_ADAPTER_CLASSES = {
    # Hyperliquid HIP-3 builder DEXes are distinct routes (different `dex`
    # parameter on /info), so each is registered as its own exchange key.
    # They share the HyperliquidAdapter class — the adapter reads
    # market.symbol ("xyz:SPCX" / "vntl:SPACEX") to know which dex to poll.
    "hl-xyz":  HyperliquidAdapter,
    "hl-vntl": HyperliquidAdapter,
    "okx": OkxAdapter,
    "bitget": BitgetAdapter,
    "gate": GateAdapter,
    # BingX lists two distinct pre-IPO products under one platform — Ventuals-
    # mirror valuation perps (V2 suffix) and BingX-native PreStocks (P2). They
    # share the BingXAdapter class but get distinct exchange keys so the
    # supervisor registers them as independent markets (same trick as
    # hyperliquid hl-xyz / hl-vntl above).
    "bingx": BingXAdapter,
    "bingx-prestocks": BingXAdapter,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Idempotent: also called from __main__ before uvicorn.run so that
    # uvicorn's own bind/startup errors land in app.log. The guards inside
    # _configure_logging skip duplicate handlers on the second call.
    _configure_logging()
    log.info("startup", host=settings.host, port=settings.port, db=str(settings.db_path))
    # Token retrieval path: write the value to admin_token.txt and log only
    # source + path. Keeps the secret out of the rotating app.log archive.
    admin_token_source = "env" if os.environ.get("ADMIN_TOKEN") else "generated"
    token_path = _persist_admin_token(settings.admin_token)
    log.info("admin_token", source=admin_token_source, path=str(token_path))

    registry = get_registry()
    log.info(
        "registry_loaded",
        assets=len(registry.list_assets()),
        markets=len(registry.list_markets()),
    )

    # Acquire components incrementally so a failure midway through startup can
    # unwind cleanly. Previously a crash between aggregator.start() and
    # supervisor.start() would leave the aggregator running while the finally
    # block tried to stop a never-assigned supervisor — silent leak.
    store = None
    aggregator = None
    supervisor = None
    try:
        store = get_store()
        await store.init()
        log.info("store_initialized", db_path=str(settings.db_path))

        aggregator = Aggregator(registry, store)
        supervisor = Supervisor(sink=aggregator.on_tick)
        aggregator.attach_status_source(supervisor.statuses)

        # Register adapters per market in registry
        for market in registry.list_markets():
            adapter_cls = _ADAPTER_CLASSES.get(market.exchange)
            if adapter_cls is None:
                log.warning("no_adapter_class", exchange=market.exchange)
                continue
            adapter = adapter_cls()
            supervisor.register(adapter, market)
            # Disable adapters whose market is still uncalibrated. The previous
            # `hyperliquid` blanket-disable is gone now that the adapter understands
            # HIP-3 builder DEXes (hl-xyz, hl-vntl) via dex-prefixed symbols.
            if market.ratio_source == "uncalibrated":
                supervisor.disable(market.market_id, "uncalibrated: ratio not set")

        await aggregator.start()
        await supervisor.start()

        # Mount WS now that aggregator + supervisor are live
        ws_api.attach(app, aggregator)

        app.state.aggregator = aggregator
        app.state.supervisor = supervisor
    except Exception:
        log.exception("startup_failed_unwinding")
        if supervisor is not None:
            with contextlib.suppress(Exception):
                await supervisor.stop()
        if aggregator is not None:
            with contextlib.suppress(Exception):
                await aggregator.stop()
        if store is not None:
            with contextlib.suppress(Exception):
                await store.close()
        raise

    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            await supervisor.stop()
        with contextlib.suppress(Exception):
            await aggregator.stop()
        with contextlib.suppress(Exception):
            await store.close()
        log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Pre-IPO Normalizer",
        version="0.1.0",
        description="Real-time synthetic pre-market valuation normalizer.",
        lifespan=lifespan,
    )
    # Restricted CORS. The earlier `allow_origins=["*"]` let any browser tab
    # preflight /api/admin/* and (combined with a leaked token) corrupt
    # share_ratio. Allow-list is sourced from settings so the operator can
    # extend it without code edits.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(health_api.router)
    app.include_router(rest_api.router)
    app.include_router(admin_api.router)

    # P11: serve the prototype (HTML + app.jsx + data-live.js + styles.css)
    # directly from FastAPI so `python -m app.main` is the only command needed
    # for the full dashboard. Project root = parent of backend/.
    #
    # Codex round-2 fix: do NOT mount StaticFiles over the project root — that
    # would expose backend/, .claude/, screenshots/, etc. Instead, register
    # explicit handlers only for the small set of files the prototype loads.
    project_root = settings.base_dir.parent
    # `data.js` was the original static-data stub; `data-live.js` replaced
    # it and the prototype HTML no longer references data.js. Dropped from
    # the allow-list to reduce the static-serve surface. Slice 5 F77.
    _allowed_root_files = ("app.jsx", "data-live.js", "styles.css")

    if (project_root / "Pre-IPO Normalizer.html").exists():
        @app.get("/", include_in_schema=False)
        def root_index() -> FileResponse:
            return FileResponse(project_root / "Pre-IPO Normalizer.html")

        for name in _allowed_root_files:
            file_path = project_root / name
            if not file_path.exists():
                continue

            def make_handler(p):
                def _h() -> FileResponse:
                    return FileResponse(p)
                return _h

            app.get(f"/{name}", include_in_schema=False)(make_handler(file_path))

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    # Configure logging BEFORE uvicorn binds. Previously this only ran inside
    # `lifespan`, which fires after the bind — so an EADDRINUSE on :8000 left
    # zero trace in app.log (uvicorn's stderr was hidden by start.bat's
    # -WindowStyle Hidden, masking the failure entirely).
    _configure_logging()
    log.info("pre_uvicorn_bind", host=settings.host, port=settings.port)

    # log_config=None skips uvicorn's own dictConfig, leaving uvicorn,
    # uvicorn.error, and uvicorn.access as vanilla loggers that propagate to
    # root — where our RotatingFileHandler catches them. access_log=False
    # mutes the per-request line that would otherwise flood app.log with
    # FE polling traffic.
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_config=None,
        access_log=False,
        ws_max_size=settings.ws_max_size_bytes,
    )
