"""
This sets up a instance connection to the database. It conect's it to Postgresql now"""

import os
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql+asyncpg://postgres:postgres@db:5432/prof_db"
)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,  
    future=True
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an active async database session context."""
    async with AsyncSessionLocal() as session:
        yield session
