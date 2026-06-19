from datetime import date, time, datetime
from typing import Optional
from uuid import UUID, uuid4
from sqlalchemy import String, Date, Time, DateTime, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models mapping to Postgres."""
    pass

class Horario(Base):
    __tablename__ = "horarios"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    class_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    classroom: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    module_ref: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)

    lesson_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)

    # 4. Audit Trail: Automatically handled by Postgres server-side
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=text("TIMEZONE('utc', NOW())")
    )
