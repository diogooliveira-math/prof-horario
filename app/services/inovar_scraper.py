"""
app/services/inovar_scraper.py

Async Playwright-based scraper for the Inovar teacher schedule portal.

Architecture
------------
- One class: InovarScraperService
- One public async method: scrape_week(week: Literal["current", "next"])
- Per-request browser lifecycle — a new Chromium instance is launched for
  each call and closed when the async context manager exits.  This keeps the
  implementation simple and testable; the trade-off (browser cold-start per
  call) is acceptable because sync is an infrequent operation (once per day).
- extract_schedule_by_date is imported at module level so tests can patch it
  cleanly without any filesystem or WSL dependency.

Selectors
---------
All primary selectors have a title/text fallback because Gizmox WebGUI assigns
dynamic IDs that can drift between server updates.  The pattern used in the
legacy TypeScript layer (browser_management.ts) is replicated here.

Error taxonomy
--------------
  InovarAuthError          — login form never appeared / credentials rejected
  InovarNavigationError    — Sumários page did not load / date guard failed /
                             week label did not change after "Semana Seguinte"
  InovarEmptyScheduleError — page loaded correctly but no events in the week
"""
from __future__ import annotations

import re
import logging
from typing import Any, Literal, Union

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from pydantic import SecretStr

from app.exceptions import (
    InovarAuthError,
    InovarNavigationError,
    InovarEmptyScheduleError,
)
from app.services.inovar_html import extract_schedule_by_date

logger = logging.getLogger(__name__)

# Inovar date pattern — used as a guard to confirm the schedule table loaded.
_DATE_PATTERN = re.compile(r"\d{2}-\d{2}-\d{4}")

# Navigation timeouts (ms).  Gizmox WebGUI is slow — the framework bootstraps
# a full UI from a ~2 KB shell after networkidle.
_NAV_TIMEOUT   = 60_000   # page.goto / networkidle
_CLICK_TIMEOUT = 30_000   # individual element waits

WeekLiteral = Literal["current", "next"]


class InovarScraperService:
    """Headless Playwright scraper for the Inovar teacher schedule portal.

    Args:
        username:   Value of INOVAR_USERNAME env var.
        password:   Value of INOVAR_PASSWORD env var.
        inovar_url: Base URL of the Inovar login page.
    """

    def __init__(self, username: str, password: Union[str, SecretStr], inovar_url: str) -> None:
        self.username   = username
        self.password   = password if isinstance(password, SecretStr) else SecretStr(password)
        self.inovar_url = inovar_url

    def __repr__(self) -> str:
        return f"InovarScraperService(username={self.username!r}, inovar_url={self.inovar_url!r})"

    def __str__(self) -> str:
        return self.__repr__()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scrape_week(self, week: WeekLiteral) -> dict[str, list[dict[str, Any]]]:
        """Scrape the Inovar schedule for *week* and return a structured dict.

        Args:
            week: "current" for the current week, "next" for the next week.

        Returns:
            dict keyed by "dd-mm-yyyy" with lists of class-slot dicts —
            the same shape extract_schedule_by_date() produces.

        Raises:
            ValueError:              week is not "current" or "next".
            InovarAuthError:         Login form did not appear or creds rejected.
            InovarNavigationError:   Sumários page failed to load, or date guard
                                     failed, or week navigation stalled.
            InovarEmptyScheduleError: Page loaded correctly but zero events found.
        """
        if week not in ("current", "next"):
            raise ValueError(
                f"week must be 'current' or 'next', got {week!r}"
            )

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                page    = await context.new_page()

                await self._login(page)
                await self._navigate_to_sumarios(page)

                if week == "next":
                    await self._navigate_to_next_week(page)

                html = await self._capture_html(page)

            finally:
                await browser.close()

        schedule = extract_schedule_by_date(html)

        if not schedule:
            logger.warning("Inovar returned empty schedule for week=%s", week)
            raise InovarEmptyScheduleError()

        return schedule

    # ------------------------------------------------------------------
    # Private steps
    # ------------------------------------------------------------------

    async def _login(self, page) -> None:
        """Navigate to Inovar and complete the login form."""
        logger.info("Navigating to Inovar: %s", self.inovar_url)
        await page.goto(self.inovar_url, wait_until="networkidle", timeout=_NAV_TIMEOUT)

        # Wait for the login form — its absence means we cannot proceed.
        try:
            await page.wait_for_selector("#TRG_62", timeout=_CLICK_TIMEOUT)
        except PlaywrightTimeout:
            raise InovarAuthError(
                "Login form did not appear within the timeout — "
                "Inovar may be unreachable or its layout has changed."
            )

        await page.fill("#TRG_62", self.username)
        await page.fill("#TRG_61", self.password.get_secret_value())
        await page.press("#TRG_61", "Enter")

        # Wait for the Gizmox main UI to bootstrap after login.
        try:
            await page.wait_for_selector('div[id^="VWG_"]', timeout=_NAV_TIMEOUT)
        except PlaywrightTimeout:
            raise InovarAuthError(
                "Main Inovar UI did not load after login — "
                "credentials may be incorrect."
            )

        logger.info("Login successful")

    async def _navigate_to_sumarios(self, page) -> None:
        """Click Área Docente → Sumários."""
        logger.info("Navigating to Área Docente")
        try:
            await page.click("#VWG_116", timeout=_CLICK_TIMEOUT)
        except PlaywrightTimeout:
            await page.click(
                '[data-vwgtype="control"].cda3 >> text="Área Docente"',
                timeout=_CLICK_TIMEOUT,
            )

        logger.info("Navigating to Sumários")
        try:
            await page.click("#VWG_172", timeout=_CLICK_TIMEOUT)
        except PlaywrightTimeout:
            await page.click(
                '[data-vwgtype="control"].cda3 >> text="Sumários"',
                timeout=_CLICK_TIMEOUT,
            )

    async def _navigate_to_next_week(self, page) -> None:
        """Click 'Semana Seguinte' and confirm the week label changed."""
        logger.info("Navigating to next week")
        try:
            await page.click("#VWG_189", timeout=_CLICK_TIMEOUT)
        except PlaywrightTimeout:
            try:
                await page.click('[title="Semana Seguinte"]', timeout=_CLICK_TIMEOUT)
            except PlaywrightTimeout:
                raise InovarNavigationError(
                    "Could not click 'Semana Seguinte' — "
                    "the next-week navigation button was not found."
                )

    async def _capture_html(self, page) -> str:
        """Capture full page HTML and verify the date guard passes."""
        html = await page.content()

        if not _DATE_PATTERN.search(html):
            raise InovarNavigationError(
                "Schedule page loaded but contains no date cells "
                r"(expected pattern \d{2}-\d{2}-\d{4}) — "
                "the Inovar schedule table may not have rendered."
            )

        logger.info(
            "HTML captured (%d chars), date guard passed", len(html)
        )
        return html
