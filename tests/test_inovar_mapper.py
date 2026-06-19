"""
Step 1 — GREEN (corrected): inovar-to-horario data mapper.

map_inovar_to_horarios() is a pure function — no I/O, no network, no mocking.

IMPORTANT DOMAIN FACT: Inovar's hour codes (800, 900, ...) are slot labels, not
literal clock times. The real institution bell schedule is:
  800  -> 08:50-09:40    1200 -> 12:35-13:25
  900  -> 09:45-10:35    1300 -> 13:30-14:20
  1000 -> 10:45-11:35    1400 -> 14:25-15:15
  1100 -> 11:40-12:30    1500 -> 15:20-16:10
                         1600 -> 16:15-17:05

This came from TeacherDataConverter.REFERENCE['hour'] in the legacy project
(commands/utils/utils_teacher.py). The mapper must use this lookup, not a
naive `hour // 100` arithmetic, or every start time will be ~50 min wrong.
"""
from datetime import date, time


# ---------------------------------------------------------------------------
# Single item — full field mapping
# ---------------------------------------------------------------------------

def test_single_item_lesson_date():
    from app.services.inovar_mapper import map_inovar_to_horarios
    result = map_inovar_to_horarios({"20-06-2026": [{"class_name": "11B", "inovar_classroom": "MATEM", "hour": 800}]})
    assert result[0]["lesson_date"] == date(2026, 6, 20)


def test_single_item_start_time():
    """800 slot starts at 08:50, not 08:00 — institution bell schedule."""
    from app.services.inovar_mapper import map_inovar_to_horarios
    result = map_inovar_to_horarios({"20-06-2026": [{"class_name": "11B", "inovar_classroom": "MATEM", "hour": 800}]})
    assert result[0]["start_time"] == time(8, 50)


def test_single_item_end_time():
    """800 slot ends at 09:40."""
    from app.services.inovar_mapper import map_inovar_to_horarios
    result = map_inovar_to_horarios({"20-06-2026": [{"class_name": "11B", "inovar_classroom": "MATEM", "hour": 800}]})
    assert result[0]["end_time"] == time(9, 40)


def test_single_item_class_name():
    from app.services.inovar_mapper import map_inovar_to_horarios
    result = map_inovar_to_horarios({"20-06-2026": [{"class_name": "11B", "inovar_classroom": "MATEM", "hour": 800}]})
    assert result[0]["class_name"] == "11B"


def test_single_item_classroom_is_inovar_classroom():
    from app.services.inovar_mapper import map_inovar_to_horarios
    result = map_inovar_to_horarios({"20-06-2026": [{"class_name": "11B", "inovar_classroom": "MATEM - 11 N1", "hour": 800}]})
    assert result[0]["classroom"] == "MATEM - 11 N1"


def test_single_item_description_synthesised():
    from app.services.inovar_mapper import map_inovar_to_horarios
    result = map_inovar_to_horarios({"20-06-2026": [{"class_name": "11B", "inovar_classroom": "MATEM", "hour": 800}]})
    assert result[0]["description"] == "Aula de 11B"


def test_single_item_module_ref_is_none():
    from app.services.inovar_mapper import map_inovar_to_horarios
    result = map_inovar_to_horarios({"20-06-2026": [{"class_name": "11B", "inovar_classroom": "MATEM", "hour": 800}]})
    assert result[0]["module_ref"] is None


# ---------------------------------------------------------------------------
# Time slot variations — real bell schedule
# ---------------------------------------------------------------------------

def test_hour_900_maps_start_time():
    """900 slot starts at 09:45."""
    from app.services.inovar_mapper import map_inovar_to_horarios
    result = map_inovar_to_horarios({"20-06-2026": [{"class_name": "11B", "inovar_classroom": "X", "hour": 900}]})
    assert result[0]["start_time"] == time(9, 45)


