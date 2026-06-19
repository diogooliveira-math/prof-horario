from datetime import date, time, datetime
from uuid import UUID
from pydantic import BaseModel, Field, model_validator
from pydantic import ConfigDict


class HorarioCreateSchema(BaseModel):
    class_name: str = Field(..., max_length=50, examples=["12B"])
    classroom: str | None = Field(None, max_length=50, examples=["Sala 102"])
    module_ref: str | None = Field(None, max_length=100)
    description: str = Field(..., max_length=500)
    lesson_date: date = Field(..., description="The date of the lesson (YYYY-MM-DD)")
    start_time: time = Field(..., description="The start time of the lesson (HH:MM)")
    end_time: time = Field(..., description="The end time of the lesson (HH:MM)")

    @model_validator(mode='after')
    def verify_time_sequence(self) -> 'HorarioCreateSchema':
        """Ensures logic sequence makes chronological sense."""
        if self.start_time >= self.end_time:
            raise ValueError("end_time must be chronologically after start_time.")
        return self


class HorarioReadSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    class_name: str
    classroom: str | None
    module_ref: str | None
    description: str
    lesson_date: date
    start_time: time
    end_time: time
    created_at: datetime
