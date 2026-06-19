"""
app/services/inovar_mapper.py

Pure function: converts the dict produced by extract_schedule_by_date()
into a list of dicts that each satisfy HorarioCreateSchema.

No I/O.  No side-effects.  Safe to call in tests with zero mocking.

DOMAIN FACT — Inovar hour codes are slot labels, not clock times.
The source of truth is TeacherDataConverter.REFERENCE['hour'] in the
legacy project (commands/utils/utils_teacher.py). Doing `hour // 100`
would place every lesson ~50 minutes early.

Bell schedule (epralima institution):
    800  -> 08:50-09:40    1200 -> 12:35-13:25
    900  -> 09:45-10:35    1300 -> 13:30-14:20
    1000 -> 10:45-11:35    1400 -> 14:25-15:15
    1100 -> 11:40-12:30    1500 -> 15:20-16:10
                           1600 -> 16:15-17:05

Inovar schedule shape (input)
------------------------------
{
    "dd-mm-yyyy": [
        {
            "class_name":       str,   # e.g. "11B"
            "inovar_classroom": str,   # e.g. "MATEM - 11 N1 / 11 N2 - AV-08"
            "hour":             int,   # e.g. 800  (slot label, NOT 08:00)
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
    "end_time":     time,
}
"""
from __future__ import annotations

from datetime import date, time, datetime
from typing import Any

# Date format used by the Inovar HTML parser in the legacy project.
_INOVAR_DATE_FMT: str = "%d-%m-%Y"

# Real institution bell schedule.
# Key = Inovar hour code (int), Value = (start_time, end_time).
# Source: TeacherDataConverter.REFERENCE['hour'] in commands/utils/utils_teacher.py
BELL_SCHEDULE: dict[int, tuple[time, time]] = {
    800:  (time(8,  50), time(9,  40)),
    900:  (time(9,  45), time(10, 35)),
    1000: (time(10, 45), time(11, 35)),
    1100: (time(11, 40), time(12, 30)),
    1200: (time(12, 35), time(13, 25)),
    1300: (time(13, 30), time(14, 20)),
    1400: (time(14, 25), time(15, 15)),
    1500: (time(15, 20), time(16, 10)),
    1600: (time(16, 15), time(17,  5)),
}


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

    Raises:
        ValueError: If an hour code is not present in BELL_SCHEDULE.
    """
    result: list[dict[str, Any]] = []

    for date_str, slots in schedule.items():
        lesson_date: date = datetime.strptime(date_str, _INOVAR_DATE_FMT).date()

        for slot in slots:
            hour: int = slot["hour"]
            if hour not in BELL_SCHEDULE:
                valid = sorted(BELL_SCHEDULE.keys())
                raise ValueError(
                    f"Unknown Inovar hour code {hour!r}. "
                    f"Valid codes: {valid}"
                )
            start, end = BELL_SCHEDULE[hour]
            result.append(
                {
                    "class_name":  slot["class_name"],
                    "classroom":   slot["inovar_classroom"],
                    "module_ref":  None,
                    "description": f"Aula de {slot['class_name']}",
                    "lesson_date": lesson_date,
                    "start_time":  start,
                    "end_time":    end,
                }
            )

    return result
