"""
app/config.py

Application settings loaded from environment variables via pydantic-settings,
with optional credential injection from HashiCorp Vault.

Behaviour
---------
  Without Vault (VAULT_ADDR not set):
    Reads INOVAR_USERNAME and INOVAR_PASSWORD from the environment or
    .env file exactly as before.  Zero behaviour change for local
    development without a Vault server.

  With Vault (VAULT_ADDR + VAULT_ROLE_ID + VAULT_SECRET_ID all set):
    After pydantic-settings populates the model from the environment,
    model_post_init authenticates to Vault via AppRole and overwrites
    inovar_username and inovar_password with values read from
    secret/data/inovar/credentials.
    The INOVAR_USERNAME / INOVAR_PASSWORD env vars are ignored when
    Vault is active (they may be absent entirely).

Error handling
--------------
  If Vault is configured but unreachable, Settings() raises
  VaultUnavailableError.  The application must not start with empty
  credentials — an empty string to InovarScraperService would silently
  produce an auth failure hours later at scrape time.

Secret discipline
-----------------
  inovar_password is SecretStr.  Call .get_secret_value() only at the
  call site that needs the raw string (InovarScraperService constructor).

Usage
-----
  from app.config import get_settings, Settings

  # In FastAPI handlers:
  settings: Settings = Depends(get_settings)

  # Outside request context:
  s = get_settings()
"""
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings

from app.services.vault_client import VaultClient


class Settings(BaseSettings):
    # Inovar credentials — sourced from env or Vault (see module docstring)
    inovar_username: str = ""
    inovar_password: SecretStr = SecretStr("")
    inovar_url: str = "https://epralima.inovarmais.com/alunos/Inicial.wgx"

    # Vault connection — all three must be set for Vault to activate.
    # If any are absent, the env-only path is used.
    vault_addr: str = ""
    vault_role_id: str = ""
    vault_secret_id: SecretStr = SecretStr("")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    def model_post_init(self, __context) -> None:
        """Overwrite Inovar credentials from Vault when configured."""
        if not (self.vault_addr and self.vault_role_id and self.vault_secret_id.get_secret_value()):
            return  # env-only path — nothing to do

        vc = VaultClient(
            vault_addr=self.vault_addr,
            role_id=self.vault_role_id,
            secret_id=self.vault_secret_id.get_secret_value(),
        )
        vc.login()  # raises VaultUnavailableError or VaultAuthError on failure
        creds = vc.read_inovar_credentials()  # raises VaultSecretNotFoundError if absent

        # Override — object is not yet frozen at this point
        object.__setattr__(self, "inovar_username", creds["inovar_username"])
        object.__setattr__(self, "inovar_password", SecretStr(creds["inovar_password"]))


@lru_cache
def get_settings() -> Settings:
    return Settings()
