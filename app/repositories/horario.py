from typing import Any, Sequence
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.horario import Horario

class HorarioRepository:
    """
    Handles strict, asynchronous data isolation for the Horario domain.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all(self) -> Sequence[Horario]:
        """Fetches all schedules using async execution."""
        result = await self.session.execute(select(Horario))
        return result.scalars().all()

    async def find_by_fragment(self, filters: dict[str, Any]) -> Sequence[Horario]:
        """Queries records utilizing SQLAlchemy's async criteria filter."""
        stmt = select(Horario).filter_by(**filters)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def exists(self, dedupe_keys: dict[str, Any]) -> bool:
        """Isolated verification helper—business logic handles the rest."""
        stmt = select(func.count()).select_from(Horario).filter_by(**dedupe_keys)
        result = await self.session.execute(stmt)
        return (result.scalar() or 0) > 0

    async def add(self, horario_obj: Horario) -> Horario:
        """Persists the object instance to the tracking session."""
        self.session.add(horario_obj)
        await self.session.flush() 
        return horario_obj
