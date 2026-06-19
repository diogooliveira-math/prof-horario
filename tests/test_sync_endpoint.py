"""
Step 3 — RED phase: POST /api/v1/horarios/sync endpoint.

Drives the creation of the sync route in app/routers/horario.py.

Strategy — same as existing test_horario.py:
  - patch HorarioRepository at the router import level
  - patch InovarScraperService at the router import level
  - override get_db_session via app.dependency_overrides
  - drive get_settings via app.dependency_overrides

The endpoint under test:
  POST /api/v1/horarios/sync?week=current|next
  → 200 {"inserted": N, "skipped": N, "errors": N}

Error propagation (Inovar exceptions bubble via polymorphic handler):
  InovarAuthError         → 401
  InovarNavigationError   → 502
  InovarEmptyScheduleError→ 200 with success=False

Dedupe logic: if repo.exists() is True for a slot, it is counted as
"skipped" and no add() call is made for that slot.
"""
import pytest
from datetime import date, time
from httpx import AsyncClient, ASGITransport
from fastapi import status
from unittest.mock import AsyncMock, MagicMock, patch, call

from app.main import app
from app.database import get_db_session


# ---------------------------------------------------------------------------
# Fixtures & shared helpers
# ---------------------------------------------------------------------------

def _mock_db():
    db = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: db
    return db


def _fake_schedule():
    """Two-slot schedule across two dates — drives inserted/skipped counting."""
    return {
        "23-06-2026": [
            {"class_name": "11B", "inovar_classroom": "AV-09", "hour": 800},
            {"class_name": "12-H12", "inovar_classroom": "PB-21", "hour": 1000},
        ],
        "24-06-2026": [
            {"class_name": "10-S12", "inovar_classroom": "AV-08", "hour": 900},
        ],
    }


def _clear():
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Happy path — all slots new (inserted=3, skipped=0)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.routers.horario.InovarScraperService")
@patch("app.routers.horario.HorarioRepository")
async def test_sync_returns_200_with_summary(mock_repo_cls, mock_scraper_cls):
    _mock_db()

    mock_repo = AsyncMock()
    mock_repo.exists.return_value = False
    mock_repo_cls.return_value = mock_repo

    mock_scraper = AsyncMock()
    mock_scraper.scrape_week.return_value = _fake_schedule()
    mock_scraper_cls.return_value = mock_scraper

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/horarios/sync?week=next")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["inserted"] == 3
    assert body["skipped"] == 0
    assert body["errors"] == 0
    _clear()


@pytest.mark.asyncio
@patch("app.routers.horario.InovarScraperService")
@patch("app.routers.horario.HorarioRepository")
async def test_sync_calls_scrape_week_with_correct_param(mock_repo_cls, mock_scraper_cls):
    _mock_db()

    mock_repo = AsyncMock()
    mock_repo.exists.return_value = False
    mock_repo_cls.return_value = mock_repo

    mock_scraper = AsyncMock()
    mock_scraper.scrape_week.return_value = _fake_schedule()
    mock_scraper_cls.return_value = mock_scraper

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/horarios/sync?week=current")

    mock_scraper.scrape_week.assert_called_once_with("current")
    _clear()


# ---------------------------------------------------------------------------
# Dedupe — one slot already exists (inserted=2, skipped=1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.routers.horario.InovarScraperService")
@patch("app.routers.horario.HorarioRepository")
async def test_sync_skips_duplicate_slots(mock_repo_cls, mock_scraper_cls):
    _mock_db()

    # First exists() call returns True (duplicate), remaining return False
    mock_repo = AsyncMock()
    mock_repo.exists.side_effect = [True, False, False]
    mock_repo_cls.return_value = mock_repo

    mock_scraper = AsyncMock()
    mock_scraper.scrape_week.return_value = _fake_schedule()
    mock_scraper_cls.return_value = mock_scraper

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/horarios/sync?week=current")

    body = response.json()
    assert body["inserted"] == 2
    assert body["skipped"] == 1
    assert body["errors"] == 0
    _clear()


