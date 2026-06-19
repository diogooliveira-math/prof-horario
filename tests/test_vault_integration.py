"""
tests/test_vault_integration.py

Integration tests against a real Vault server running in dev mode.

SKIPPED BY DEFAULT.  Only run when the environment variable
VAULT_INTEGRATION_TEST=1 is explicitly set.  Never runs in CI unless
that variable is configured as a repository secret/variable.

To run locally:

  1. Start Vault in dev mode on a dedicated port:

       docker run --rm -d --name vault-test \
         -p 8300:8200 \
         -e VAULT_DEV_ROOT_TOKEN_ID=root-test-token \
         hashicorp/vault:1.15.4 \
         server -dev -dev-listen-address=0.0.0.0:8200

  2. Export the required env vars:

       export VAULT_INTEGRATION_TEST=1
       export VAULT_TEST_ADDR=http://localhost:8300
       export VAULT_TEST_ROOT_TOKEN=root-test-token

  3. Run only the integration tests:

       ./venv/Scripts/python.exe -m pytest tests/test_vault_integration.py -v

  4. Stop the container:

       docker stop vault-test

Why dev mode?
  Vault dev mode starts already initialized, unsealed, with in-memory
  storage.  No unseal keys, no persistent state.  Ideal for ephemeral
  test fixtures.  Never use dev mode in production — it prints the root
  token to stdout and persists nothing across restarts.
"""
import os
import pytest

# ---------------------------------------------------------------------------
# Skip guard — all tests in this file are skipped unless explicitly opted in
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    os.environ.get("VAULT_INTEGRATION_TEST") != "1",
    reason="Set VAULT_INTEGRATION_TEST=1 to run Vault integration tests",
)

VAULT_ADDR = os.environ.get("VAULT_TEST_ADDR", "http://localhost:8300")
ROOT_TOKEN = os.environ.get("VAULT_TEST_ROOT_TOKEN", "root-test-token")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def vault_client_admin():
    """
    An hvac client with root access for fixture setup/teardown.
    Not the VaultClient wrapper — direct hvac access for admin operations.
    """
    import hvac
    client = hvac.Client(url=VAULT_ADDR, token=ROOT_TOKEN)
    assert client.is_authenticated(), "Root token is not valid — is Vault running?"

    # Ensure KV v2 is enabled at 'secret/'
    try:
        client.sys.enable_secrets_engine(backend_type="kv", path="secret", options={"version": "2"})
    except Exception:
        pass  # already enabled — dev mode enables it by default

    # Enable AppRole auth method
    try:
        client.sys.enable_auth_method(method_type="approle")
    except Exception:
        pass  # already enabled

    return client


@pytest.fixture(scope="module")
def inovar_policy(vault_client_admin):
    """Create the narrow read-only inovar policy."""
    policy_hcl = """
path "secret/data/inovar/*" {
  capabilities = ["read"]
}
"""
    vault_client_admin.sys.create_or_update_policy(
        name="inovar-policy",
        policy=policy_hcl,
    )
    return "inovar-policy"


@pytest.fixture(scope="module")
def approle_credentials(vault_client_admin, inovar_policy):
    """Create an AppRole role and return (role_id, secret_id)."""
    vault_client_admin.auth.approle.create_or_update_approle(
        role_name="inovar-test-role",
        token_policies=[inovar_policy],
        secret_id_ttl="1h",
        token_ttl="5m",
        token_max_ttl="10m",
    )
    role_id = vault_client_admin.auth.approle.read_role_id("inovar-test-role")
    role_id = role_id["data"]["role_id"]

    secret_id = vault_client_admin.auth.approle.generate_secret_id("inovar-test-role")
    secret_id = secret_id["data"]["secret_id"]

    return role_id, secret_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_vault_devmode_is_reachable():
    """Sanity check: Vault is up and answering requests."""
    import hvac
    client = hvac.Client(url=VAULT_ADDR, token=ROOT_TOKEN)
    assert client.is_authenticated()


