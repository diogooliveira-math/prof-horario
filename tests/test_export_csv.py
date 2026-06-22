"""
Unit tests for GET /api/v1/horarios/export/csv

The endpoint must:
  - Return HTTP 200 with Content-Type: text/csv
  - Include a header row with the exact columns the PS1 script expects
  - Encode lesson_date as dd-mm-yyyy
  - Reconstruct the Inovar-style hour code: HH*100 + MM (e.g. 08:50 -> 850)
  - Set classroom to an empty string when the field is None
  - Produce an empty CSV (header-only) when no records exist
"""
import csv
import io
import pytest
from datetime import date, time, datetime
from uuid import uuid4

from httpx import AsyncClient, ASGITransport
from fastapi import status
from unittest.mock import AsyncMock, MagicMock, patch

from app.main import app
from app.database import get_db_session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CSV_COLUMNS = ["date", "class_name", "inovar_classroom", "hour", "fetched_at"]


def _make_horario(
    class_name="11B",
    classroom="AV-08",
    lesson_date=date(2026, 6, 23),
    start_time=time(8, 50),       # Inovar slot 800 → real 08:50
    end_time=time(9, 40),
    created_at=datetime(2026, 6, 20, 10, 0, 0),
):
    obj = MagicMock()
    obj.id          = uuid4()
    obj.class_name  = class_name
    obj.classroom   = classroom
    obj.lesson_date = lesson_date
    obj.start_time  = start_time
    obj.end_time    = end_time
    obj.created_at  = created_at
    return obj


def _parse_csv(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.routers.horario.HorarioRepository")
async def test_export_csv_returns_200_and_csv_content_type(mock_repo_cls):
    mock_repo = AsyncMock()
    mock_repo.get_all.return_value = []
    mock_repo_cls.return_value = mock_repo

    mock_db = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: mock_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/v1/horarios/export/csv")

    assert resp.status_code == status.HTTP_200_OK
    assert "text/csv" in resp.headers["content-type"]
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@patch("app.routers.horario.HorarioRepository")
async def test_export_csv_header_row_matches_ps1_schema(mock_repo_cls):
    """The CSV header must match exactly what sync-to-outlook.ps1 expects."""
    mock_repo = AsyncMock()
    mock_repo.get_all.return_value = []
    mock_repo_cls.return_value = mock_repo

    mock_db = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: mock_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/v1/horarios/export/csv")

    first_line = resp.text.splitlines()[0]
    assert first_line == ",".join(CSV_COLUMNS)
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@patch("app.routers.horario.HorarioRepository")
async def test_export_csv_empty_when_no_records(mock_repo_cls):
    """Header-only CSV (no data rows) when the table is empty."""
    mock_repo = AsyncMock()
    mock_repo.get_all.return_value = []
    mock_repo_cls.return_value = mock_repo

    mock_db = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: mock_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/v1/horarios/export/csv")

    rows = _parse_csv(resp.text)
    assert rows == []
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@patch("app.routers.horario.HorarioRepository")
async def test_export_csv_date_format_is_dd_mm_yyyy(mock_repo_cls):
    """lesson_date must be serialised as dd-mm-yyyy, not yyyy-mm-dd."""
    mock_repo = AsyncMock()
    mock_repo.get_all.return_value = [_make_horario(lesson_date=date(2026, 6, 23))]
    mock_repo_cls.return_value = mock_repo

    mock_db = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: mock_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/v1/horarios/export/csv")

    rows = _parse_csv(resp.text)
    assert rows[0]["date"] == "23-06-2026"
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@patch("app.routers.horario.HorarioRepository")
async def test_export_csv_hour_code_reconstructed_correctly(mock_repo_cls):
    """start_time is reconstructed as HH*100+MM for the PS1 script.

    time(8, 50)  -> 850    (Inovar slot 800 real start 08:50)
    time(10, 45) -> 1045   (Inovar slot 1000 real start 10:45)
    time(16, 15) -> 1615   (Inovar slot 1600 real start 16:15)
    """
    h1 = _make_horario(class_name="11B", start_time=time(8, 50))
    h2 = _make_horario(class_name="12H", start_time=time(10, 45))
    h3 = _make_horario(class_name="10T", start_time=time(16, 15))

    mock_repo = AsyncMock()
    mock_repo.get_all.return_value = [h1, h2, h3]
    mock_repo_cls.return_value = mock_repo

    mock_db = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: mock_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/v1/horarios/export/csv")

    rows = _parse_csv(resp.text)
    assert rows[0]["hour"] == "850"
    assert rows[1]["hour"] == "1045"
    assert rows[2]["hour"] == "1615"
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@patch("app.routers.horario.HorarioRepository")
async def test_export_csv_null_classroom_becomes_empty_string(mock_repo_cls):
    """classroom=None must not crash; it must appear as an empty CSV field."""
    mock_repo = AsyncMock()
    mock_repo.get_all.return_value = [_make_horario(classroom=None)]
    mock_repo_cls.return_value = mock_repo

    mock_db = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: mock_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/v1/horarios/export/csv")

    rows = _parse_csv(resp.text)
    assert rows[0]["inovar_classroom"] == ""
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@patch("app.routers.horario.HorarioRepository")
async def test_export_csv_all_columns_present_per_row(mock_repo_cls):
    """Every row must have all five columns the PS1 script reads."""
    mock_repo = AsyncMock()
    mock_repo.get_all.return_value = [_make_horario()]
    mock_repo_cls.return_value = mock_repo

    mock_db = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: mock_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/v1/horarios/export/csv")

    rows = _parse_csv(resp.text)
    assert len(rows) == 1
    for col in CSV_COLUMNS:
        assert col in rows[0], f"Missing column: {col}"
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@patch("app.routers.horario.HorarioRepository")
async def test_export_csv_content_disposition_header(mock_repo_cls):
    """Response must include Content-Disposition: attachment; filename=horario.csv."""
    mock_repo = AsyncMock()
    mock_repo.get_all.return_value = []
    mock_repo_cls.return_value = mock_repo

    mock_db = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: mock_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/v1/horarios/export/csv")

    assert "attachment" in resp.headers.get("content-disposition", "")
    assert "horario.csv" in resp.headers.get("content-disposition", "")
    app.dependency_overrides.clear()
