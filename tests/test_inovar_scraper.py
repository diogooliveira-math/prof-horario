"""
Step 2 — RED phase: InovarScraperService.

Tests drive the creation of app/services/inovar_scraper.py.
All tests mock async_playwright — zero network access, zero Playwright
binaries required.

Contract under test:
    InovarScraperService(username, password, inovar_url)
        .scrape_week(week: Literal["current", "next"])
        -> dict[str, list[dict]]   (shape: extract_schedule_by_date output)
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers — build a minimal Playwright mock tree
# ---------------------------------------------------------------------------

def _make_pw_mock(html_content: str = "<html><table></table></html>") -> tuple:
    """Return (mock_playwright_ctx, mock_page) wired up for a successful run.

    The returned mock_playwright_ctx is used as the return value of
    async_playwright().__aenter__().
    """
    mock_page = AsyncMock()
    mock_page.content.return_value = html_content
    # wait_for_selector resolves without error by default (AsyncMock)

    mock_context = AsyncMock()
    mock_context.new_page.return_value = mock_page

    mock_browser = AsyncMock()
    mock_browser.new_context.return_value = mock_context

    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw = MagicMock()
    mock_pw.chromium = mock_chromium

    # async_playwright() is used as an async context manager
    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__.return_value = mock_pw
    mock_pw_cm.__aexit__.return_value = None

    return mock_pw_cm, mock_page


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_scraper_can_be_instantiated():
    from app.services.inovar_scraper import InovarScraperService
    svc = InovarScraperService(
        username="user",
        password="pass",
        inovar_url="https://example.com",
    )
    assert svc is not None


def test_scraper_stores_credentials():
    from app.services.inovar_scraper import InovarScraperService
    svc = InovarScraperService(username="prof", password="secret", inovar_url="https://x.com")
    assert svc.username == "prof"
    assert svc.password == "secret"


def test_scraper_rejects_invalid_week_literal():
    from app.services.inovar_scraper import InovarScraperService
    import pytest
    svc = InovarScraperService(username="u", password="p", inovar_url="https://x.com")
    with pytest.raises(ValueError, match="week"):
        import asyncio
        asyncio.get_event_loop().run_until_complete(svc.scrape_week("invalid"))


# ---------------------------------------------------------------------------
# Login sequence
# ---------------------------------------------------------------------------

_FAKE_SCHEDULE = {"20-06-2026": [{"class_name": "11B", "inovar_classroom": "MAT", "hour": 800}]}


@pytest.mark.asyncio
async def test_login_fills_username_field():
    from app.services.inovar_scraper import InovarScraperService

    mock_pw_cm, mock_page = _make_pw_mock()
    mock_page.content.return_value = "20-06-2026"

    with patch("app.services.inovar_scraper.async_playwright", return_value=mock_pw_cm):
        with patch("app.services.inovar_scraper.extract_schedule_by_date", return_value=_FAKE_SCHEDULE):
            svc = InovarScraperService("myuser", "mypass", "https://inovar.example.com")
            await svc.scrape_week("current")

    mock_page.fill.assert_any_call("#TRG_62", "myuser")


@pytest.mark.asyncio
async def test_login_fills_password_field():
    from app.services.inovar_scraper import InovarScraperService

    mock_pw_cm, mock_page = _make_pw_mock()
    mock_page.content.return_value = "20-06-2026"

    with patch("app.services.inovar_scraper.async_playwright", return_value=mock_pw_cm):
        with patch("app.services.inovar_scraper.extract_schedule_by_date", return_value=_FAKE_SCHEDULE):
            svc = InovarScraperService("myuser", "mypass", "https://inovar.example.com")
            await svc.scrape_week("current")

    mock_page.fill.assert_any_call("#TRG_61", "mypass")


@pytest.mark.asyncio
async def test_login_presses_enter_to_submit():
    from app.services.inovar_scraper import InovarScraperService

    mock_pw_cm, mock_page = _make_pw_mock()
    mock_page.content.return_value = "20-06-2026"

    with patch("app.services.inovar_scraper.async_playwright", return_value=mock_pw_cm):
        with patch("app.services.inovar_scraper.extract_schedule_by_date", return_value=_FAKE_SCHEDULE):
            svc = InovarScraperService("myuser", "mypass", "https://inovar.example.com")
            await svc.scrape_week("current")

    mock_page.press.assert_any_call("#TRG_61", "Enter")


@pytest.mark.asyncio
async def test_login_navigates_to_inovar_url():
    from app.services.inovar_scraper import InovarScraperService

    mock_pw_cm, mock_page = _make_pw_mock()
    mock_page.content.return_value = "20-06-2026"

    with patch("app.services.inovar_scraper.async_playwright", return_value=mock_pw_cm):
        with patch("app.services.inovar_scraper.extract_schedule_by_date", return_value=_FAKE_SCHEDULE):
            svc = InovarScraperService("myuser", "mypass", "https://epralima.inovarmais.com/alunos/Inicial.wgx")
            await svc.scrape_week("current")

    mock_page.goto.assert_called_once()
    args, _ = mock_page.goto.call_args
    assert "epralima.inovarmais.com" in args[0]


# ---------------------------------------------------------------------------
# Navigation to Sumários
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_navigates_to_area_docente():
    from app.services.inovar_scraper import InovarScraperService

    mock_pw_cm, mock_page = _make_pw_mock()
    mock_page.content.return_value = "20-06-2026"

    with patch("app.services.inovar_scraper.async_playwright", return_value=mock_pw_cm):
        with patch("app.services.inovar_scraper.extract_schedule_by_date", return_value=_FAKE_SCHEDULE):
            svc = InovarScraperService("u", "p", "https://x.com")
            await svc.scrape_week("current")

    all_clicks = [str(c) for c in mock_page.click.call_args_list]
    assert any("#VWG_116" in c for c in all_clicks)


@pytest.mark.asyncio
async def test_navigates_to_sumarios():
    from app.services.inovar_scraper import InovarScraperService

    mock_pw_cm, mock_page = _make_pw_mock()
    mock_page.content.return_value = "20-06-2026"

    with patch("app.services.inovar_scraper.async_playwright", return_value=mock_pw_cm):
        with patch("app.services.inovar_scraper.extract_schedule_by_date", return_value=_FAKE_SCHEDULE):
            svc = InovarScraperService("u", "p", "https://x.com")
            await svc.scrape_week("current")

    all_clicks = [str(c) for c in mock_page.click.call_args_list]
    assert any("#VWG_172" in c for c in all_clicks)


@pytest.mark.asyncio
async def test_next_week_clicks_semana_seguinte():
    from app.services.inovar_scraper import InovarScraperService

    mock_pw_cm, mock_page = _make_pw_mock()
    mock_page.content.return_value = "20-06-2026"

    with patch("app.services.inovar_scraper.async_playwright", return_value=mock_pw_cm):
        with patch("app.services.inovar_scraper.extract_schedule_by_date", return_value=_FAKE_SCHEDULE):
            svc = InovarScraperService("u", "p", "https://x.com")
            await svc.scrape_week("next")

    all_clicks = [str(c) for c in mock_page.click.call_args_list]
    assert any("#VWG_189" in c for c in all_clicks)


@pytest.mark.asyncio
async def test_current_week_does_not_click_semana_seguinte():
    from app.services.inovar_scraper import InovarScraperService

    mock_pw_cm, mock_page = _make_pw_mock()
    mock_page.content.return_value = "20-06-2026"

    with patch("app.services.inovar_scraper.async_playwright", return_value=mock_pw_cm):
        with patch("app.services.inovar_scraper.extract_schedule_by_date", return_value=_FAKE_SCHEDULE):
            svc = InovarScraperService("u", "p", "https://x.com")
            await svc.scrape_week("current")

    all_clicks = [str(c) for c in mock_page.click.call_args_list]
    assert not any("#VWG_189" in c for c in all_clicks)


# ---------------------------------------------------------------------------
# Return value
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_returns_dict_from_extract_schedule():
    from app.services.inovar_scraper import InovarScraperService

    expected = {"20-06-2026": [{"class_name": "11B", "inovar_classroom": "MAT", "hour": 800}]}

    mock_pw_cm, mock_page = _make_pw_mock()
    mock_page.content.return_value = "20-06-2026 some html"

    with patch("app.services.inovar_scraper.async_playwright", return_value=mock_pw_cm):
        with patch("app.services.inovar_scraper.extract_schedule_by_date", return_value=expected):
            svc = InovarScraperService("u", "p", "https://x.com")
            result = await svc.scrape_week("current")

    assert result == expected


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_raises_inovar_auth_error_when_login_form_not_found():
    from app.services.inovar_scraper import InovarScraperService
    from app.exceptions import InovarAuthError
    from playwright.async_api import TimeoutError as PlaywrightTimeout

    mock_pw_cm, mock_page = _make_pw_mock()
    # wait_for_selector raises on the login form selector
    mock_page.wait_for_selector.side_effect = PlaywrightTimeout("timeout")

    with patch("app.services.inovar_scraper.async_playwright", return_value=mock_pw_cm):
        svc = InovarScraperService("u", "p", "https://x.com")
        with pytest.raises(InovarAuthError):
            await svc.scrape_week("current")


@pytest.mark.asyncio
async def test_raises_inovar_navigation_error_when_date_guard_fails():
    from app.services.inovar_scraper import InovarScraperService
    from app.exceptions import InovarNavigationError

    mock_pw_cm, mock_page = _make_pw_mock()
    # page.content() returns HTML with no date pattern — guard fails
    mock_page.content.return_value = "<html>no dates here</html>"

    with patch("app.services.inovar_scraper.async_playwright", return_value=mock_pw_cm):
        svc = InovarScraperService("u", "p", "https://x.com")
        with pytest.raises(InovarNavigationError):
            await svc.scrape_week("current")


@pytest.mark.asyncio
async def test_raises_inovar_empty_schedule_when_extract_returns_empty():
    from app.services.inovar_scraper import InovarScraperService
    from app.exceptions import InovarEmptyScheduleError

    mock_pw_cm, mock_page = _make_pw_mock()
    mock_page.content.return_value = "20-06-2026"  # date guard passes

    with patch("app.services.inovar_scraper.async_playwright", return_value=mock_pw_cm):
        with patch("app.services.inovar_scraper.extract_schedule_by_date", return_value={}):
            svc = InovarScraperService("u", "p", "https://x.com")
            with pytest.raises(InovarEmptyScheduleError):
                await svc.scrape_week("current")
