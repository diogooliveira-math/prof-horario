"""
Domain exceptions for prof-horario.

These classes know nothing about FastAPI or HTTP transports.
Each class carries status_code and error_code as class attributes so
the global handler in app/errors.py can dispatch polymorphically —
no isinstance chains, no open/closed violations.
"""
from typing import Any


class DomainError(Exception):
    """Base exception for all domain-specific errors in prof-horario."""

    status_code: int = 400
    error_code: str = "BAD_REQUEST"

    def __init__(self, message: str = "An unexpected domain error occurred."):
        self.message = message
        super().__init__(self.message)


class NotFoundError(DomainError):
    """Raised when a requested resource cannot be located."""

    status_code = 404
    error_code = "NOT_FOUND"

    def __init__(self, message: str = "The requested resource was not found."):
        super().__init__(message)


class HorarioNotFoundError(NotFoundError):
    """Raised when a specific horario cannot be located by its ID."""

    error_code = "HORARIO_NOT_FOUND"
    # status_code inherited from NotFoundError (404)

    def __init__(self, horario_id: Any):
        self.horario_id = horario_id
        super().__init__(f"Horário with ID '{horario_id}' was not found.")


class DuplicateHorarioError(DomainError):
    """Raised when a new horario would conflict with an existing time slot."""

    status_code = 409
    error_code = "DUPLICATE_HORARIO"

    def __init__(
        self,
        message: str = "A lesson has already been scheduled for this class at this specific time slot.",
    ):
        super().__init__(message)
