import pytest
from httpx import AsyncClient, ASGITransport 
from fastapi import status
from unittest.mock import AsyncMock, patch

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
