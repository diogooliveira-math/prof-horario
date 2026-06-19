"""
tests/test_config_vault.py

Unit tests for the Vault-backed Settings path — Step 1, RED phase.

Tests mock VaultClient at the app.config import level so no real Vault
server is needed.  These run in CI unconditionally alongside all other
unit tests.
"""
import pytest
from unittest.mock import MagicMock, patch


def _make_vault_client_mock(credentials=None, login_raises=None, read_raises=None):
    mock = MagicMock()
    if login_raises:
        mock.login.side_effect = login_raises
    if read_raises:
        mock.read_inovar_credentials.side_effect = read_raises
    elif credentials is not None:
        mock.read_inovar_credentials.return_value = credentials
    return mock


# ---------------------------------------------------------------------------
# Env-only path (no VAULT_ADDR) — existing behaviour must be unchanged
# ---------------------------------------------------------------------------

def test_settings_reads_from_env_when_vault_addr_absent(monkeypatch):
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    monkeypatch.setenv("INOVAR_USERNAME", "env_user")
    monkeypatch.setenv("INOVAR_PASSWORD", "env_pass")

    from importlib import reload
    import app.config as cfg
    reload(cfg)

    with patch("app.config.VaultClient") as MockVC:
        s = cfg.Settings()

    MockVC.assert_not_called()
    assert s.inovar_username == "env_user"
    assert s.inovar_password.get_secret_value() == "env_pass"


def test_get_settings_does_not_call_vault_when_vault_addr_absent(monkeypatch):
    monkeypatch.delenv("VAULT_ADDR", raising=False)

    from importlib import reload
    import app.config as cfg
    reload(cfg)

    with patch("app.config.VaultClient") as MockVC:
        cfg.get_settings()

    MockVC.assert_not_called()


# ---------------------------------------------------------------------------
# Vault path (VAULT_ADDR set) — credentials come from Vault, not env
# ---------------------------------------------------------------------------

def test_settings_reads_from_vault_when_vault_addr_present(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
    monkeypatch.setenv("VAULT_ROLE_ID", "test-role-id")
    monkeypatch.setenv("VAULT_SECRET_ID", "test-secret-id")
    monkeypatch.setenv("INOVAR_USERNAME", "env_user_ignored")
    monkeypatch.setenv("INOVAR_PASSWORD", "env_pass_ignored")

    from importlib import reload
    import app.config as cfg
    reload(cfg)

    mock_vc = _make_vault_client_mock(
        credentials={"inovar_username": "vault_user", "inovar_password": "vault_pass"}
    )

    with patch("app.config.VaultClient", return_value=mock_vc):
        s = cfg.Settings()

    assert s.inovar_username == "vault_user"
    assert s.inovar_password.get_secret_value() == "vault_pass"


def test_settings_vault_path_ignores_env_credentials(monkeypatch):
    """When Vault is active, INOVAR_USERNAME/INOVAR_PASSWORD must be overwritten."""
    monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
    monkeypatch.setenv("VAULT_ROLE_ID", "role")
    monkeypatch.setenv("VAULT_SECRET_ID", "secret")
    monkeypatch.setenv("INOVAR_USERNAME", "should-be-ignored")

    from importlib import reload
    import app.config as cfg
    reload(cfg)

    mock_vc = _make_vault_client_mock(
        credentials={"inovar_username": "vault_user", "inovar_password": "vault_pass"}
    )

    with patch("app.config.VaultClient", return_value=mock_vc):
        s = cfg.Settings()

    assert s.inovar_username != "should-be-ignored"
    assert s.inovar_username == "vault_user"


def test_settings_vault_constructs_client_with_correct_args(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
    monkeypatch.setenv("VAULT_ROLE_ID", "the-role-id")
    monkeypatch.setenv("VAULT_SECRET_ID", "the-secret-id")

    from importlib import reload
    import app.config as cfg
    reload(cfg)

    mock_vc = _make_vault_client_mock(
        credentials={"inovar_username": "u", "inovar_password": "p"}
    )

    with patch("app.config.VaultClient", return_value=mock_vc) as MockVC:
        cfg.Settings()

    MockVC.assert_called_once_with(
        vault_addr="http://vault:8200",
        role_id="the-role-id",
        secret_id="the-secret-id",
    )


# ---------------------------------------------------------------------------
# Error propagation — Settings must NOT start with empty credentials
# ---------------------------------------------------------------------------

def test_settings_raises_vault_unavailable_when_vault_unreachable(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
    monkeypatch.setenv("VAULT_ROLE_ID", "role")
    monkeypatch.setenv("VAULT_SECRET_ID", "secret")

    from importlib import reload
    import app.config as cfg
    reload(cfg)
    from app.exceptions import VaultUnavailableError

    mock_vc = _make_vault_client_mock(login_raises=VaultUnavailableError())

    with patch("app.config.VaultClient", return_value=mock_vc):
        with pytest.raises(VaultUnavailableError):
            cfg.Settings()


def test_settings_raises_vault_auth_error_when_approle_rejected(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
    monkeypatch.setenv("VAULT_ROLE_ID", "bad-role")
    monkeypatch.setenv("VAULT_SECRET_ID", "bad-secret")

    from importlib import reload
    import app.config as cfg
    reload(cfg)
    from app.exceptions import VaultAuthError

    mock_vc = _make_vault_client_mock(login_raises=VaultAuthError())

    with patch("app.config.VaultClient", return_value=mock_vc):
        with pytest.raises(VaultAuthError):
            cfg.Settings()


def test_settings_raises_vault_secret_not_found_when_path_absent(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
    monkeypatch.setenv("VAULT_ROLE_ID", "role")
    monkeypatch.setenv("VAULT_SECRET_ID", "secret")

    from importlib import reload
    import app.config as cfg
    reload(cfg)
    from app.exceptions import VaultSecretNotFoundError

    mock_vc = _make_vault_client_mock(read_raises=VaultSecretNotFoundError())

    with patch("app.config.VaultClient", return_value=mock_vc):
        with pytest.raises(VaultSecretNotFoundError):
            cfg.Settings()
