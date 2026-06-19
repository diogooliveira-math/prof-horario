"""
Unit tests for domain exception hierarchy (app/exceptions.py).
These tests define the contract before the module exists — pure RED phase.
"""
import pytest
from uuid import uuid4

from app.exceptions import (
    DomainError,
    NotFoundError,
    HorarioNotFoundError,
    DuplicateHorarioError,
)


# ---------------------------------------------------------------------------
# DomainError
# ---------------------------------------------------------------------------

def test_domain_error_default_message():
    exc = DomainError()
    assert exc.message == "An unexpected domain error occurred."
    assert str(exc) == "An unexpected domain error occurred."


def test_domain_error_custom_message():
    exc = DomainError("something went wrong")
    assert exc.message == "something went wrong"
    assert str(exc) == "something went wrong"


def test_domain_error_is_exception():
    assert issubclass(DomainError, Exception)


# ---------------------------------------------------------------------------
# NotFoundError
# ---------------------------------------------------------------------------

def test_not_found_error_is_domain_error():
    assert issubclass(NotFoundError, DomainError)


def test_not_found_error_default_message_contains_not_found():
    exc = NotFoundError()
    assert "not found" in exc.message.lower()


def test_not_found_error_custom_message():
    exc = NotFoundError("thing X not found")
    assert exc.message == "thing X not found"


# ---------------------------------------------------------------------------
# HorarioNotFoundError
# ---------------------------------------------------------------------------

def test_horario_not_found_error_is_not_found_error():
    exc = HorarioNotFoundError(uuid4())
    assert isinstance(exc, NotFoundError)


def test_horario_not_found_error_stores_horario_id():
    hid = uuid4()
    exc = HorarioNotFoundError(hid)
    assert exc.horario_id == hid


def test_horario_not_found_error_message_contains_id():
    hid = "abc-123"
    exc = HorarioNotFoundError(hid)
    assert "abc-123" in exc.message


def test_horario_not_found_error_message_contains_not_found():
    exc = HorarioNotFoundError("some-id")
    assert "not found" in exc.message.lower()


# ---------------------------------------------------------------------------
# DuplicateHorarioError
# ---------------------------------------------------------------------------

def test_duplicate_horario_error_is_domain_error():
    assert issubclass(DuplicateHorarioError, DomainError)


def test_duplicate_horario_error_default_message_is_not_empty():
    exc = DuplicateHorarioError()
    assert exc.message


def test_duplicate_horario_error_custom_message():
    msg = "This slot is already taken."
    exc = DuplicateHorarioError(msg)
    assert exc.message == msg


# ---------------------------------------------------------------------------
# Class-level HTTP metadata attributes (polymorphic dispatch)
# ---------------------------------------------------------------------------

def test_domain_error_has_status_code_400():
    assert DomainError.status_code == 400


def test_domain_error_has_error_code_bad_request():
    assert DomainError.error_code == "BAD_REQUEST"


def test_not_found_error_has_status_code_404():
    assert NotFoundError.status_code == 404


def test_not_found_error_has_error_code_not_found():
    assert NotFoundError.error_code == "NOT_FOUND"


def test_horario_not_found_error_inherits_status_code_404():
    # inherits from NotFoundError — no override needed
    assert HorarioNotFoundError.status_code == 404


def test_horario_not_found_error_has_own_error_code():
    assert HorarioNotFoundError.error_code == "HORARIO_NOT_FOUND"


def test_duplicate_horario_error_has_status_code_409():
    assert DuplicateHorarioError.status_code == 409


def test_duplicate_horario_error_has_error_code():
    assert DuplicateHorarioError.error_code == "DUPLICATE_HORARIO"
