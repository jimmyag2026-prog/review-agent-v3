"""Test the set-model CLI + secrets.upsert_env_value."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from review_agent.secrets import upsert_env_value


def test_upsert_creates_file(tmp_path):
    p = tmp_path / "secrets.env"
    out = upsert_env_value("REVIEW_AGENT_MODEL", "deepseek-v4-flash", env_file=str(p))
    assert out == p
    body = p.read_text()
    assert "REVIEW_AGENT_MODEL=deepseek-v4-flash" in body
    assert oct(p.stat().st_mode)[-3:] == "600"


def test_upsert_replaces_existing(tmp_path):
    p = tmp_path / "secrets.env"
    p.write_text("# comment\nDEEPSEEK_API_KEY=sk-old\nREVIEW_AGENT_MODEL=deepseek-v4-pro\n")
    upsert_env_value("REVIEW_AGENT_MODEL", "deepseek-v4-flash", env_file=str(p))
    body = p.read_text()
    lines = [ln for ln in body.splitlines() if ln.strip() and not ln.startswith("#")]
    assert "DEEPSEEK_API_KEY=sk-old" in lines
    assert "REVIEW_AGENT_MODEL=deepseek-v4-flash" in lines
    # ensure no duplicate
    assert sum(1 for ln in lines if ln.startswith("REVIEW_AGENT_MODEL=")) == 1


def test_upsert_appends_when_missing(tmp_path):
    p = tmp_path / "secrets.env"
    p.write_text("DEEPSEEK_API_KEY=sk-test\n")
    upsert_env_value("REVIEW_AGENT_FAST_MODEL", "deepseek-v4-flash", env_file=str(p))
    body = p.read_text()
    assert "DEEPSEEK_API_KEY=sk-test" in body
    assert "REVIEW_AGENT_FAST_MODEL=deepseek-v4-flash" in body


def test_upsert_preserves_comments(tmp_path):
    p = tmp_path / "secrets.env"
    p.write_text("# === LLM ===\n# helpful comment\nDEEPSEEK_API_KEY=\n")
    upsert_env_value("REVIEW_AGENT_MODEL", "deepseek-v4-pro", env_file=str(p))
    body = p.read_text()
    assert "# === LLM ===" in body
    assert "# helpful comment" in body


def test_env_var_override_for_fast_model(monkeypatch):
    """REVIEW_AGENT_FAST_MODEL env var must override config.fast_model."""
    from review_agent.config import load
    monkeypatch.setenv("REVIEW_AGENT_FAST_MODEL", "deepseek-v4-flash-test")
    monkeypatch.delenv("REVIEW_AGENT_CONFIG", raising=False)
    cfg = load()
    assert cfg.llm.fast_model == "deepseek-v4-flash-test"


def test_env_var_override_for_provider(monkeypatch):
    from review_agent.config import load
    monkeypatch.setenv("REVIEW_AGENT_LLM_PROVIDER", "openai")
    monkeypatch.delenv("REVIEW_AGENT_CONFIG", raising=False)
    cfg = load()
    assert cfg.llm.provider == "openai"
