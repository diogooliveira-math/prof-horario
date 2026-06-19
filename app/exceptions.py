"""
Domain exceptions for prof-horario.

These classes know nothing about HTTP, FastAPI, or status codes.
Translation to HTTP happens in app/main.py via global exception handlers.
"""
from typing import Any


class DomainError(Exception):
    """Base exception for all domain-specific errors in prof-horario."""

    def __init__(self, message: str = "An unexpected domain error occurred."):
        self.message = message
        super().__init__(self.message)


class NotFoundError(DomainError):
    """Raised when a requested resource cannot be located."""

    def __init__(self, message: str = "The requested resource was not found."):
        super().__init__(message)


class HorarioNotFoundError(NotFoundError):
    """Raised when a specific horario cannot be located by its ID."""

    def __init__(self, horario_id: Any):
        self.horario_id = horario_id
        super().__init__(f"Horário with ID '{horario_id}' was not found.")


class DuplicateHorarioError(DomainError):
    """Raised when a new horario would conflict with an existing time slot."""

    def __init__(
        self,
        message: str = "A lesson has already been scheduled for this class at this specific time slot.",
    ):
        super().__init__(message)
