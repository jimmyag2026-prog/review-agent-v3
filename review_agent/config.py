"""Runtime config — loaded from /etc/review-agent/config.toml + env.

When the prod paths (/var/lib/review-agent etc.) aren't writable (typical for dev/CI),
fall back to ~/.review-agent/ automatically so import never blows up.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = "/etc/review-agent/config.toml"


def _default_data_root() -> str:
    prod = "/var/lib/review-agent"
    try:
        Path(prod).mkdir(parents=True, exist_ok=True)
        # if we can mkdir we are root or owner; use prod path
        return prod
    except (PermissionError, OSError):
        return os.path.expanduser("~/.review-agent")


def _default_log_root() -> str:
    prod = "/var/log/review-agent"
    try:
        Path(prod).mkdir(parents=True, exist_ok=True)
        return prod
    except (PermissionError, OSError):
        return os.path.expanduser("~/.review-agent/logs")


_DATA_ROOT = _default_data_root()
_LOG_ROOT = _default_log_root()


@dataclass
class ServerCfg:
    bind: str = "127.0.0.1"
    port: int = 8080


@dataclass
class PathsCfg:
    db: str = f"{_DATA_ROOT}/state.db"
    fs: str = f"{_DATA_ROOT}/fs"
    log: str = _LOG_ROOT


@dataclass
class LarkCfg:
    app_id: str = ""
    domain: str = "https://open.feishu.cn"
    timeout_seconds: int = 30


@dataclass
class LlmCfg:
    provider: str = "deepseek"
    default_model: str = "deepseek-v4-pro"
    fast_model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com/v1"
    timeout_seconds: int = 90
    max_retries: int = 3


@dataclass
class ReviewCfg:
    max_rounds: int = 3
    max_rounds_with_request: int = 5
    top_n_findings: int = 5
    final_gate_max_fail_count: int = 2
    session_close_grace_seconds: int = 30


@dataclass
class DashboardCfg:
    enabled: bool = True
    host: str = "127.0.0.1"
    port_internal: int = 8765


@dataclass
class Config:
    server: ServerCfg = field(default_factory=ServerCfg)
    paths: PathsCfg = field(default_factory=PathsCfg)
    lark: LarkCfg = field(default_factory=LarkCfg)
    llm: LlmCfg = field(default_factory=LlmCfg)
    review: ReviewCfg = field(default_factory=ReviewCfg)
    dashboard: DashboardCfg = field(default_factory=DashboardCfg)


def load(path: str | None = None) -> Config:
    p = Path(path or os.environ.get("REVIEW_AGENT_CONFIG", DEFAULT_PATH))
    if not p.exists():
        return _from_env(Config())
    raw = tomllib.loads(p.read_text())
    return _from_env(_merge(Config(), raw))


def _merge(cfg: Config, raw: dict) -> Config:
    sections = {
        "server": cfg.server, "paths": cfg.paths, "lark": cfg.lark,
        "llm": cfg.llm, "review": cfg.review, "dashboard": cfg.dashboard,
    }
    for sect, obj in sections.items():
        if sect in raw and isinstance(raw[sect], dict):
            for k, v in raw[sect].items():
                if hasattr(obj, k):
                    setattr(obj, k, v)
    return cfg


def _from_env(cfg: Config) -> Config:
    if v := os.environ.get("REVIEW_AGENT_BIND"):
        cfg.server.bind = v
    if v := os.environ.get("REVIEW_AGENT_PORT"):
        cfg.server.port = int(v)
    if v := os.environ.get("REVIEW_AGENT_DB"):
        cfg.paths.db = v
    if v := os.environ.get("REVIEW_AGENT_FS"):
        cfg.paths.fs = v
    if v := os.environ.get("REVIEW_AGENT_LOG"):
        cfg.paths.log = v
    if v := os.environ.get("REVIEW_AGENT_LARK_APP_ID"):
        cfg.lark.app_id = v
    if v := os.environ.get("REVIEW_AGENT_MODEL"):
        cfg.llm.default_model = v
    if v := os.environ.get("REVIEW_AGENT_MAX_ROUNDS"):
        cfg.review.max_rounds = int(v)
    if v := os.environ.get("REVIEW_AGENT_TOP_N"):
        cfg.review.top_n_findings = int(v)
    return cfg
