from fastapi import FastAPI

from app.errors import init_error_handlers
from app.routers import horario as horario_router

# Tracks whether Vault was active and reachable at startup.
# Set by the lifespan handler; read by the /health endpoint.
_vault_status: str = "not_configured"


async def _init_db() -> None:
    """Create all SQLAlchemy tables if they don't exist yet (idempotent)."""
    from app.database import engine
    from app.models.horario import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def lifespan(app: FastAPI):
    """
    Startup:
      1. Create database tables if they don't exist yet (idempotent).
      2. Validate credentials — eagerly call get_settings() so Vault
         errors are caught at boot time rather than silently on the
         first request.

    If Vault is configured but unreachable (sealed, wrong address, etc.)
    we raise RuntimeError with a clear human-readable message so the
    container exits with a meaningful log line instead of a traceback.

    If Vault is not configured (VAULT_ADDR absent) we proceed normally —
    the env-only credential path is valid for local development.
    """
    global _vault_status

    # --- Step 1: ensure the horarios table exists ----------------------
    await _init_db()


    from app.config import get_settings
    from app.exceptions import VaultUnavailableError, VaultAuthError

    try:
        settings = get_settings()
        if settings.vault_addr:
            _vault_status = "connected"
        else:
            _vault_status = "not_configured"
    except (VaultUnavailableError, VaultAuthError) as exc:
        raise RuntimeError(
            f"Vault is sealed or unreachable — application cannot start. "
            f"Unseal Vault at {exc.message} and restart. "
            f"Original error: {exc.message}"
        ) from exc

    yield
    # Shutdown — nothing to clean up for now.


app = FastAPI(title="Prof Service", lifespan=lifespan)

init_error_handlers(app)
app.include_router(horario_router.router)


@app.get("/health", status_code=200)
async def health_check():
    """
    Health probe. Determines Vault status from the cached settings object
    so it works correctly whether or not the lifespan has fired (test mode).

      vault: "connected"      — VAULT_ADDR was set and credentials loaded
      vault: "not_configured" — running in env-only mode (no Vault)
    """
    from app.config import get_settings
    settings = get_settings()
    vault_status = "connected" if settings.vault_addr else "not_configured"
    return {"status": "healthy", "vault": vault_status}
