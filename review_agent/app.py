from __future__ import annotations

import asyncio

from fastapi import FastAPI

from . import secrets as secrets_mod
from .config import Config, load as load_config
from .core.dispatcher import Dispatcher
from .core.storage import Storage
from .lark.client import LarkClient
from .llm.deepseek import DeepSeekClient
from .pipeline.ingest_backends import PdfBackend, TextBackend
from .routers import dashboard, health, lark_webhook
from .tasks.queue import TaskQueue
from .tasks.worker import run as run_worker
from .util import log

_logger = log.get(__name__)


def build_app(config_path: str | None = None) -> FastAPI:
    log.configure()
    cfg: Config = load_config(config_path)
    secrets = secrets_mod.load()

    storage = Storage(cfg.paths.db, cfg.paths.fs)
    llm = DeepSeekClient(
        api_key=secrets.get("DEEPSEEK_API_KEY", ""),
        base_url=cfg.llm.base_url, max_retries=cfg.llm.max_retries,
    )
    lark = LarkClient(
        app_id=cfg.lark.app_id or secrets.get("LARK_APP_ID", ""),
        app_secret=secrets.get("LARK_APP_SECRET", ""),
        base_url=cfg.lark.domain,
    )
    dispatcher = Dispatcher(
        cfg=cfg, storage=storage, llm=llm, lark=lark,
        ingest_backends=[TextBackend(), PdfBackend()],
    )
    queue = TaskQueue(storage)

    app = FastAPI(title="review-agent", version=__import__("review_agent").__version__)
    app.include_router(health.router)
    app.include_router(
        lark_webhook.make_router(
            storage, queue,
            encrypt_key=secrets.get("LARK_ENCRYPT_KEY", ""),
            verification_token=secrets.get("LARK_VERIFICATION_TOKEN", ""),
        )
    )
    if cfg.dashboard.enabled:
        app.include_router(dashboard.make_router(storage))

    app.state.cfg = cfg
    app.state.storage = storage
    app.state.llm = llm
    app.state.lark = lark
    app.state.dispatcher = dispatcher
    app.state.queue = queue
    app.state.worker_task = None

    @app.on_event("startup")
    async def _start():
        recovered = await queue.replay_pending()
        if recovered:
            _logger.info("queue replay: %d tasks restored", recovered)
        app.state.worker_task = asyncio.create_task(run_worker(queue, dispatcher.dispatch))

    @app.on_event("shutdown")
    async def _stop():
        if app.state.worker_task:
            app.state.worker_task.cancel()
        await llm.aclose()
        await lark.aclose()
        storage.close()

    return app


def _maybe_app():
    """Lazy-build so test imports don't need secrets / writable /var/lib paths."""
    import os
    if os.environ.get("REVIEW_AGENT_NO_AUTOBUILD") == "1":
        return None
    try:
        return build_app()
    except Exception as e:
        _logger.warning("build_app deferred (factory fallback): %s", e)
        return None


app = _maybe_app()