@pytest.mark.asyncio
@patch("app.routers.horario.InovarScraperService")
@patch("app.routers.horario.HorarioRepository")
async def test_sync_does_not_add_duplicate_slots(mock_repo_cls, mock_scraper_cls):
    _mock_db()

    mock_repo = AsyncMock()
    mock_repo.exists.side_effect = [True, False, False]
    mock_repo_cls.return_value = mock_repo

    mock_scraper = AsyncMock()
    mock_scraper.scrape_week.return_value = _fake_schedule()
    mock_scraper_cls.return_value = mock_scraper

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/horarios/sync?week=current")

    # add() must only be called for the 2 non-duplicate slots
    assert mock_repo.add.call_count == 2
    _clear()


# ---------------------------------------------------------------------------
# Default week param — "next" when omitted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.routers.horario.InovarScraperService")
@patch("app.routers.horario.HorarioRepository")
async def test_sync_defaults_to_next_week_when_param_omitted(mock_repo_cls, mock_scraper_cls):
    _mock_db()

    mock_repo = AsyncMock()
    mock_repo.exists.return_value = False
    mock_repo_cls.return_value = mock_repo

    mock_scraper = AsyncMock()
    mock_scraper.scrape_week.return_value = _fake_schedule()
    mock_scraper_cls.return_value = mock_scraper

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/horarios/sync")

    mock_scraper.scrape_week.assert_called_once_with("next")
    _clear()


# ---------------------------------------------------------------------------
# DB commit called once after all inserts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.routers.horario.InovarScraperService")
@patch("app.routers.horario.HorarioRepository")
async def test_sync_commits_once(mock_repo_cls, mock_scraper_cls):
    mock_db = _mock_db()

    mock_repo = AsyncMock()
    mock_repo.exists.return_value = False
    mock_repo_cls.return_value = mock_repo

    mock_scraper = AsyncMock()
    mock_scraper.scrape_week.return_value = _fake_schedule()
    mock_scraper_cls.return_value = mock_scraper

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/horarios/sync?week=current")

    mock_db.commit.assert_called_once()
    _clear()


# ---------------------------------------------------------------------------
# Error propagation — Inovar exceptions become HTTP errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.routers.horario.InovarScraperService")
@patch("app.routers.horario.HorarioRepository")
async def test_sync_returns_401_on_auth_error(mock_repo_cls, mock_scraper_cls):
    from app.exceptions import InovarAuthError
    _mock_db()

    mock_repo_cls.return_value = AsyncMock()
    mock_scraper = AsyncMock()
    mock_scraper.scrape_week.side_effect = InovarAuthError()
    mock_scraper_cls.return_value = mock_scraper

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/horarios/sync?week=current")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["error"]["code"] == "INOVAR_AUTH_ERROR"
    _clear()


@pytest.mark.asyncio
@patch("app.routers.horario.InovarScraperService")
@patch("app.routers.horario.HorarioRepository")
async def test_sync_returns_502_on_navigation_error(mock_repo_cls, mock_scraper_cls):
    from app.exceptions import InovarNavigationError
    _mock_db()

    mock_repo_cls.return_value = AsyncMock()
    mock_scraper = AsyncMock()
    mock_scraper.scrape_week.side_effect = InovarNavigationError()
    mock_scraper_cls.return_value = mock_scraper

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/horarios/sync?week=current")

    assert response.status_code == status.HTTP_502_BAD_GATEWAY
    assert response.json()["error"]["code"] == "INOVAR_NAVIGATION_ERROR"
    _clear()


@pytest.mark.asyncio
@patch("app.routers.horario.InovarScraperService")
@patch("app.routers.horario.HorarioRepository")
async def test_sync_returns_200_with_error_flag_on_empty_schedule(mock_repo_cls, mock_scraper_cls):
    """InovarEmptyScheduleError has status_code=200 — holiday/exam week is valid."""
    from app.exceptions import InovarEmptyScheduleError
    _mock_db()

    mock_repo_cls.return_value = AsyncMock()
    mock_scraper = AsyncMock()
    mock_scraper.scrape_week.side_effect = InovarEmptyScheduleError()
    mock_scraper_cls.return_value = mock_scraper

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/horarios/sync?week=next")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INOVAR_EMPTY_SCHEDULE"
    _clear()


# ---------------------------------------------------------------------------
# Invalid week param — 422 Unprocessable Entity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_returns_422_for_invalid_week_param():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/horarios/sync?week=yesterday")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
