"""
Sets up the logic for the Horario Router, so it later connects to the fastapi instance. It defines the basic logic.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models.horario import Horario
from app.repositories.horario import HorarioRepository
from app.schemas.horario import HorarioCreateSchema, HorarioReadSchema

router = APIRouter(
    prefix="/api/v1/horarios",
    tags=["Horarios"]
)


@router.get("", response_model=list[HorarioReadSchema], status_code=status.HTTP_200_OK)
async def list_horarios(db: AsyncSession = Depends(get_db_session)):
    repo = HorarioRepository(db)
    return await repo.get_all()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_horario(
    payload: HorarioCreateSchema,
    db: AsyncSession = Depends(get_db_session)
):
    repo = HorarioRepository(db)

    dedupe_keys = {
        "class_name": payload.class_name,
        "lesson_date": payload.lesson_date,
        "start_time": payload.start_time,
    }

    if await repo.exists(dedupe_keys):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A lesson has already been scheduled for this class at this specific time slot."
        )

    new_horario = Horario(
        class_name=payload.class_name,
        classroom=payload.classroom,
        module_ref=payload.module_ref,
        description=payload.description,
        lesson_date=payload.lesson_date,
        start_time=payload.start_time,
        end_time=payload.end_time
    )

    await repo.add(new_horario)
    await db.commit()

    return {
        "status": "success",
        "id": str(new_horario.id)
    }
