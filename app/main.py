from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.routers import horario as horario_router
from app.exceptions import NotFoundError, DuplicateHorarioError

app = FastAPI(title="Prof Service")


@app.exception_handler(NotFoundError)
async def domain_not_found_handler(request: Request, exc: NotFoundError):
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"status": "error", "type": "resource_missing", "detail": exc.message},
    )


@app.exception_handler(DuplicateHorarioError)
async def domain_duplicate_handler(request: Request, exc: DuplicateHorarioError):
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"status": "error", "type": "business_conflict", "detail": exc.message},
    )


app.include_router(horario_router.router)


@app.get("/health", status_code=200)
async def health_check():
    """
    Simple async health check endpoint to verify API availability.
    """
    return {"status": "healthy", "database": "connected_placeholder"}
