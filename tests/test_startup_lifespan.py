"""
tests/test_startup_lifespan.py

RED phase — FastAPI startup lifespan handles Vault errors gracefully.

PROBLEM
-------
app/main.py currently has no lifespan handler. When Vault is configured
but sealed or unreachable, Settings() raises VaultUnavailableError during
the first request that calls Depends(get_settings). The result is:
  - A raw 500 traceback in the logs instead of a clear message
  - No distinction between "Vault sealed after restart" and a real bug
  - The health endpoint returns 200 even though the service is broken

TARGET BEHAVIOUR
----------------
1. At startup (lifespan), the app eagerly calls get_settings() to validate
   credentials are reachable. If they are not, it logs a clear human-readable
   message and exits cleanly rather than silently serving broken requests.

2. The /health endpoint reports Vault status explicitly:
     {"status": "healthy", "vault": "connected"}    — Vault active and readable
     {"status": "healthy", "vault": "not_configured"} — env-only mode (no Vault)

3. VaultUnavailableError propagates as HTTP 503 with a clear JSON body
   when it surfaces during a request (not just at startup), so callers
   know to retry after unsealing rather than assuming the service is broken.

TESTS
-----
  test_startup_with_vault_unconfigured_succeeds
    No VAULT_ADDR — settings loads from env, no Vault call. App starts.

  test_startup_with_vault_reachable_succeeds
    VAULT_ADDR set, VaultClient mock returns credentials. App starts.

  test_startup_with_vault_unreachable_raises_runtime_error_with_clear_message
    VAULT_ADDR set, VaultClient raises VaultUnavailableError.
    The lifespan must raise RuntimeError with a message containing
    "Vault" and "sealed" or "unreachable" — not a raw traceback.

  test_health_reports_vault_not_configured_when_vault_addr_absent
    GET /health → {"status": "healthy", "vault": "not_configured"}

  test_health_reports_vault_connected_when_vault_active
    GET /health → {"status": "healthy", "vault": "connected"}

  test_vault_unavailable_error_returns_503_during_request
    If VaultUnavailableError is raised mid-request (e.g. token expired),
    the error handler must return 503 with a JSON body, not 500.

CURRENT STATE: all tests RED because:
  - app/main.py has no lifespan
  - /health always returns {"status": "healthy", "database": "connected_placeholder"}
  - VaultUnavailableError has no HTTP handler registration
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import SecretStr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vault_settings(vault_active: bool):
    """Return a Settings-like object for the given vault state."""
    from app.config import Settings

    if vault_active:
        return Settings.model_construct(
            inovar_username="vault_user",
            inovar_password=SecretStr("vault_pass"),
            inovar_url="https://x.com",
            vault_addr="http://vault:8200",
            vault_role_id="role-id",
            vault_secret_id=SecretStr("secret-id"),
        )
    else:
        return Settings.model_construct(
            inovar_username="env_user",
            inovar_password=SecretStr("env_pass"),
            inovar_url="https://x.com",
            vault_addr="",
            vault_role_id="",
            vault_secret_id=SecretStr(""),
        )


# ===========================================================================
# Startup behaviour
# ===========================================================================

def test_startup_with_vault_unconfigured_succeeds(monkeypatch):
    """
    When VAULT_ADDR is absent, app startup must succeed without any Vault call.
    """
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    monkeypatch.setenv("INOVAR_USERNAME", "env_user")
    monkeypatch.setenv("INOVAR_PASSWORD", "env_pass")

    from importlib import reload
    import app.config as cfg
    reload(cfg)

    with patch("app.config.VaultClient") as MockVC:
        from fastapi.testclient import TestClient
        import app.main as main_module
        reload(main_module)
        client = TestClient(main_module.app)
        resp = client.get("/health")

    MockVC.assert_not_called()
    assert resp.status_code == 200


def test_startup_with_vault_reachable_succeeds(monkeypatch):
    """
    When VAULT_ADDR is set and Vault responds correctly, app startup succeeds.
    """
    monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
    monkeypatch.setenv("VAULT_ROLE_ID", "role-id")
    monkeypatch.setenv("VAULT_SECRET_ID", "secret-id")

    from importlib import reload
    import app.config as cfg
    reload(cfg)

    mock_vc = MagicMock()
    mock_vc.read_inovar_credentials.return_value = {
        "inovar_username": "u",
        "inovar_password": "p",
    }

    with patch("app.config.VaultClient", return_value=mock_vc):
        from fastapi.testclient import TestClient
        import app.main as main_module
        reload(main_module)
        client = TestClient(main_module.app)
        resp = client.get("/health")

    assert resp.status_code == 200


def test_startup_with_vault_unreachable_raises_runtime_error_with_clear_message(monkeypatch):
    """
    When Vault is configured but unreachable, the lifespan must raise a
    RuntimeError whose message names Vault and explains the problem
    (e.g. 'sealed', 'unreachable', or both).

    This is RED because app/main.py has no lifespan handler — the error
    currently propagates as a raw VaultUnavailableError traceback.
    """
    monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
    monkeypatch.setenv("VAULT_ROLE_ID", "role-id")
    monkeypatch.setenv("VAULT_SECRET_ID", "secret-id")

    from importlib import reload
    import app.config as cfg
    reload(cfg)
    from app.exceptions import VaultUnavailableError

    mock_vc = MagicMock()
    mock_vc.login.side_effect = VaultUnavailableError("connection refused")

    with patch("app.config.VaultClient", return_value=mock_vc):
        import app.main as main_module
        reload(main_module)

        with pytest.raises(RuntimeError) as exc_info:
            from fastapi.testclient import TestClient
            with TestClient(main_module.app):
                pass  # lifespan fires on enter

    message = str(exc_info.value).lower()
    assert "vault" in message, (
        f"RuntimeError message does not mention 'Vault': {exc_info.value!r}. "
        "The message must name what went wrong so an operator knows to unseal."
    )
    # Must mention sealed or unreachable — concrete, actionable
    assert any(word in message for word in ("seal", "unreachable", "connect", "reach")), (
        f"RuntimeError message does not explain how to fix it: {exc_info.value!r}. "
        "Include 'sealed' or 'unreachable' so the operator knows what to do."
    )


# ===========================================================================
# /health endpoint — Vault status reporting
# ===========================================================================

def test_health_reports_vault_not_configured_when_vault_addr_absent(monkeypatch):
    """
    GET /health must include a 'vault' key set to 'not_configured' when
    VAULT_ADDR is not set.

    Currently /health returns {"status": "healthy", "database": "..."} with
    no vault field — this test is RED.
    """
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    monkeypatch.setenv("INOVAR_USERNAME", "u")
    monkeypatch.setenv("INOVAR_PASSWORD", "p")

    from importlib import reload
    import app.config as cfg
    reload(cfg)

    with patch("app.config.VaultClient"):
        import app.main as main_module
        reload(main_module)
        from fastapi.testclient import TestClient
        client = TestClient(main_module.app)
        resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert "vault" in body, (
        f"GET /health response has no 'vault' key: {body}. "
        "Add vault status to the health response."
    )
    assert body["vault"] == "not_configured", (
        f"Expected vault='not_configured' but got vault={body['vault']!r}."
    )


def test_health_reports_vault_connected_when_vault_active(monkeypatch):
    """
    GET /health must include vault='connected' when Vault is configured
    and was successfully read at startup.
    """
    monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
    monkeypatch.setenv("VAULT_ROLE_ID", "r")
    monkeypatch.setenv("VAULT_SECRET_ID", "s")

    from importlib import reload
    import app.config as cfg
    reload(cfg)

    mock_vc = MagicMock()
    mock_vc.read_inovar_credentials.return_value = {
        "inovar_username": "u",
        "inovar_password": "p",
    }

    with patch("app.config.VaultClient", return_value=mock_vc):
        import app.main as main_module
        reload(main_module)
        from fastapi.testclient import TestClient
        client = TestClient(main_module.app)
        resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body.get("vault") == "connected", (
        f"Expected vault='connected' but got: {body}"
    )


# ===========================================================================
# HTTP error mapping — VaultUnavailableError → 503
# ===========================================================================

def test_vault_unavailable_error_returns_503_json_response():
    """
    If VaultUnavailableError is raised during a request (e.g. token expired
    mid-session), the global error handler must return HTTP 503 with a JSON
    body — not HTTP 500 or an unhandled traceback.

    Currently app/errors.py handles DomainError via status_code, but
    VaultUnavailableError.status_code = 503 — verify the handler actually
    produces the right response for a request that triggers it.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from app.exceptions import VaultUnavailableError

    mock_settings = MagicMock()
    mock_settings.vault_addr = "http://vault:8200"
    mock_settings.inovar_password = SecretStr("p")
    mock_settings.inovar_username = "u"
    mock_settings.inovar_url = "https://x.com"

    # Make the scraper raise VaultUnavailableError when constructed
    with patch("app.routers.horario.InovarScraperService") as MockScraper:
        MockScraper.side_effect = VaultUnavailableError("Vault is sealed")
        with patch("app.routers.horario.get_settings", return_value=mock_settings):
            with patch("app.database.get_db_session") as mock_db:
                mock_db.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
                mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post("/api/v1/horarios/sync?week=next")

    assert resp.status_code == 503, (
        f"Expected 503 for VaultUnavailableError but got {resp.status_code}. "
        "The DomainError handler should use VaultUnavailableError.status_code."
    )
    body = resp.json()
    assert "error_code" in body or "detail" in body, (
        f"Response body has no error info: {body}"
    )
