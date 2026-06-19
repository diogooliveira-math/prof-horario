"""
Step 4 — RED phase: app/config.py Settings class.

Drives creation of a pydantic-settings Settings class that reads
INOVAR_USERNAME, INOVAR_PASSWORD, INOVAR_URL from the environment.

No network, no Playwright, no DB — pure unit tests on a value object.
"""
import os
import pytest


def test_settings_reads_inovar_username_from_env(monkeypatch):
    monkeypatch.setenv("INOVAR_USERNAME", "prof_user")
    from importlib import reload
    import app.config as cfg
    reload(cfg)
    s = cfg.get_settings()
    assert s.inovar_username == "prof_user"


def test_settings_reads_inovar_password_from_env(monkeypatch):
    monkeypatch.setenv("INOVAR_PASSWORD", "s3cr3t")
    from importlib import reload
    import app.config as cfg
    reload(cfg)
    s = cfg.get_settings()
    assert s.inovar_password.get_secret_value() == "s3cr3t"


def test_settings_inovar_url_defaults_to_epralima(monkeypatch):
    monkeypatch.delenv("INOVAR_URL", raising=False)
    from importlib import reload
    import app.config as cfg
    reload(cfg)
    s = cfg.get_settings()
    assert "epralima.inovarmais.com" in s.inovar_url


def test_settings_inovar_url_can_be_overridden(monkeypatch):
    monkeypatch.setenv("INOVAR_URL", "https://test.inovar.local/login")
    from importlib import reload
    import app.config as cfg
    reload(cfg)
    s = cfg.get_settings()
    assert s.inovar_url == "https://test.inovar.local/login"


def test_get_settings_returns_same_instance():
    """get_settings() must be cacheable (lru_cache or equivalent)."""
    from importlib import reload
    import app.config as cfg
    reload(cfg)
    assert cfg.get_settings() is cfg.get_settings()


def test_settings_has_no_plaintext_repr_for_password(monkeypatch):
    """Password must not appear in repr/str — pydantic SecretStr or masked."""
    monkeypatch.setenv("INOVAR_PASSWORD", "supersecret")
    from importlib import reload
    import app.config as cfg
    reload(cfg)
    s = cfg.get_settings()
    assert "supersecret" not in repr(s)
    assert "supersecret" not in str(s)
