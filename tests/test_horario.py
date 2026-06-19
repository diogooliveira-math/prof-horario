import pytest
from datetime import date, time, datetime
from uuid import uuid4
from httpx import AsyncClient, ASGITransport
from fastapi import status
from unittest.mock import AsyncMock, MagicMock, patch

from app.main import app
from app.database import get_db_session

# Dummy schema payload to match our router's expectations
@pytest.fixture
def valid_horario_payload():
    return {
        "class_name": "12B",
        "classroom": "Sala 102",
        "module_ref": "A3_Derivadas",
        "description": "Introducao as derivadas",
        "lesson_date": "2026-06-20",
        "start_time": "09:00",
        "end_time": "10:30"
    }

@pytest.mark.asyncio
@patch("app.routers.horario.HorarioRepository")
async def test_create_horario_success(mock_repo_class, valid_horario_payload):
    """
    TDD Test: Verifies that a valid payload yields a HTTP 201 Created status
    and successfully invokes the repository persistence layer.
    """
    # 1. Arrange: Setup the mock repository instance
    mock_repo_instance = AsyncMock()
    mock_repo_instance.exists.return_value = False  # No duplicates exist
    mock_repo_class.return_value = mock_repo_instance

    # Mock the database session dependency bypass
    mock_db_session = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: mock_db_session

    # 2. Act: Dispatch request using HTTPX AsyncClient against our real FastAPI router
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/horarios", json=valid_horario_payload)

    # 3. Assert: Verify behaviors
    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["status"] == "success"
    assert "id" in response.json()
    
    mock_repo_instance.exists.assert_called_once()
    mock_repo_instance.add.assert_called_once()
    mock_db_session.commit.assert_called_once()

    # Clean up overrides
    app.dependency_overrides.clear()

@pytest.mark.asyncio
@patch("app.routers.horario.HorarioRepository")
async def test_create_horario_duplicate_conflict(mock_repo_class, valid_horario_payload):
    """
    TDD Test: Verifies that if the repository reports a duplicate entry exists,
    the router short-circuits with a HTTP 409 Conflict.
    """
    # 1. Arrange: Enforce a simulated database collision
    mock_repo_instance = AsyncMock()
    mock_repo_instance.exists.return_value = True  # Duplicate exists!
    mock_repo_class.return_value = mock_repo_instance

    mock_db_session = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: mock_db_session

    # 2. Act
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/horarios", json=valid_horario_payload)

    # 3. Assert
    assert response.status_code == status.HTTP_409_CONFLICT
    assert "already been scheduled" in response.json()["detail"]
    mock_repo_instance.add.assert_not_called()
    mock_db_session.commit.assert_not_called()

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_horario_mock(**overrides):
    """Build a fake Horario ORM object suitable for mock returns."""
    obj = MagicMock()
    obj.id          = overrides.get("id",          uuid4())
    obj.class_name  = overrides.get("class_name",  "12B")
    obj.classroom   = overrides.get("classroom",   "Sala 102")
    obj.module_ref  = overrides.get("module_ref",  "A3_Derivadas")
    obj.description = overrides.get("description", "Introducao as derivadas")
    obj.lesson_date = overrides.get("lesson_date", date(2026, 6, 20))
    obj.start_time  = overrides.get("start_time",  time(9, 0))
    obj.end_time    = overrides.get("end_time",    time(10, 30))
    obj.created_at  = overrides.get("created_at",  datetime(2026, 6, 19, 8, 0, 0))
    return obj


# ---------------------------------------------------------------------------
# GET /api/v1/horarios
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.routers.horario.HorarioRepository")
async def test_list_horarios_returns_empty_list(mock_repo_class):
    """
    TDD Test: When no records exist the endpoint must return HTTP 200
    with an empty JSON array — never 404 or 500.
    """
    mock_repo_instance = AsyncMock()
    mock_repo_instance.get_all.return_value = []
    mock_repo_class.return_value = mock_repo_instance

    mock_db_session = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: mock_db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/horarios")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []
    mock_repo_instance.get_all.assert_called_once()

    app.dependency_overrides.clear()


@pytest.mark.asyncio
@patch("app.routers.horario.HorarioRepository")
async def test_list_horarios_returns_all_records(mock_repo_class):
    """
    TDD Test: When records exist the endpoint must return HTTP 200
    with a JSON array containing one entry per ORM object, serialised
    with the expected field names and values.
    """
    fake = _make_horario_mock()
    mock_repo_instance = AsyncMock()
    mock_repo_instance.get_all.return_value = [fake]
    mock_repo_class.return_value = mock_repo_instance

    mock_db_session = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: mock_db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/horarios")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert len(body) == 1
    item = body[0]
    assert item["class_name"]  == fake.class_name
    assert item["classroom"]   == fake.classroom
    assert item["lesson_date"] == str(fake.lesson_date)
    assert item["start_time"]  == fake.start_time.strftime("%H:%M:%S")
    assert item["end_time"]    == fake.end_time.strftime("%H:%M:%S")

    app.dependency_overrides.clear()
