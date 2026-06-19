"""
tests/test_vault_client.py

Unit tests for app/services/vault_client.py — Step 0, RED phase.

All tests mock hvac.Client entirely.  No Docker, no network, no Vault
binary required.  These run in CI unconditionally.

Naming convention:
    test_<what behaviour>_<when condition>
"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hvac_mock(
    login_token="s.test-token",
    secret_data=None,
    login_raises=None,
    read_raises=None,
    write_raises=None,
):
    """Return a mock hvac.Client wired for the common happy/sad paths."""
    client = MagicMock()

    # auth.approle.login — returns a token response dict
    if login_raises:
        client.auth.approle.login.side_effect = login_raises
    else:
        client.auth.approle.login.return_value = {
            "auth": {"client_token": login_token}
        }

    # secrets.kv.v2.read_secret_version — returns versioned KV dict
    if read_raises:
        client.secrets.kv.v2.read_secret_version.side_effect = read_raises
    elif secret_data is not None:
        client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": secret_data}
        }

    # write raises
    if write_raises:
        client.secrets.kv.v2.create_or_update_secret.side_effect = write_raises

    return client


# ---------------------------------------------------------------------------
# AppRole login
# ---------------------------------------------------------------------------

def test_login_calls_approle_with_correct_arguments():
    from app.services.vault_client import VaultClient

    mock_hvac = _make_hvac_mock()
    with patch("app.services.vault_client.hvac.Client", return_value=mock_hvac):
        vc = VaultClient(
            vault_addr="http://vault:8200",
            role_id="my-role-id",
            secret_id="my-secret-id",
        )
        vc.login()

    mock_hvac.auth.approle.login.assert_called_once_with(
        role_id="my-role-id",
        secret_id="my-secret-id",
    )


def test_login_sets_token_on_client():
    from app.services.vault_client import VaultClient

    mock_hvac = _make_hvac_mock(login_token="s.abc123")
    with patch("app.services.vault_client.hvac.Client", return_value=mock_hvac):
        vc = VaultClient("http://vault:8200", "role", "secret")
        vc.login()

    # The client token must be applied so subsequent calls are authenticated
    assert mock_hvac.token == "s.abc123"


def test_login_raises_vault_auth_error_on_forbidden():
    from app.services.vault_client import VaultClient
    from app.exceptions import VaultAuthError
    import hvac.exceptions

    mock_hvac = _make_hvac_mock(login_raises=hvac.exceptions.Forbidden("denied"))
    with patch("app.services.vault_client.hvac.Client", return_value=mock_hvac):
        vc = VaultClient("http://vault:8200", "bad-role", "bad-secret")
        with pytest.raises(VaultAuthError):
            vc.login()


def test_login_raises_vault_unavailable_on_connection_error():
    from app.services.vault_client import VaultClient
    from app.exceptions import VaultUnavailableError

    mock_hvac = _make_hvac_mock(login_raises=ConnectionError("refused"))
    with patch("app.services.vault_client.hvac.Client", return_value=mock_hvac):
        vc = VaultClient("http://vault:8200", "role", "secret")
        with pytest.raises(VaultUnavailableError):
            vc.login()


# ---------------------------------------------------------------------------
# KV secret read
# ---------------------------------------------------------------------------

def test_read_inovar_credentials_returns_username_and_password():
    from app.services.vault_client import VaultClient

    mock_hvac = _make_hvac_mock(
        secret_data={
            "inovar_username": "prof_user",
            "inovar_password": "hunter2",
        }
    )
    with patch("app.services.vault_client.hvac.Client", return_value=mock_hvac):
        vc = VaultClient("http://vault:8200", "role", "secret")
        vc.login()
        result = vc.read_inovar_credentials()

    assert result["inovar_username"] == "prof_user"
    assert result["inovar_password"] == "hunter2"


def test_read_inovar_credentials_calls_correct_path():
    from app.services.vault_client import VaultClient

    mock_hvac = _make_hvac_mock(
        secret_data={"inovar_username": "u", "inovar_password": "p"}
    )
    with patch("app.services.vault_client.hvac.Client", return_value=mock_hvac):
        vc = VaultClient("http://vault:8200", "role", "secret")
        vc.login()
        vc.read_inovar_credentials()

    mock_hvac.secrets.kv.v2.read_secret_version.assert_called_once_with(
        path="inovar/credentials",
        mount_point="secret",
    )


def test_read_raises_vault_secret_not_found_when_path_missing():
    from app.services.vault_client import VaultClient
    from app.exceptions import VaultSecretNotFoundError
    import hvac.exceptions

    mock_hvac = _make_hvac_mock(
        read_raises=hvac.exceptions.InvalidPath("no such path")
    )
    with patch("app.services.vault_client.hvac.Client", return_value=mock_hvac):
        vc = VaultClient("http://vault:8200", "role", "secret")
        vc.login()
        with pytest.raises(VaultSecretNotFoundError):
            vc.read_inovar_credentials()


def test_read_raises_vault_auth_error_on_forbidden_read():
    from app.services.vault_client import VaultClient
    from app.exceptions import VaultAuthError
    import hvac.exceptions

    mock_hvac = _make_hvac_mock(
        read_raises=hvac.exceptions.Forbidden("token has no read capability")
    )
    with patch("app.services.vault_client.hvac.Client", return_value=mock_hvac):
        vc = VaultClient("http://vault:8200", "role", "secret")
        vc.login()
        with pytest.raises(VaultAuthError):
            vc.read_inovar_credentials()


# ---------------------------------------------------------------------------
# Secrets must not appear in repr
# ---------------------------------------------------------------------------

def test_token_does_not_appear_in_repr():
    from app.services.vault_client import VaultClient

    mock_hvac = _make_hvac_mock(login_token="s.supersecret-token")
    with patch("app.services.vault_client.hvac.Client", return_value=mock_hvac):
        vc = VaultClient("http://vault:8200", "role", "secret")
        vc.login()

    assert "supersecret-token" not in repr(vc)
    assert "supersecret-token" not in str(vc)


def test_secret_id_does_not_appear_in_repr():
    from app.services.vault_client import VaultClient

    with patch("app.services.vault_client.hvac.Client", return_value=_make_hvac_mock()):
        vc = VaultClient("http://vault:8200", "role", "plaintext-secret-id")

    assert "plaintext-secret-id" not in repr(vc)
    assert "plaintext-secret-id" not in str(vc)
