"""
End-to-end tests for the prof-horario microservice running at http://localhost:8000.

These tests hit the REAL service with a REAL Postgres database.
They are tagged with @pytest.mark.e2e and intentionally separated from unit tests.

Run with:
  pytest tests/test_e2e.py -v -m e2e

Prerequisites:
  - docker compose up -d (service + db must be healthy)
  - The network must reach http://localhost:8000

Test scope:
  - /health           — service liveness
  - POST/GET/DELETE   — full CRUD lifecycle in real DB
  - GET /export/csv   — CSV shape compatible with sync-to-outlook.ps1
  - NOT /sync         — avoided: would launch real Playwright against Inovar

Cleanup strategy:
  Every test that inserts records deletes them by ID at the end.
  That way the DB is left clean after the suite.
"""
import csv
import io
import time
import pytest
import httpx
from datetime import date, timedelta

BASE = "http://localhost:8000"
API  = f"{BASE}/api/v1/horarios"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _payload(**overrides):
    """Valid HorarioCreateSchema payload for E2E insertion.

    Uses a run-unique class_name so parallel or repeated test runs never collide
    on the (class_name, lesson_date, start_time) unique constraint.
    """
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    # Unique per test run — avoids duplicate-key collisions on repeated runs.
    run_tag = str(int(time.time() * 1000))[-6:]
    defaults = {
        "class_name":  f"E2E-{run_tag}",
        "classroom":   "AV-TEST",
        "module_ref":  None,
        "description": "E2E automated test lesson",
        "lesson_date": tomorrow,
        "start_time":  "08:50",
        "end_time":    "09:40",
    }
    defaults.update(overrides)
    return defaults


def _delete(record_id: str):
    httpx.delete(f"{API}/{record_id}", timeout=10)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_health_returns_healthy():
    resp = httpx.get(f"{BASE}/health", timeout=5)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"


# ---------------------------------------------------------------------------
# CRUD lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_create_horario_returns_201_and_id():
    resp = httpx.post(API, json=_payload(), timeout=10)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert "id" in body
    _delete(body["id"])


@pytest.mark.e2e
def test_list_horarios_returns_200_list():
    resp = httpx.get(API, timeout=10)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.e2e
def test_get_horario_by_id():
    p = _payload()
    created = httpx.post(API, json=p, timeout=10)
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]

    resp = httpx.get(f"{API}/{record_id}", timeout=10)
    assert resp.status_code == 200
    body = resp.json()
    assert body["class_name"] == p["class_name"]
    assert body["classroom"]  == "AV-TEST"
    _delete(record_id)


@pytest.mark.e2e
def test_get_horario_unknown_id_returns_404():
    fake_id = "00000000-0000-0000-0000-000000000000"
    resp = httpx.get(f"{API}/{fake_id}", timeout=10)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "HORARIO_NOT_FOUND"


@pytest.mark.e2e
def test_delete_horario_returns_204():
    created = httpx.post(API, json=_payload(), timeout=10)
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]

    resp = httpx.delete(f"{API}/{record_id}", timeout=10)
    assert resp.status_code == 204
    assert resp.content == b""


@pytest.mark.e2e
def test_delete_nonexistent_returns_404():
    fake_id = "00000000-0000-0000-0000-000000000001"
    resp = httpx.delete(f"{API}/{fake_id}", timeout=10)
    assert resp.status_code == 404


@pytest.mark.e2e
def test_create_duplicate_returns_409():
    """Same class+date+start_time must be rejected as a duplicate."""
    # Both POSTs need the exact same payload — build once and reuse.
    p = _payload()
    first = httpx.post(API, json=p, timeout=10)
    assert first.status_code == 201, first.text
    record_id = first.json()["id"]

    try:
        second = httpx.post(API, json=p, timeout=10)
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "DUPLICATE_HORARIO"
    finally:
        _delete(record_id)


@pytest.mark.e2e
def test_create_with_invalid_time_order_returns_422():
    """start_time >= end_time must be rejected with 422."""
    bad = _payload(start_time="10:00", end_time="09:00")
    resp = httpx.post(API, json=bad, timeout=10)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# CSV export — the Outlook integration bridge
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_export_csv_returns_200_csv_content_type():
    resp = httpx.get(f"{API}/export/csv", timeout=10)
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("content-type", "")


@pytest.mark.e2e
def test_export_csv_header_matches_ps1_schema():
    """The CSV must have exactly the columns sync-to-outlook.ps1 expects."""
    expected_cols = ["date", "class_name", "inovar_classroom", "hour", "fetched_at"]
    resp = httpx.get(f"{API}/export/csv", timeout=10)
    assert resp.status_code == 200
    first_line = resp.text.splitlines()[0]
    assert first_line == ",".join(expected_cols), (
        f"CSV header mismatch.\n  Expected: {','.join(expected_cols)}\n  Got:      {first_line}"
    )


@pytest.mark.e2e
def test_export_csv_row_values_are_correct():
    """Insert a known record, export CSV, verify every field the PS1 reads."""
    run_tag = str(int(time.time() * 1000))[-6:]
    csv_class = f"E2ECSV-{run_tag}"
    p = _payload(class_name=csv_class, classroom="AV-99",
                 start_time="08:50", end_time="09:40")
    created = httpx.post(API, json=p, timeout=10)
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]

    try:
        resp = httpx.get(f"{API}/export/csv", timeout=10)
        assert resp.status_code == 200

        rows = list(csv.DictReader(io.StringIO(resp.text)))
        our_rows = [r for r in rows if r["class_name"] == csv_class and r["inovar_classroom"] == "AV-99"]
        assert len(our_rows) == 1, f"Expected 1 row for {csv_class}, got {len(our_rows)}"

        row = our_rows[0]
        # date in dd-mm-yyyy — the PS1 does: $dateParts = $entry.date -split '-'
        lesson_date_dd_mm_yyyy = (date.today() + timedelta(days=1)).strftime("%d-%m-%Y")
        assert row["date"] == lesson_date_dd_mm_yyyy, f"date mismatch: {row['date']!r}"
        # hour code: time(8,50) -> 850
        assert row["hour"] == "850", f"hour mismatch: {row['hour']!r}"
        assert row["fetched_at"] != ""
    finally:
        _delete(record_id)


@pytest.mark.e2e
def test_export_csv_content_disposition():
    resp = httpx.get(f"{API}/export/csv", timeout=10)
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "horario.csv" in cd


# ---------------------------------------------------------------------------
# Sync endpoint — request validation only (no real Inovar call)
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_sync_invalid_week_returns_422():
    """week=badvalue must be rejected before any Playwright is launched."""
    resp = httpx.post(f"{API}/sync?week=badvalue", timeout=10)
    assert resp.status_code == 422
