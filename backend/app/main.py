from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .adapters.bitget import BitgetAdapter
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
    "hyperliquid": HyperliquidAdapter,
    "okx": OkxAdapter,
    "bitget": BitgetAdapter,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    log.info("startup", host=settings.host, port=settings.port, db=str(settings.db_path))

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
        # Disable adapters whose stream() is not yet implemented or whose
        # market is uncalibrated. Hyperliquid HIP-3 routing = P12+.
        if market.ratio_source == "uncalibrated":
            supervisor.disable(market.exchange, "uncalibrated: ratio not set")
        elif market.exchange == "hyperliquid":
            supervisor.disable(market.exchange, "deferred: HIP-3 routing not yet implemented")

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
    # Permissive CORS for local dev (prototype served from :8080, file://, or
    # FastAPI :8000). P11 single-process deploy makes same-origin the default.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
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

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
