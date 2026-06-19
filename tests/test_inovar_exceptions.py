"""
Step 0 — RED phase: domain exception classes for Inovar failures.

These tests drive the creation of three new DomainError subclasses in
app/exceptions.py. No Playwright, no network, no mocking required here.
"""
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI

from app.exceptions import DomainError
from app.errors import init_error_handlers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app_with_route(exc_factory):
    """Minimal FastAPI app that raises a given exception on GET /crash."""
    test_app = FastAPI()
    init_error_handlers(test_app)

    @test_app.get("/crash")
    async def crash():
        raise exc_factory()

    return test_app


# ---------------------------------------------------------------------------
# InovarAuthError
# ---------------------------------------------------------------------------

def test_inovar_auth_error_is_domain_error():
    from app.exceptions import InovarAuthError
    assert issubclass(InovarAuthError, DomainError)


def test_inovar_auth_error_status_code():
    from app.exceptions import InovarAuthError
    assert InovarAuthError.status_code == 401


def test_inovar_auth_error_code():
    from app.exceptions import InovarAuthError
    assert InovarAuthError.error_code == "INOVAR_AUTH_ERROR"


def test_inovar_auth_error_default_message():
    from app.exceptions import InovarAuthError
    exc = InovarAuthError()
    assert exc.message
    assert "login" in exc.message.lower() or "auth" in exc.message.lower() or "credential" in exc.message.lower()


def test_inovar_auth_error_custom_message():
    from app.exceptions import InovarAuthError
    exc = InovarAuthError("login form did not appear within 60 s")
    assert "login form" in exc.message


@pytest.mark.asyncio
async def test_inovar_auth_error_yields_401_via_global_handler():
    from app.exceptions import InovarAuthError
    app = _make_app_with_route(InovarAuthError)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/crash")
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INOVAR_AUTH_ERROR"


# ---------------------------------------------------------------------------
# InovarNavigationError
# ---------------------------------------------------------------------------

def test_inovar_navigation_error_is_domain_error():
    from app.exceptions import InovarNavigationError
    assert issubclass(InovarNavigationError, DomainError)


def test_inovar_navigation_error_status_code():
    from app.exceptions import InovarNavigationError
    assert InovarNavigationError.status_code == 502


def test_inovar_navigation_error_code():
    from app.exceptions import InovarNavigationError
    assert InovarNavigationError.error_code == "INOVAR_NAVIGATION_ERROR"


def test_inovar_navigation_error_default_message():
    from app.exceptions import InovarNavigationError
    exc = InovarNavigationError()
    assert exc.message


def test_inovar_navigation_error_custom_message():
    from app.exceptions import InovarNavigationError
    exc = InovarNavigationError("week label did not change after clicking Semana Seguinte")
    assert "week label" in exc.message


@pytest.mark.asyncio
async def test_inovar_navigation_error_yields_502_via_global_handler():
    from app.exceptions import InovarNavigationError
    app = _make_app_with_route(InovarNavigationError)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/crash")
    assert response.status_code == 502
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INOVAR_NAVIGATION_ERROR"


# ---------------------------------------------------------------------------
# InovarEmptyScheduleError
# ---------------------------------------------------------------------------

def test_inovar_empty_schedule_error_is_domain_error():
    from app.exceptions import InovarEmptyScheduleError
    assert issubclass(InovarEmptyScheduleError, DomainError)


def test_inovar_empty_schedule_error_status_code():
    from app.exceptions import InovarEmptyScheduleError
    # 200 — an empty week is not a server failure, it is a valid outcome
    assert InovarEmptyScheduleError.status_code == 200


def test_inovar_empty_schedule_error_code():
    from app.exceptions import InovarEmptyScheduleError
    assert InovarEmptyScheduleError.error_code == "INOVAR_EMPTY_SCHEDULE"


def test_inovar_empty_schedule_error_default_message():
    from app.exceptions import InovarEmptyScheduleError
    exc = InovarEmptyScheduleError()
    assert exc.message


@pytest.mark.asyncio
async def test_inovar_empty_schedule_error_yields_200_via_global_handler():
    from app.exceptions import InovarEmptyScheduleError
    app = _make_app_with_route(InovarEmptyScheduleError)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/crash")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INOVAR_EMPTY_SCHEDULE"


# ---------------------------------------------------------------------------
# MRO sanity: all three are independent leaves, not siblings of each other
# ---------------------------------------------------------------------------

def test_inovar_exceptions_are_independent():
    from app.exceptions import InovarAuthError, InovarNavigationError, InovarEmptyScheduleError
    assert not issubclass(InovarAuthError, InovarNavigationError)
    assert not issubclass(InovarNavigationError, InovarEmptyScheduleError)
    assert not issubclass(InovarEmptyScheduleError, InovarAuthError)