def test_write_and_read_inovar_credentials(vault_client_admin):
    """Write a secret via admin client, read it back via VaultClient wrapper."""
    from app.services.vault_client import VaultClient

    vault_client_admin.secrets.kv.v2.create_or_update_secret(
        path="inovar/credentials",
        mount_point="secret",
        secret={
            "inovar_username": "integration_user",
            "inovar_password": "integration_pass",
        },
    )

    vc = VaultClient(
        vault_addr=VAULT_ADDR,
        role_id=ROOT_TOKEN,   # dev mode accepts root token as a login token directly
        secret_id="",
    )
    # In dev mode login via token directly
    import hvac
    raw = hvac.Client(url=VAULT_ADDR, token=ROOT_TOKEN)
    vc._client = raw  # bypass AppRole for this test — we just want to verify the read path

    result = vc.read_inovar_credentials()
    assert result["inovar_username"] == "integration_user"
    assert result["inovar_password"] == "integration_pass"


def test_approle_login_succeeds_with_valid_credentials(approle_credentials):
    """VaultClient.login() must succeed with a valid role-id and secret-id."""
    from app.services.vault_client import VaultClient

    role_id, secret_id = approle_credentials
    vc = VaultClient(vault_addr=VAULT_ADDR, role_id=role_id, secret_id=secret_id)
    vc.login()  # must not raise


def test_approle_can_read_inovar_credentials(vault_client_admin, approle_credentials):
    """A token issued by AppRole with inovar-policy must be able to read the secret."""
    from app.services.vault_client import VaultClient

    # Ensure secret exists
    vault_client_admin.secrets.kv.v2.create_or_update_secret(
        path="inovar/credentials",
        mount_point="secret",
        secret={"inovar_username": "approle_user", "inovar_password": "approle_pass"},
    )

    role_id, secret_id = approle_credentials
    vc = VaultClient(vault_addr=VAULT_ADDR, role_id=role_id, secret_id=secret_id)
    vc.login()

    result = vc.read_inovar_credentials()
    assert result["inovar_username"] == "approle_user"
    assert result["inovar_password"] == "approle_pass"


def test_policy_prevents_write_from_approle_token(vault_client_admin, approle_credentials):
    """
    A token scoped to inovar-policy (read-only) must not be able to write.
    This verifies that the policy is tight — the app cannot overwrite its
    own credentials even if compromised.
    """
    from app.services.vault_client import VaultClient
    from app.exceptions import VaultAuthError
    import hvac

    role_id, secret_id = approle_credentials
    vc = VaultClient(vault_addr=VAULT_ADDR, role_id=role_id, secret_id=secret_id)
    vc.login()

    # Attempt a write using the app's scoped token — must be denied
    with pytest.raises((VaultAuthError, hvac.exceptions.Forbidden)):
        vc._client.secrets.kv.v2.create_or_update_secret(
            path="inovar/credentials",
            mount_point="secret",
            secret={"inovar_username": "hacked", "inovar_password": "hacked"},
        )


def test_settings_reads_live_credentials_from_vault(vault_client_admin, approle_credentials, monkeypatch):
    """
    End-to-end: Settings() with VAULT_* env vars reads real credentials from
    a live dev-mode Vault, not from env vars.
    """
    vault_client_admin.secrets.kv.v2.create_or_update_secret(
        path="inovar/credentials",
        mount_point="secret",
        secret={"inovar_username": "live_user", "inovar_password": "live_pass"},
    )

    role_id, secret_id = approle_credentials
    monkeypatch.setenv("VAULT_ADDR", VAULT_ADDR)
    monkeypatch.setenv("VAULT_ROLE_ID", role_id)
    monkeypatch.setenv("VAULT_SECRET_ID", secret_id)
    monkeypatch.setenv("INOVAR_USERNAME", "env_ignored")
    monkeypatch.setenv("INOVAR_PASSWORD", "env_ignored")

    from importlib import reload
    import app.config as cfg
    reload(cfg)

    s = cfg.Settings()
    assert s.inovar_username == "live_user"
    assert s.inovar_password.get_secret_value() == "live_pass"
