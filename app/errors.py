"""
Global exception handler registration for prof-horario.

Keeps app/main.py clean by decoupling the handler definitions into a
single init_error_handlers(app) call.  All DomainError subclasses are
caught by one polymorphic handler that reads status_code and error_code
directly from the exception class — no isinstance chains required.
"""
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.exceptions import DomainError

logger = logging.getLogger("app.errors")


def init_error_handlers(app: FastAPI) -> None:
    """Register all global exception handlers onto *app*."""

    @app.exception_handler(DomainError)
    async def domain_error_handler(
        request: Request, exc: DomainError
    ) -> JSONResponse:
        """
        Catches every DomainError subclass polymorphically.
        status_code and error_code are read from the exception class
        attributes — no isinstance dispatch needed.

        Only scalar / string fields from __dict__ are forwarded as
        'details' to guarantee JSON serialisability.  The 'message'
        and 'args' keys are excluded (message is already top-level).
        """
        status_code = exc.status_code
        error_code = exc.error_code

        logger.warning(
            "Domain error [%s] on %s: %s",
            error_code,
            request.url.path,
            exc.message,
        )

        # Convert all extra instance fields to str to guarantee
        # JSONResponse will not crash on non-serialisable objects
        # (e.g. UUID, date, time from HorarioNotFoundError.horario_id).
        details = {
            k: str(v)
            for k, v in exc.__dict__.items()
            if k not in ("message", "args")
        }

        return JSONResponse(
            status_code=status_code,
            content={
                "success": False,
                "error": {
                    "code": error_code,
                    "message": exc.message,
                    "details": details or None,
                },
            },
        )
