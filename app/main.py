from fastapi import FastAPI

from app.errors import init_error_handlers
from app.routers import horario as horario_router

app = FastAPI(title="Prof Service")

init_error_handlers(app)

app.include_router(horario_router.router)


@app.get("/health", status_code=200)
async def health_check():
    """
    Simple async health check endpoint to verify API availability.
    """
    return {"status": "healthy", "database": "connected_placeholder"}
