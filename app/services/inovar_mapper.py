"""
app/services/inovar_mapper.py

Pure function: converts the dict produced by the legacy
extract_schedule_by_date() into a list of dicts that each
satisfy HorarioCreateSchema.

No I/O.  No side-effects.  Safe to call in tests with zero mocking.

Inovar schedule shape (input)
------------------------------
{
    "dd-mm-yyyy": [
        {
            "class_name":       str,   # e.g. "11B"
            "inovar_classroom": str,   # e.g. "MATEM - 11 N1 / 11 N2 - AV-08"
            "hour":             int,   # e.g. 800  (= 08:00)
        },
        ...
    ],
    ...
}

HorarioCreateSchema shape (output per item)
--------------------------------------------
{
    "class_name":   str,
    "classroom":    str | None,
    "module_ref":   None,          # Inovar provides no module reference
    "description":  str,           # synthesised: "Aula de <class_name>"
    "lesson_date":  date,
    "start_time":   time,
    "end_time":     time,          # start + LESSON_DURATION_MINUTES
}
"""
from __future__ import annotations

from datetime import date, time, datetime, timedelta
from typing import Any

# Named constant — one place to change if the institution changes lesson length.
LESSON_DURATION_MINUTES: int = 50

# Date format used by the Inovar HTML parser in the legacy project.
_INOVAR_DATE_FMT: str = "%d-%m-%Y"


def _parse_hour(hour: int) -> time:
    """Convert a 3-or-4-digit Inovar hour integer to a datetime.time.

    Inovar encodes times as plain integers where the hundreds digit(s)
    are the hour and the last two digits are the minute:
        800  -> time(8, 0)
        900  -> time(9, 0)
        1400 -> time(14, 0)
    """
    h, m = divmod(hour, 100)
    return time(h, m)


def _end_time(start: time) -> time:
    """Add LESSON_DURATION_MINUTES to *start* and return the resulting time.

    Uses a datetime sentinel date to perform arithmetic safely without
    implementing manual minute-carry logic.
    """
    sentinel = datetime(2000, 1, 1, start.hour, start.minute)
    end_dt = sentinel + timedelta(minutes=LESSON_DURATION_MINUTES)
    return end_dt.time()


def map_inovar_to_horarios(
    schedule: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Map an Inovar weekly schedule dict to a list of HorarioCreateSchema-compatible dicts.

    Args:
        schedule: Output of extract_schedule_by_date().  Keys are date strings
                  in "dd-mm-yyyy" format; values are lists of class-slot dicts.

    Returns:
        A flat list of dicts, one per class-slot, ready to be unpacked into
        HorarioCreateSchema(**item).  Returns an empty list for an empty schedule.
    """
    result: list[dict[str, Any]] = []

    for date_str, slots in schedule.items():
        lesson_date: date = datetime.strptime(date_str, _INOVAR_DATE_FMT).date()

        for slot in slots:
            start: time = _parse_hour(slot["hour"])
            result.append(
                {
                    "class_name":  slot["class_name"],
                    "classroom":   slot["inovar_classroom"],
                    "module_ref":  None,
                    "description": f"Aula de {slot['class_name']}",
                    "lesson_date": lesson_date,
                    "start_time":  start,
                    "end_time":    _end_time(start),
                }
            )

    return result
