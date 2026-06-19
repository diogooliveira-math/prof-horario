"""
app/services/inovar_html.py

HTML parsing layer — ported from the legacy project's:
  commands/utils/utils_html.py   (HtmlUtils class)
  commands/services/browser_services.py (extract_schedule_by_date method)

Provides a single public function:

    extract_schedule_by_date(html: str) -> dict[str, list[dict]]

Returns:
    {
        "dd-mm-yyyy": [
            {"class_name": str, "inovar_classroom": str, "hour": int},
            ...
        ],
        ...
    }

Where `hour` is the Inovar slot label integer (e.g. 800, 900).
Callers (inovar_mapper.py) are responsible for converting slot labels
to real clock times using BELL_SCHEDULE.

Design notes:
  - No direct dependency on the legacy WSL project.
  - `lxml` is used as the BeautifulSoup parser (faster than html.parser).
  - TeacherDataConverter.convert_inovar_class() logic is inlined — no
    REFERENCE lookup needed; the scraper stores the raw Inovar classroom
    string and our mapper maps only the class_name via the REFERENCE dict.
  - Hour codes are returned as integers, NOT converted to time dicts.
    The clean split is: html.py produces raw codes, mapper converts them.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compiled regexes (same patterns as the legacy utils_html.py)
# ---------------------------------------------------------------------------

# Matches dates like "02-03-2026"
_date_re = re.compile(r"\b\d{2}-\d{2}-\d{4}\b")

# Matches school-hour time cells in the range 7:00–17:59 (inclusive).
# Amendment A1 from the legacy project: prevents row-index labels like
# "0 00", "1 00" from being mistaken for time markers.
_time_re = re.compile(
    r"^([7-9]|1[0-7])\s*\d{2}$"  # "8 00", "10 00", "17 59"
    r"|^[7-9]\d{2}$"               # "800", "900"
    r"|^1[0-7]\d{2}$"              # "1000", "1700"
)

# Inovar class-string pattern: "SUBJECT - GROUPS - ROOM"
# We extract the class_name from the first two segments and the room from the last.
_CLASS_REFERENCE: dict[str, str] = {
    "MATEM - 12 H1 / 12 H2": "12-H12",
    "MATEM - 10 S1 / 10 S2": "10-S12",
    "MATEM - 10 T1 / 10 T2": "10-T12",
    "MATEM - 11 M1 / 11 M2": "11-M12",
    "MATEM - 10 U1":          "10-U1",
    "MATEM - 11 N1 / 11 N2": "11-N12",
    "MATEM - 11 O2":          "11-O2",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _convert_class(raw: str) -> tuple[str, str]:
    """Map a raw Inovar class string to (class_name, inovar_classroom).

    Returns the standardised short name if found in _CLASS_REFERENCE,
    otherwise falls back to the raw string.  The classroom code is the
    rightmost dash-separated segment (e.g. "AV-09").
    """
    # Exact match
    if raw in _CLASS_REFERENCE:
        class_name = _CLASS_REFERENCE[raw]
    else:
        # Partial match: first two dash-separated segments
        parts = raw.split(" - ")
        key = " - ".join(parts[:2]) if len(parts) >= 2 else raw
        class_name = _CLASS_REFERENCE.get(key, raw)

    # Room code = last dash-separated segment
    parts = raw.split(" - ")
    classroom = parts[-1].strip() if len(parts) > 1 else raw

    return class_name, classroom


def _table_to_matrix(table) -> list[list[str]]:
    """Convert an HTML <table> to a 2D list of strings, rowspan/colspan aware."""
    grid: list[list[str]] = []
    span_map: dict[tuple[int, int], str] = {}

    def _set(r: int, c: int, val: str) -> None:
        while len(grid) <= r:
            grid.append([])
        while len(grid[r]) <= c:
            grid[r].append("")
        grid[r][c] = val

    def _register(r: int, c: int, val: str, rowspan: int, colspan: int) -> None:
        for rr in range(r, r + rowspan):
            for cc in range(c, c + colspan):
                if rr == r and cc == c:
                    continue
                span_map[(rr, cc)] = val

    for r_idx, row in enumerate(table.find_all("tr")):
        c_idx = 0
        while len(grid) <= r_idx:
            grid.append([])

        while (r_idx, c_idx) in span_map:
            _set(r_idx, c_idx, span_map.pop((r_idx, c_idx)))
            c_idx += 1

        for cell in row.find_all(["td", "th"]):
            while (r_idx, c_idx) in span_map:
                _set(r_idx, c_idx, span_map.pop((r_idx, c_idx)))
                c_idx += 1

            text = _normalize(cell.get_text(" "))
            rowspan = int(cell.get("rowspan", 1) or 1)
            colspan = int(cell.get("colspan", 1) or 1)

            _set(r_idx, c_idx, text)
            if rowspan > 1 or colspan > 1:
                _register(r_idx, c_idx, text, rowspan, colspan)
            c_idx += 1

    return grid


def _find_schedule_table(soup: BeautifulSoup):
    """Pick the best schedule table using the legacy scoring heuristic.

    Priority:
      1. Tables with DD-MM-YYYY date cells (score +1000 + 10×count)
      2. Large tables (>100 cells: +100, >50: +50)
      3. Schedule keyword matches (+1 each)
      4. Header count (+1 each)

    Navigation menus (< 5 rows and >= 3 nav keywords) are skipped.
    """
    nav_markers = ["eventos", "inicial", "sumários", "avaliações", "comunicações"]
    keywords = [
        "horário", "horario", "turma", "sala", "disciplina",
        "docente", "aula", "dia", "hora",
    ]

    best_score = -1
    best_table = None

    for table in soup.find_all("table"):
        cells = table.find_all(["td", "th"])
        if len(cells) < 10:
            continue

        rows = table.find_all("tr")
        text = _normalize(table.get_text(" "))

        if len(rows) < 5:
            nav_hits = sum(1 for m in nav_markers if m in text.lower())
            if nav_hits >= 3:
                continue

        score = 0
        dates_found = _date_re.findall(text)
        if dates_found:
            score += 1000 + len(dates_found) * 10

        cell_count = len(cells)
        if cell_count > 100:
            score += 100
        elif cell_count > 50:
            score += 50

        score += sum(1 for kw in keywords if kw.lower() in text.lower())
        score += len(table.find_all("th"))

        if score > best_score:
            best_score = score
            best_table = table

    return best_table


def _extract_events(matrix: list[list[str]]) -> list[dict[str, str]]:
    """Extract raw schedule events from a 2D matrix.

    Returns a list of {"date": str, "time": str, "text": str} dicts
    using the same two-pass algorithm as the legacy utils_html.py.
    """
    if not matrix:
        return []

    # First pass: find date header columns
    date_headers: dict[int, str] = {}
    for row in matrix:
        for col_idx, cell in enumerate(row):
            if isinstance(cell, str) and _date_re.search(cell):
                date_headers[col_idx] = _normalize(cell)

    if not date_headers:
        logger.warning("No date headers found in matrix")
        return []

    # Amendment A2: time markers only appear before the first date column
    first_date_col = min(date_headers.keys())

    events: list[dict[str, str]] = []
    current_time: str | None = None

    for row in matrix:
        # Look for a time marker in the pre-date columns
        for col_idx, cell in enumerate(row):
            if col_idx >= first_date_col:
                break
            if isinstance(cell, str):
                norm = _normalize(cell)
                if _time_re.match(norm):
                    current_time = norm.replace(" ", "")
                    break

        if not date_headers or current_time is None:
            continue

        for col_idx, cell in enumerate(row):
            if not isinstance(cell, str):
                continue
            text = _normalize(cell)
            if not text:
                continue
            if _date_re.search(text) or _time_re.match(text):
                continue

            date_val = date_headers.get(col_idx, "")
            if not date_val:
                continue

            events.append({"date": date_val, "time": current_time, "text": text})

    return events


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_schedule_by_date(
    html: str,
) -> dict[str, list[dict[str, Any]]]:
    """Parse Inovar schedule HTML and return a structured schedule dict.

    Args:
        html: Full page HTML as returned by Playwright page.content().

    Returns:
        Dict keyed by "dd-mm-yyyy" with lists of class-slot dicts:
        {"class_name": str, "inovar_classroom": str, "hour": int}

        Returns an empty dict if no schedule events are found
        (holiday week, parse failure, etc.).
    """
    soup = BeautifulSoup(html, "lxml")
    table = _find_schedule_table(soup)

    if table is None:
        logger.warning("No schedule table found in HTML (%d chars)", len(html))
        return {}

    matrix = _table_to_matrix(table)
    raw_events = _extract_events(matrix)

    if not raw_events:
        logger.warning("No events extracted from schedule table")
        return {}

    schedule: dict[str, list[dict[str, Any]]] = {}
    skipped = 0

    for event in raw_events:
        date_str   = event.get("date", "").strip()
        time_str   = event.get("time", "").strip()
        class_text = event.get("text", "").strip()

        if not date_str or not time_str or not class_text:
            skipped += 1
            continue

        try:
            time_int = int(time_str)
            hour     = (time_int // 100) * 100  # normalise to slot boundary
        except ValueError:
            skipped += 1
            continue

        # Guard against row-index label leakage (legacy Amendment A3)
        if not (700 <= hour <= 1700):
            skipped += 1
            continue

        class_name, classroom = _convert_class(class_text)

        if date_str not in schedule:
            schedule[date_str] = []

        schedule[date_str].append(
            {
                "class_name":       class_name,
                "inovar_classroom": classroom,
                "hour":             hour,
            }
        )

    if skipped:
        logger.debug("Skipped %d incomplete/invalid events", skipped)

    logger.info(
        "Parsed schedule: %d dates, %d total slots",
        len(schedule),
        sum(len(v) for v in schedule.values()),
    )
    return schedule
