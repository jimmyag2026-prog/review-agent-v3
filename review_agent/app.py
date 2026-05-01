from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI

from . import secrets as secrets_mod
from .config import Config, load as load_config
from .core.dispatcher import Dispatcher
from .core.storage import Storage
from .lark.client import LarkClient
from .llm.deepseek import DeepSeekClient
from .pipeline.ingest_backends import (
    AudioBackend, ImageBackend, PdfBackend, TextBackend, WebScrapBackend,
)
from .routers import dashboard, health, lark_webhook
from .slack import SlackAdapter
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
    queue = TaskQueue(storage)

    # ── Slack adapter (optional — starts only if tokens are configured) ──
    slack_adapter = None
    slack_bot_token = cfg.slack.bot_token or secrets.get("SLACK_BOT_TOKEN", "")
    slack_app_token = cfg.slack.app_token or secrets.get("SLACK_APP_TOKEN", "")
    if slack_bot_token and slack_app_token:
        try:
            slack_adapter = SlackAdapter(
                bot_token=slack_bot_token,
                app_token=slack_app_token,
                bot_user_id=cfg.slack.bot_user_id or secrets.get("SLACK_BOT_USER_ID", ""),
                storage=storage,
                queue=queue,
            )
            # Set persistence path for thread participation tracking
            slack_adapter.set_persistence_path(
                Path(cfg.paths.fs) / "slack_thread_participation.json"
            )
            _logger.info("Slack adapter configured (%s)", slack_adapter._bot_user_id or "unresolved")
        except Exception as e:
            _logger.warning("Slack adapter setup failed (tokens present but error): %s", e)
            slack_adapter = None

    dispatcher = Dispatcher(
        cfg=cfg, storage=storage, llm=llm, lark=lark,
        slack_adapter=slack_adapter,
        ingest_backends=[
            TextBackend(), PdfBackend(), ImageBackend(),
            AudioBackend(), WebScrapBackend(),
        ],
    )

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
    app.state.slack = slack_adapter
    app.state.dispatcher = dispatcher
    app.state.queue = queue
    app.state.worker_task = None

    @app.on_event("startup")
    async def _start():
        recovered = await queue.replay_pending()
        if recovered:
            _logger.info("queue replay: %d tasks restored", recovered)
        app.state.worker_task = asyncio.create_task(run_worker(queue, dispatcher.dispatch))
        # Start Slack adapter if configured
        if app.state.slack is not None:
            await app.state.slack.start()

    @app.on_event("shutdown")
    async def _stop():
        if app.state.worker_task:
            app.state.worker_task.cancel()
        # Stop Slack adapter first (before closing other connections)
        if app.state.slack is not None:
            await app.state.slack.stop()
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
