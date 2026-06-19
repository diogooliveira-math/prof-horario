"""
tests/test_secret_discipline.py

RED phase — secret/credential discipline across the stack.

These tests enforce that secrets (Vault secret-id, Inovar password) are never
stored or transmitted as plain Python str objects after they enter the system.
The rule: a secret must travel as SecretStr and be unwrapped ONLY at the exact
call site that needs the raw bytes (page.fill, hvac login, etc.).

WHY this matters — a plain str:
  - appears in repr() and str(), leaking into log files and tracebacks
  - is stored in Python's object __dict__ and can be extracted by any code
    that holds a reference to the object
  - can be accidentally passed to a third-party library that logs arguments

SecretStr from pydantic:
  - repr() shows '**********' regardless of the actual value
  - .get_secret_value() is the explicit opt-in to unwrap
  - isinstance(x, str) is False — so grep/log scanners that look for str
    attributes cannot find it

CURRENT STATE (all tests below should be RED / FAILING):
  - Settings.vault_secret_id is plain str  → repr exposes it
  - InovarScraperService stores self.password as whatever is passed (str)
  - The router calls .get_secret_value() BEFORE passing to scraper,
    so the scraper always receives a plain str, not SecretStr

AFTER IMPLEMENTATION (all tests below should be GREEN):
  - Settings.vault_secret_id: SecretStr
  - InovarScraperService always converts/stores password as SecretStr
  - Router passes settings.inovar_password (SecretStr) directly to scraper
  - Scraper unwraps only inside _login() immediately before page.fill()

NOTE: implementing these changes will break one existing test:
  test_inovar_scraper.py::test_scraper_stores_credentials
  which asserts  svc.password == "secret"  (plain str equality).
  That test must be updated to  svc.password.get_secret_value() == "secret"
  as part of the GREEN implementation step.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import SecretStr


# ---------------------------------------------------------------------------
# Helpers — reuse the Playwright mock tree from test_inovar_scraper.py
# ---------------------------------------------------------------------------

_FAKE_SCHEDULE = {"20-06-2026": [{"class_name": "11B", "inovar_classroom": "MAT", "hour": 800}]}


def _make_pw_mock(html_content: str = "20-06-2026"):
    mock_page = AsyncMock()
    mock_page.content.return_value = html_content

    mock_context = AsyncMock()
    mock_context.new_page.return_value = mock_page

    mock_browser = AsyncMock()
    mock_browser.new_context.return_value = mock_context

    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw = MagicMock()
    mock_pw.chromium = mock_chromium

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__.return_value = mock_pw
    mock_pw_cm.__aexit__.return_value = None

    return mock_pw_cm, mock_page


# ===========================================================================
# Gap 2a — Settings.vault_secret_id must be SecretStr
# ===========================================================================

def test_vault_secret_id_is_secret_str_not_plain_str(monkeypatch):
    """
    Settings.vault_secret_id must be a SecretStr field.

    Currently the field is declared as plain str, so this test is RED.
    Fix: change the annotation to SecretStr in app/config.py.
    """
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    monkeypatch.delenv("VAULT_ROLE_ID", raising=False)
    monkeypatch.delenv("VAULT_SECRET_ID", raising=False)

    from importlib import reload
    import app.config as cfg
    reload(cfg)

    with patch("app.config.VaultClient"):
        s = cfg.Settings(vault_secret_id="my-secret-id")

    # Must be SecretStr, not a plain Python str
    assert isinstance(s.vault_secret_id, SecretStr), (
        f"Expected SecretStr but got {type(s.vault_secret_id).__name__}. "
        "vault_secret_id is an AppRole credential — it must be opaque."
    )


def test_vault_secret_id_not_exposed_in_settings_repr(monkeypatch):
    """
    The vault secret-id must not appear in repr(settings) or str(settings).

    Currently vault_secret_id is plain str, so pydantic includes it verbatim
    in the model repr — this test is RED.
    Fix: SecretStr fields are automatically masked as '**********' in repr.
    """
    monkeypatch.delenv("VAULT_ADDR", raising=False)

    from importlib import reload
    import app.config as cfg
    reload(cfg)

    with patch("app.config.VaultClient"):
        s = cfg.Settings(vault_secret_id="super-secret-approle-id")

    assert "super-secret-approle-id" not in repr(s), (
        "vault_secret_id appeared in repr(settings). "
        "Change the field to SecretStr so pydantic masks it."
    )
    assert "super-secret-approle-id" not in str(s)


def test_vault_client_called_with_unwrapped_secret_id(monkeypatch):
    """
    VaultClient must still receive the raw string (it wraps it internally).
    This guards against the fix breaking the call site in model_post_init.

    VaultClient.__init__ expects a plain str for secret_id because it wraps
    it itself. Settings must call .get_secret_value() before passing.
    """
    monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
    monkeypatch.setenv("VAULT_ROLE_ID", "role-id")
    monkeypatch.setenv("VAULT_SECRET_ID", "the-secret-id")

    from importlib import reload
    import app.config as cfg
    reload(cfg)

    captured = {}

    class SpyVaultClient:
        def __init__(self, vault_addr, role_id, secret_id):
            captured["secret_id_type"] = type(secret_id)
            captured["secret_id_value"] = secret_id

        def login(self):
            pass

        def read_inovar_credentials(self):
            return {"inovar_username": "u", "inovar_password": "p"}

    with patch("app.config.VaultClient", SpyVaultClient):
        cfg.Settings()

    # VaultClient must receive a plain str — it does its own SecretStr wrapping
    assert isinstance(captured["secret_id_value"], str), (
        "VaultClient expects a plain str for secret_id (it wraps internally). "
        "Settings.model_post_init must call vault_secret_id.get_secret_value()."
    )
    assert not isinstance(captured["secret_id_value"], SecretStr)
    assert captured["secret_id_value"] == "the-secret-id"


# ===========================================================================
# Gap 2b — InovarScraperService must store password as SecretStr
# ===========================================================================

def test_scraper_does_not_store_password_as_plain_str():
    """
    InovarScraperService must never store the password as a plain Python str.

    Currently self.password = password (whatever was passed), so when the
    router passes a plain str this test is RED.
    Fix: InovarScraperService.__init__ must convert to SecretStr internally.
    """
    from app.services.inovar_scraper import InovarScraperService

    svc = InovarScraperService(
        username="teacher",
        password="plaintext-pass",  # plain str — as the router currently passes
        inovar_url="https://x.com",
    )

    # The stored attribute must NOT be a plain Python str
    assert not isinstance(svc.password, str), (
        "svc.password is a plain str. "
        "InovarScraperService must convert it to SecretStr so it cannot "
        "accidentally appear in logs or repr."
    )


def test_scraper_stores_password_as_secret_str():
    """Positive version of the above — the stored type must be SecretStr."""
    from app.services.inovar_scraper import InovarScraperService

    svc = InovarScraperService(
        username="teacher",
        password="plaintext-pass",
        inovar_url="https://x.com",
    )

    assert isinstance(svc.password, SecretStr), (
        f"Expected SecretStr, got {type(svc.password).__name__}."
    )


def test_scraper_also_accepts_secret_str_directly():
    """
    When the router is fixed to pass SecretStr, the scraper must still work.
    It must accept SecretStr without double-wrapping.
    """
    from app.services.inovar_scraper import InovarScraperService

    svc = InovarScraperService(
        username="teacher",
        password=SecretStr("vault-sourced-pass"),
        inovar_url="https://x.com",
    )

    assert isinstance(svc.password, SecretStr)
    assert svc.password.get_secret_value() == "vault-sourced-pass"


def test_scraper_password_not_in_repr():
    """
    The plaintext password must not appear in repr(scraper) or str(scraper).

    If self.password is SecretStr, pydantic masks it. But the scraper also
    needs a __repr__ that does not accidentally expose it via vars(self).
    """
    from app.services.inovar_scraper import InovarScraperService

    svc = InovarScraperService("teacher", "h1ghly-s3cret", "https://x.com")

    assert "h1ghly-s3cret" not in repr(svc), (
        "Password appeared in repr(scraper). "
        "Store as SecretStr and add a __repr__ that omits it."
    )
    assert "h1ghly-s3cret" not in str(svc)


@pytest.mark.asyncio
async def test_scraper_page_fill_receives_unwrapped_plain_string():
    """
    page.fill('#TRG_61', ...) must receive the raw string — Playwright
    cannot accept a SecretStr object, only a plain str.

    This test verifies the unwrap happens at the RIGHT moment (inside _login,
    immediately before fill) and that the string passed is correct.

    This test must stay GREEN even during the intermediate implementation step
    where the scraper starts storing SecretStr — if it goes RED, it means
    the unwrap was forgotten.
    """
    from app.services.inovar_scraper import InovarScraperService

    mock_pw_cm, mock_page = _make_pw_mock()

    with patch("app.services.inovar_scraper.async_playwright", return_value=mock_pw_cm):
        with patch("app.services.inovar_scraper.extract_schedule_by_date", return_value=_FAKE_SCHEDULE):
            svc = InovarScraperService("teacher", "correct-password", "https://x.com")
            await svc.scrape_week("current")

    # Find the password fill call
    fill_calls = {args[0]: args[1] for args, _ in mock_page.fill.call_args_list}

    assert "#TRG_61" in fill_calls, "page.fill('#TRG_61', ...) was never called"

    password_sent = fill_calls["#TRG_61"]

    # Must be a plain str — Playwright does not know about SecretStr
    assert isinstance(password_sent, str), (
        f"page.fill received {type(password_sent).__name__}, expected str. "
        "Call .get_secret_value() inside _login() before page.fill()."
    )
    assert not isinstance(password_sent, SecretStr)

    # Must be the actual password value, not a masked representation
    assert password_sent == "correct-password", (
        f"page.fill received {password_sent!r} instead of 'correct-password'. "
        "Unwrap with .get_secret_value()."
    )


# ===========================================================================
# Gap 2b (router side) — router must pass SecretStr to scraper constructor
# ===========================================================================

@pytest.mark.asyncio
async def test_sync_router_passes_secret_str_to_scraper_not_plain_str():
    """
    The sync endpoint must pass settings.inovar_password (SecretStr) directly
    to InovarScraperService, NOT call .get_secret_value() first.

    Currently the router does:
        password=settings.inovar_password.get_secret_value()   <- plain str

    Target:
        password=settings.inovar_password                      <- SecretStr

    This is RED now because the router unwraps before passing.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from app.config import Settings

    captured = {}

    class SpyScraper:
        def __init__(self, username, password, inovar_url):
            captured["password_type"] = type(password)
            captured["password_value"] = password

        async def scrape_week(self, week):
            return _FAKE_SCHEDULE

    mock_settings = Settings.model_construct(
        inovar_username="u",
        inovar_password=SecretStr("vault-pass"),
        inovar_url="https://x.com",
        vault_addr="",
        vault_role_id="",
        vault_secret_id=SecretStr(""),
    )

    with patch("app.routers.horario.InovarScraperService", SpyScraper), \
         patch("app.routers.horario.get_settings", return_value=mock_settings), \
         patch("app.routers.horario.map_inovar_to_horarios", return_value=[]), \
         patch("app.routers.horario.HorarioRepository") as MockRepo:

        MockRepo.return_value.exists = AsyncMock(return_value=False)
        MockRepo.return_value.add = AsyncMock()

        with patch("app.database.get_db_session") as mock_db:
            mock_db.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            client = TestClient(app)
            client.post("/api/v1/horarios/sync?week=next")

    assert "password_type" in captured, "SpyScraper constructor was never called"
    assert captured["password_type"] is SecretStr, (
        f"Router passed {captured['password_type'].__name__} to InovarScraperService. "
        "Expected SecretStr — remove .get_secret_value() from the router call and "
        "let InovarScraperService own the unwrapping."
    )
