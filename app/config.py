"""
app/config.py

Application settings loaded from environment variables via pydantic-settings.

Usage in FastAPI handlers:
    from app.config import get_settings, Settings
    settings: Settings = Depends(get_settings)

Or outside request context:
    from app.config import get_settings
    s = get_settings()
    scraper = InovarScraperService(s.inovar_username, s.inovar_password.get_secret_value(), s.inovar_url)

inovar_password is a SecretStr so it never appears in logs, repr, or JSON
serialisation by accident.  Call .get_secret_value() only at the site where
you need the raw string (InovarScraperService constructor).
"""
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    inovar_username: str = ""
    inovar_password: SecretStr = SecretStr("")
    inovar_url: str = "https://epralima.inovarmais.com/alunos/Inicial.wgx"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
