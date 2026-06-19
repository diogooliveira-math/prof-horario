"""
app/services/vault_client.py

Thin wrapper around the hvac Python client for HashiCorp Vault.

Responsibilities
----------------
- AppRole login (role-id + secret-id -> short-lived token)
- KV v2 read for the one path this project uses:
    secret/data/inovar/credentials
- Translate hvac exceptions into domain exceptions so callers never
  import hvac directly and the exception hierarchy stays clean.

Security properties
-------------------
- The Vault token returned by login() is stored on the hvac client
  instance.  It is never stored as a plain str attribute on VaultClient,
  so it does not appear in repr() or str().
- The secret_id parameter is wrapped in SecretStr for the same reason.
- Nothing in this file logs credential values.

Usage
-----
    vc = VaultClient(
        vault_addr="http://vault:8200",
        role_id="xxxxxxxx-...",
        secret_id="yyyyyyyy-...",
    )
    vc.login()
    creds = vc.read_inovar_credentials()
    # creds = {"inovar_username": "...", "inovar_password": "..."}
"""
import hvac
import hvac.exceptions
from pydantic import SecretStr

from app.exceptions import VaultAuthError, VaultSecretNotFoundError, VaultUnavailableError


class VaultClient:
    """
    Vault access object scoped to a single AppRole identity.

    Attributes that must never appear in repr:
        _secret_id   (SecretStr)
        _client.token  (managed by hvac, not exposed directly)
    """

    def __init__(self, vault_addr: str, role_id: str, secret_id: str) -> None:
        self._vault_addr = vault_addr
        self._role_id = role_id
        self._secret_id = SecretStr(secret_id)
        self._client = hvac.Client(url=vault_addr)

    def __repr__(self) -> str:
        return f"VaultClient(vault_addr={self._vault_addr!r}, role_id={self._role_id!r})"

    def __str__(self) -> str:
        return self.__repr__()

    def login(self) -> None:
        """Authenticate via AppRole and store the resulting token on the client.

        Raises:
            VaultUnavailableError  — connection refused / network unreachable
            VaultAuthError         — Vault rejected the role-id / secret-id pair
        """
        try:
            response = self._client.auth.approle.login(
                role_id=self._role_id,
                secret_id=self._secret_id.get_secret_value(),
            )
            self._client.token = response["auth"]["client_token"]
        except (ConnectionError, OSError) as exc:
            raise VaultUnavailableError(
                f"Cannot reach Vault at {self._vault_addr}: {exc}"
            ) from exc
        except hvac.exceptions.Forbidden as exc:
            raise VaultAuthError(
                f"Vault AppRole rejected credentials for role '{self._role_id}': {exc}"
            ) from exc

    def read_inovar_credentials(self) -> dict[str, str]:
        """Read inovar/credentials from the KV v2 engine mounted at 'secret/'.

        Returns:
            {"inovar_username": str, "inovar_password": str}

        Raises:
            VaultSecretNotFoundError  — path does not exist
            VaultAuthError            — token has no read capability on this path
        """
        try:
            response = self._client.secrets.kv.v2.read_secret_version(
                path="inovar/credentials",
                mount_point="secret",
            )
            return response["data"]["data"]
        except hvac.exceptions.InvalidPath as exc:
            raise VaultSecretNotFoundError(
                "secret/data/inovar/credentials does not exist in Vault. "
                "Run 'vault kv put secret/inovar/credentials ...' to populate it."
            ) from exc
        except hvac.exceptions.Forbidden as exc:
            raise VaultAuthError(
                "The current Vault token has no read capability on "
                f"secret/data/inovar/credentials: {exc}"
            ) from exc
