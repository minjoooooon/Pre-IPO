from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

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
    handler = RotatingFileHandler(
        settings.log_dir / "app.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
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
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Idempotent: also called from __main__ before uvicorn.run so that
    # uvicorn's own bind/startup errors land in app.log. The guards inside
    # _configure_logging skip duplicate handlers on the second call.
    _configure_logging()
    log.info("startup", host=settings.host, port=settings.port, db=str(settings.db_path))
    # Log the active admin token once at startup so the operator can copy it
    # into the FE prompt. If the operator set ADMIN_TOKEN explicitly, this
    # just confirms it; if not, this is the only place to retrieve the
    # auto-generated value.
    admin_token_source = "env" if os.environ.get("ADMIN_TOKEN") else "generated"
    log.info("admin_token", source=admin_token_source, token=settings.admin_token)

    registry = get_registry()
    log.info(
        "registry_loaded",
        assets=len(registry.list_assets()),
        markets=len(registry.list_markets()),
    )

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
            supervisor.disable(market.exchange, "uncalibrated: ratio not set")

    await aggregator.start()
    await supervisor.start()

    # Mount WS now that aggregator + supervisor are live
    ws_api.attach(app, aggregator)

    app.state.aggregator = aggregator
    app.state.supervisor = supervisor

    try:
        yield
    finally:
        await supervisor.stop()
        await aggregator.stop()
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
    _allowed_root_files = ("app.jsx", "data-live.js", "data.js", "styles.css")

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
    )