def test_hour_900_maps_end_time():
    """900 slot ends at 10:35."""
    from app.services.inovar_mapper import map_inovar_to_horarios
    result = map_inovar_to_horarios({"20-06-2026": [{"class_name": "11B", "inovar_classroom": "X", "hour": 900}]})
    assert result[0]["end_time"] == time(10, 35)


def test_hour_1400_maps_correctly():
    """1400 slot: 14:25-15:15."""
    from app.services.inovar_mapper import map_inovar_to_horarios
    result = map_inovar_to_horarios({"20-06-2026": [{"class_name": "11B", "inovar_classroom": "X", "hour": 1400}]})
    assert result[0]["start_time"] == time(14, 25)
    assert result[0]["end_time"] == time(15, 15)


def test_hour_1600_maps_correctly():
    """1600 slot: 16:15-17:05."""
    from app.services.inovar_mapper import map_inovar_to_horarios
    result = map_inovar_to_horarios({"20-06-2026": [{"class_name": "11B", "inovar_classroom": "X", "hour": 1600}]})
    assert result[0]["start_time"] == time(16, 15)
    assert result[0]["end_time"] == time(17, 5)


# ---------------------------------------------------------------------------
# Multiple dates and items
# ---------------------------------------------------------------------------

def test_two_dates_produce_two_items():
    from app.services.inovar_mapper import map_inovar_to_horarios
    payload = {
        "20-06-2026": [{"class_name": "11B", "inovar_classroom": "MAT", "hour": 800}],
        "21-06-2026": [{"class_name": "11B", "inovar_classroom": "MAT", "hour": 900}],
    }
    result = map_inovar_to_horarios(payload)
    assert len(result) == 2


def test_two_classes_same_date_produce_two_items():
    from app.services.inovar_mapper import map_inovar_to_horarios
    payload = {
        "20-06-2026": [
            {"class_name": "11B", "inovar_classroom": "MAT", "hour": 800},
            {"class_name": "12A", "inovar_classroom": "PORT", "hour": 900},
        ]
    }
    result = map_inovar_to_horarios(payload)
    assert len(result) == 2


def test_dates_are_parsed_correctly_across_multiple_entries():
    from app.services.inovar_mapper import map_inovar_to_horarios
    payload = {
        "23-06-2026": [{"class_name": "11B", "inovar_classroom": "MAT", "hour": 800}],
        "24-06-2026": [{"class_name": "11B", "inovar_classroom": "MAT", "hour": 800}],
    }
    result = map_inovar_to_horarios(payload)
    dates = {item["lesson_date"] for item in result}
    assert date(2026, 6, 23) in dates
    assert date(2026, 6, 24) in dates


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_schedule_returns_empty_list():
    from app.services.inovar_mapper import map_inovar_to_horarios
    assert map_inovar_to_horarios({}) == []


def test_date_with_empty_class_list_produces_no_items():
    from app.services.inovar_mapper import map_inovar_to_horarios
    result = map_inovar_to_horarios({"20-06-2026": []})
    assert result == []


def test_does_not_raise_for_any_valid_hour_in_school_range():
    from app.services.inovar_mapper import map_inovar_to_horarios
    for hour in [800, 900, 1000, 1100, 1200, 1300, 1400, 1500, 1600]:
        payload = {"20-06-2026": [{"class_name": "11B", "inovar_classroom": "X", "hour": hour}]}
        result = map_inovar_to_horarios(payload)
        assert len(result) == 1


def test_output_items_satisfy_horario_create_schema():
    """Each mapped item must be accepted by HorarioCreateSchema without error."""
    from app.services.inovar_mapper import map_inovar_to_horarios
    from app.schemas.horario import HorarioCreateSchema
    payload = {
        "20-06-2026": [{"class_name": "11B", "inovar_classroom": "MATEM - 11 N1", "hour": 800}]
    }
    for item in map_inovar_to_horarios(payload):
        schema = HorarioCreateSchema(**item)
        assert schema.class_name == "11B"
