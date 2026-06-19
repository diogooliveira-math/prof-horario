from fastapi import FastAPI

app = FastAPI(title="Prof Service")


@app.get("/health", status_code=200)
async def health_check():
    """
    Simple async health check endpoint to verify API availability.
    """
    return {"status": "healthy", "database": "connected_placeholder"}
