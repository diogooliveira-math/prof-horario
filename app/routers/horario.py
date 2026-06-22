"""
Sets up the logic for the Horario Router, so it later connects to the fastapi instance.
It defines the basic logic.
"""
import csv
import io
import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db_session
from app.exceptions import DuplicateHorarioError, HorarioNotFoundError
from app.models.horario import Horario
from app.repositories.horario import HorarioRepository
from app.schemas.horario import HorarioCreateSchema, HorarioReadSchema
from app.services.inovar_mapper import map_inovar_to_horarios
from app.services.inovar_scraper import InovarScraperService
logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/horarios",
    tags=["Horarios"],
)


@router.get("", response_model=list[HorarioReadSchema], status_code=status.HTTP_200_OK)
async def list_horarios(db: AsyncSession = Depends(get_db_session)):
    repo = HorarioRepository(db)
    return await repo.get_all()


@router.get("/{horario_id}", response_model=HorarioReadSchema, status_code=status.HTTP_200_OK)
async def get_horario(horario_id: UUID, db: AsyncSession = Depends(get_db_session)):
    repo = HorarioRepository(db)
    horario = await repo.get_by_id(horario_id)
    if horario is None:
        raise HorarioNotFoundError(horario_id)
    return horario


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_horario(
    payload: HorarioCreateSchema,
    db: AsyncSession = Depends(get_db_session),
):
    repo = HorarioRepository(db)

    dedupe_keys = {
        "class_name": payload.class_name,
        "lesson_date": payload.lesson_date,
        "start_time": payload.start_time,
    }

    if await repo.exists(dedupe_keys):
        raise DuplicateHorarioError()

    new_horario = Horario(
        class_name=payload.class_name,
        classroom=payload.classroom,
        module_ref=payload.module_ref,
        description=payload.description,
        lesson_date=payload.lesson_date,
        start_time=payload.start_time,
        end_time=payload.end_time,
    )

    await repo.add(new_horario)
    await db.commit()

    return {
        "status": "success",
        "id": str(new_horario.id),
    }


@router.delete("/{horario_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_horario(horario_id: UUID, db: AsyncSession = Depends(get_db_session)):
    repo = HorarioRepository(db)
    horario = await repo.get_by_id(horario_id)
    if horario is None:
        raise HorarioNotFoundError(horario_id)
    await repo.delete(horario)
    await db.commit()


@router.post("/sync", status_code=status.HTTP_200_OK)
async def sync_horarios(
    week: Literal["current", "next"] = Query(default="next"),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    """Scrape the Inovar schedule for *week* and persist new slots.

    Returns a summary dict::

        {"inserted": int, "skipped": int, "errors": int}

    Duplicate slots (same class_name + lesson_date + start_time) are counted
    as "skipped" and never written twice.  A single DB commit covers all
    insertions so the operation is atomic.

    Raises:
        InovarAuthError          (401) — credentials rejected or form absent.
        InovarNavigationError    (502) — Playwright nav step failed.
        InovarEmptyScheduleError (200) — week is valid but has no lessons.
    """
    scraper = InovarScraperService(
        username=settings.inovar_username,
        password=settings.inovar_password,
        inovar_url=settings.inovar_url,
    )

    raw_schedule = await scraper.scrape_week(week)
    slots = map_inovar_to_horarios(raw_schedule)

    repo = HorarioRepository(db)
    inserted = skipped = errors = 0

    for slot in slots:
        dedupe_keys = {
            "class_name":  slot["class_name"],
            "lesson_date": slot["lesson_date"],
            "start_time":  slot["start_time"],
        }

        if await repo.exists(dedupe_keys):
            skipped += 1
            continue

        new_horario = Horario(
            class_name=slot["class_name"],
            classroom=slot.get("classroom"),
            module_ref=slot.get("module_ref"),
            description=slot["description"],
            lesson_date=slot["lesson_date"],
            start_time=slot["start_time"],
            end_time=slot["end_time"],
        )
        await repo.add(new_horario)
        inserted += 1

    await db.commit()

    logger.info(
        "sync_horarios week=%s: inserted=%d skipped=%d errors=%d",
        week, inserted, skipped, errors,
    )

    return {"inserted": inserted, "skipped": skipped, "errors": errors}


@router.get("/export/csv", status_code=status.HTTP_200_OK)
async def export_horarios_csv(db: AsyncSession = Depends(get_db_session)):
    """Export all stored horarios as a CSV file compatible with sync-to-outlook.ps1.

    The CSV shape matches what the legacy sync-to-outlook.ps1 PowerShell script
    expects:
        date          — lesson date in dd-mm-yyyy format
        class_name    — short class identifier (e.g. "11B")
        inovar_classroom — room string as scraped from Inovar (e.g. "AV-08")
        hour          — Inovar-style integer slot code (e.g. 850 for 08:50)
        fetched_at    — ISO timestamp when the record was created in this service

    The `hour` value is reconstructed from start_time: HH*100 + MM (e.g. 08:50
    -> 850).  This is the same encoding the legacy TeacherDataConverter uses
    and what sync-to-outlook.ps1 reads via `$hourInt = [int]$entry.hour`.
    """
    repo = HorarioRepository(db)
    horarios = await repo.get_all()

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["date", "class_name", "inovar_classroom", "hour", "fetched_at"],
    )
    writer.writeheader()

    for h in horarios:
        # Reconstruct the Inovar-style integer hour code from start_time.
        # time(8, 50)  -> 850
        # time(10, 45) -> 1045
        hour_code = h.start_time.hour * 100 + h.start_time.minute

        writer.writerow({
            "date":             h.lesson_date.strftime("%d-%m-%Y"),
            "class_name":       h.class_name,
            "inovar_classroom": h.classroom or "",
            "hour":             hour_code,
            "fetched_at":       h.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        })

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=horario.csv"},
    )
