# Inovar Schedule Extraction — Architecture, Integration Design & TDD Plan

This document has two parts.

Part 1 is the original architecture trace of the legacy `prof` CLI project —
how it navigates Inovar and extracts schedule data. It is the reference
implementation for understanding what the real system looks like.

Part 2 is the design and TDD plan for integrating that capability into this
FastAPI service (`prof-horario`), written after evaluating a published
reflection on the topic. It records where that reflection is accurate, where
it is wrong about the real system, and what decisions actually need to be made.

---

## Part 1 — Legacy Architecture & Data Flow

### The Typer Command

```
python cli.py data-extract get-inovar-schedule
```

This is the primary entry point. It fetches the current or next week's
schedule from Inovar and stores it as a CSV. A companion command exists:

```
python cli.py data-extract get-inovar-next-day-schedule
```

That one runs the same fetch but filters the result down to tomorrow's date only.

---

### Full Data Flow — Layer by Layer

#### Layer 1 — `cli.py` (entry gate)

`cli.py` registers `data_extract_app` under the group name `data-extract`:

```python
_add_typer_group(data_extract_app, name="data-extract")
```

The sub-command `get-inovar-schedule` lives in `commands/data_extract.py`.

---

#### Layer 2 — `commands/data_extract.py` (Typer command, line 98)

`get_Inovar_schedule()` orchestrates the high-level steps:

1. Instantiate `BrowserService("epralima.inovarmais.com/alunos/Inicial.wgx")`
2. Call `Inovar.open_sumario_inovar()` → returns `{ "html": "..." }`
3. Call `Inovar.extract_schedule_by_date(html=html)` → returns `{ "dd-mm-yyyy": [...] }`
4. Persist to CSV via `save_schedule_table("schedule/inovar_current.csv", schedule)`
5. Save metadata (`last_fetch`, `status`, `total_dates`) via `save_var()`
6. Print final JSON to stdout

---

#### Layer 3 — `commands/services/browser_services.py` :: `BrowserService.open_sumario_inovar()` (line 844)

This is the **Python → TypeScript bridge**. It does exactly one thing:

```python
from commands.services.typescript import TypescriptService
service = TypescriptService()
result = service.run_script("getHtmlHorario")
```

---

#### Layer 4 — `commands/services/typescript.py` :: `TypescriptService.run_script()`

Shells out via `subprocess`:

```
npx tsx ~/typescript/getHtmlHorario.ts
```

Captures `stdout` as JSON and returns it as a Python `dict`. This is the
**Python ↔ TypeScript seam** — stdout is the entire contract between runtimes.

---

#### Layer 5 — `typescript/getHtmlHorario.ts` (week-routing logic)

Checks the current day of the week and routes accordingly:

| Day              | Inovar state                            | Action              |
|------------------|-----------------------------------------|---------------------|
| Fri (5), Sat (6) | Still showing the current (past) week   | Fetch **next** week |
| Mon–Thu, Sun     | Already showing the upcoming block      | Fetch **this** week |

Delegates to either `getHtmlNextWeekHorario()` or `getHtmlThisWeekHorario()`.

---

#### Layer 6 — `typescript/getHtmlThisWeekHorario.ts` / `getHtmlNextWeekHorario.ts`

Both follow the same structure using `BrowserManagement`:

1. `chromium.launch({ headless: true })`
2. `inovar.openInovar()` — login
3. `inovar.openInovarSumario()` — navigate to Área Docente > Sumários
4. *(next-week only)* `inovar.openInovarNextWeekSumario()` — click "Semana Seguinte"
5. `page.waitForSelector('text=/\d{2}-\d{2}-\d{4}/')` — guard: date cells visible
6. `inovar.getFullHtml()` — `page.content()`, validates date pattern is present
7. `console.log(JSON.stringify({ success: true, html, html_length, timestamp }))`

---

#### Layer 7 — `typescript/browser_management.ts` :: `BrowserManagement`

**`openInovar()`**

- URL: `https://epralima.inovarmais.com/alunos/Inicial.wgx`
- Credentials from: `INOVAR_USERNAME` / `INOVAR_PASSWORD` env vars
- Waits for `networkidle` (Gizmox WebGUI framework bootstrap — thin shell
  that loads the full UI after network idle)
- Fills username into `#TRG_62`, password into `#TRG_61`
- Submits with `Enter` — the "Entrar" button gets a new dynamic ID every
  session, so `Enter` on the password field is the only reliable method
- Waits for `div[id^="VWG_"]` to confirm the main app UI has rendered

**`openInovarSumario()`**

- Clicks `#VWG_116` (Área Docente) with fallback
  `[data-vwgtype="control"].cda3 >> text="Área Docente"`
- Clicks `#VWG_172` (Sumários) with fallback
  `[data-vwgtype="control"].cda3 >> text="Sumários"`

**`openInovarNextWeekSumario()`**

- Reads the current week label from `#VWG_182` / `[title="Alterar Semana"]`
- Clicks `#VWG_189` / `[title="Semana Seguinte"]`
- Re-reads the week label and asserts it changed — navigation guard to catch
  silent failures

**`getFullHtml()`**

- `page.content()` — full DOM as a string
- Hard guard: throws if the HTML does not contain `/\d{2}-\d{2}-\d{4}/`
  (no date cells = page did not load correctly)

---

#### Layer 8 — Back in Python: `extract_schedule_by_date()`

Takes the raw HTML returned by the TS layer and structures it:

1. `HtmlUtils(html).find_schedule_table()` — BeautifulSoup, locates the table
   with the most date-header cells (score = `date_rows×10 + time_rows + row_count`)
2. `utils.table_to_matrix(table)` — flattens `rowspan`/`colspan` into a plain 2-D grid
3. `utils.extract_schedule_events(matrix)` — walks rows, tracks the current time
   slot (3–4 digit cells like `800`, `900`), emits `{ date, time, text }` events
4. Filters out hours outside `700–1700` (guard against row-index-label leakage)
5. `TeacherDataConverter().convert_inovar_class(text)` — maps Inovar's raw
   classroom string (e.g. `"MATEM - 11 N1 / 11 N2 - AV-08"`) to
   `{ class_name, inovar_classroom }`
6. Returns `{ "dd-mm-yyyy": [{ class_name, inovar_classroom, hour }, ...], ... }`

---

### Artifact Chain

```
cli.py
  -> commands/data_extract.py              Typer: get-inovar-schedule
  -> commands/services/browser_services.py BrowserService.open_sumario_inovar()
  -> commands/services/typescript.py       TypescriptService: npx tsx shell-out
  -> typescript/getHtmlHorario.ts          day-of-week router
  -> typescript/getHtmlThisWeekHorario.ts  OR
     typescript/getHtmlNextWeekHorario.ts
  -> typescript/browser_management.ts      Playwright: login + nav + HTML capture
  <- stdout JSON { success, html }
  -> commands/services/browser_services.py extract_schedule_by_date()
  -> commands/utils/utils_html.py          HTML -> matrix -> events
  -> commands/utils/utils_teacher.py       TeacherDataConverter: class name normalisation
  -> commands/utils/utils_csv.py           save_schedule_table -> variables/schedule/
```

---

### Key Design Notes (Legacy)

**The stdout contract.** `TypescriptService.run_script()` at `typescript.py:11`
is the only place Python hands off to Playwright. The `npx tsx` subprocess
writes a single JSON object to stdout. That payload is the entire interface —
no shared DB, no sockets, just a pipe.

**Gizmox WebGUI quirks.** Inovar runs a legacy Gizmox WebGUI framework. The
login page is a ~2 KB shell that bootstraps the full UI after `networkidle`.
DOM element IDs (`#TRG_62`, `#VWG_116`) are assigned dynamically and can
drift between sessions or server updates. Every selector carries a stable
text-content or `title`-attribute fallback.

**Week-navigation strategy.** On Friday and Saturday Inovar's Sumários opens
on the current (past) week, so one "Semana Seguinte" click is required. On
all other days the page opens on the upcoming block. `getHtmlHorario.ts`
encodes this with `today === 5 || today === 6`.

**Schedule table heuristic.** Multiple `<table>` elements are present.
`HtmlUtils.find_schedule_table()` scores each table by
`date_rows × 10 + time_rows + total_rows` and picks the winner. Time slots
appear as bare 3–4 digit integers (`800`, `900`); class names appear in
adjacent cells aligned to the same column as their date header.

---

---

## Part 2 — FastAPI Integration Design & TDD Plan

This section was written after evaluating a published reflection that proposed
wiring the Inovar Playwright scraper into this FastAPI service. The reflection
is evaluated honestly below before any plan is derived from it.

---

### Evaluation of the Reflection

**What it gets right:**

- Moving from a simulated worker to real browser automation is the correct
  direction. The existing CRUD endpoints have no way to populate themselves
  without a data source.
- Using `async_playwright()` instead of the synchronous variant is correct
  for FastAPI. The legacy CLI used sync Playwright via a subprocess shell-out;
  the FastAPI service should call `async_playwright()` directly so navigation
  I/O (network waits inside Chromium) releases the event loop.
- The adapter pattern — a dedicated `app/services/inovar_scraper.py` file
  that keeps selectors and browser logic away from the router — matches the
  layered architecture already in this project.
- Raising a `DomainError` subclass (specifically using the existing
  `NotFoundError`) when a resource is absent is exactly right. The global
  handler in `app/errors.py` already dispatches polymorphically on
  `DomainError`.

**What it gets wrong about the real system:**

- **No parametric URL exists.** The reflection proposes
  `f"{self.base_url}/horarios/detalhes?id={target_id}"`. The real Inovar
  system (Gizmox WebGUI) serves everything from
  `https://epralima.inovarmais.com/alunos/Inicial.wgx`. There is no REST
  endpoint for individual schedules. The entire navigation is click-driven
  through the Gizmox SPA. You cannot skip to a schedule by ID in the URL.

- **Authentication is mandatory, never optional.** The reflection asks "does
  it authenticate via a login form, or can it fetch schedules via a direct URL
  query?" The answer from the real code is: you must authenticate every time.
  The framework renders nothing without a valid session. There is no bypass.

- **The `target_id` concept does not map to anything in Inovar.** The legacy
  system scrapes the teacher's own weekly timetable — a fixed navigation path,
  not a parameterised resource. Inovar does not expose schedules as
  queryable objects with IDs. The scraper always retrieves one week at a time
  for the authenticated user.

- **The proposed CSS selectors are fiction.** `.class-title-selector`,
  `.date-selector`, `.time-range-selector` do not exist in Inovar. The real
  DOM identifiers are:
  - Login: `#TRG_62` (username), `#TRG_61` (password)
  - Navigation: `#VWG_116` (Área Docente), `#VWG_172` (Sumários),
    `#VWG_189` (Semana Seguinte)
  - Schedule data: extracted from HTML table structure by date pattern
    `\d{2}-\d{2}-\d{4}` and time pattern `\d{3,4}`, not by CSS class

- **The data shape does not map directly to `HorarioCreateSchema`.** The
  reflection returns `{ class_name, lesson_date, time_span, status }`. The
  real Inovar output is `{ class_name, inovar_classroom, hour }` where `hour`
  is an integer (`800` = 08:00). The existing schema requires `start_time`,
  `end_time`, `classroom`, `description`, `lesson_date` — several of these
  need explicit transformation that the reflection omits.

---

### Architectural Decisions

These are the real decisions that need to be made before writing any code.

---

**Decision 1: Browser lifecycle — per-request vs. long-lived**

The reflection spawns a new browser per request inside `async with async_playwright()`.
That is safe and easy to test, but browser launch takes ~500 ms per call.
For a service that is called infrequently (once per day or week to sync the
timetable), per-request launch is acceptable and keeps the implementation
simple. A long-lived browser pool (FastAPI `lifespan` dependency) would be
the right choice if sync were called frequently or concurrently.

Decision: **per-request for the first implementation.** The service interface
must be designed so the lifecycle strategy can be swapped without touching
the router or tests — the scraper class should own the launch/close cycle.

---

**Decision 2: What does the sync endpoint look like?**

This is not a `GET /horarios/{id}` — it is a write operation that triggers
a batch ingestion. The natural shape is:

```
POST /api/v1/horarios/sync
```

The endpoint calls the scraper, maps the results to the existing
`HorarioCreateSchema`, deduplicates against the repository using the existing
`exists()` check, and inserts new records. It returns a summary:
`{ inserted: N, skipped: N, errors: [...] }`.

No new model or table is needed. The existing `Horario` table is the target.

---

**Decision 3: Data mapping — Inovar output to HorarioCreateSchema**

The Inovar scraper returns:

```python
{
    "dd-mm-yyyy": [
        { "class_name": "11B", "inovar_classroom": "MATEM - 11 N1", "hour": 800 },
        ...
    ]
}
```

Mapping to `HorarioCreateSchema`:

| Inovar field        | Schema field    | Transformation                              |
|---------------------|-----------------|---------------------------------------------|
| date key `dd-mm-yyyy` | `lesson_date` | `datetime.strptime(d, "%d-%m-%Y").date()`   |
| `hour` int (e.g. 800) | `start_time`  | `time(hour=8, minute=0)`                    |
| `hour` + 50 min       | `end_time`    | `time(hour=8, minute=50)` — 50 min default  |
| `class_name`         | `class_name`   | direct                                       |
| `inovar_classroom`   | `classroom`    | direct                                       |
| *(none)*             | `description`  | synthesised: `f"Aula de {class_name}"`      |
| *(none)*             | `module_ref`   | `None`                                       |

The 50-minute class duration is a domain assumption (the standard lesson
block at the institution). This must be a named constant, not a magic number.

This mapping is a pure function — no I/O, no mocking required.
It is the easiest layer to test first.

---

**Decision 4: Credentials**

`INOVAR_USERNAME` and `INOVAR_PASSWORD` must be injectable as environment
variables. The existing `app/config.py` is empty — it needs a
`pydantic-settings` `Settings` class to hold these alongside `DATABASE_URL`.
The scraper service receives them as constructor arguments so tests can
inject arbitrary values without touching `os.environ`.

---

**Decision 5: What errors does the scraper raise?**

The scraper can fail in distinct ways that callers need to distinguish:

- `InovarAuthError` — credentials rejected by the portal (HTTP 401 equivalent)
- `InovarNavigationError` — login succeeded but a navigation step timed out
  or the week label did not change after clicking "Semana Seguinte"
- `InovarEmptyScheduleError` — page loaded but no schedule events were found
  (valid for holiday weeks; callers must not treat this as a crash)

All three extend `DomainError` so the global handler picks them up
automatically. `InovarEmptyScheduleError` gets `status_code = 200` with a
custom `error_code`; the sync endpoint interprets it as `{ inserted: 0, skipped: 0 }`.

---

### TDD Plan

The plan follows strict RED → GREEN → REFACTOR. Each step names the test
first, then the implementation that makes it pass. No implementation file is
opened before its driving test exists.

---

#### Step 0 — Domain exceptions (no mocking needed)

**File:** `tests/test_inovar_exceptions.py`

Tests to write:

- `InovarAuthError` is a subclass of `DomainError` and carries
  `status_code = 401`, `error_code = "INOVAR_AUTH_ERROR"`
- `InovarNavigationError` carries `status_code = 502`,
  `error_code = "INOVAR_NAVIGATION_ERROR"`
- `InovarEmptyScheduleError` carries `status_code = 200`,
  `error_code = "INOVAR_EMPTY_SCHEDULE"`
- All three are caught by the existing `domain_error_handler` in `app/errors.py`
  (use `TestClient` with a dummy route that raises each one and assert the
  JSON shape matches what `init_error_handlers` produces)

**Implementation:** add three classes to `app/exceptions.py`.

Validation: `pytest tests/test_inovar_exceptions.py` passes with no imports
of Playwright or any external service.

---

#### Step 1 — Data mapping (pure function, no mocking needed)

**File:** `tests/test_inovar_mapper.py`

Tests to write:

- Given `{ "20-06-2026": [{ class_name: "11B", inovar_classroom: "MATEM", hour: 800 }] }`,
  `map_inovar_to_horarios()` returns a list of one `HorarioCreateSchema`-compatible
  dict with `lesson_date=date(2026,6,20)`, `start_time=time(8,0)`,
  `end_time=time(8,50)`, `class_name="11B"`, `classroom="MATEM"`,
  `description="Aula de 11B"`, `module_ref=None`
- Multiple dates produce one item per class per date
- An empty schedule dict produces an empty list
- A class with `hour=900` produces `start_time=time(9,0)`, `end_time=time(9,50)`
- `map_inovar_to_horarios()` does not raise for any well-formed input

**Implementation:** `app/services/inovar_mapper.py` — a single pure function,
no class, no I/O. This is intentionally the simplest possible artefact.

Validation: `pytest tests/test_inovar_mapper.py` passes. Zero external calls.

---

#### Step 2 — Scraper service (mock async_playwright)

**File:** `tests/test_inovar_scraper.py`

The scraper cannot be tested against the live Inovar portal in CI. Tests mock
`async_playwright` and assert that the service calls the right methods in the
right order.

Tests to write:

- `scrape_next_week()` launches a headless Chromium browser, navigates to
  `https://epralima.inovarmais.com/alunos/Inicial.wgx`, fills `#TRG_62` with
  the username, fills `#TRG_61` with the password, presses `Enter`, clicks
  `#VWG_116` and `#VWG_172` (asserting fallback selectors via the mock), and
  returns a dict with an `html` key
- When the page has no date cells after navigation, `InovarNavigationError`
  is raised
- When `#TRG_62` is not found (login form did not appear), `InovarAuthError`
  is raised with a message that includes "login form"
- When the schedule HTML is valid but `extract_schedule_by_date()` returns
  empty, `InovarEmptyScheduleError` is raised — not a generic 500

Mocking strategy: patch `playwright.async_api.async_playwright` with an
`AsyncMock`. The mock's `__aenter__` returns a mock `Playwright` object whose
`chromium.launch()` returns a mock `Browser` that has `new_context()` and
`new_page()`. Assert `.fill()`, `.click()`, `.press()`, `.wait_for_selector()`
are called with the correct arguments.

**Implementation:** `app/services/inovar_scraper.py` — `InovarScraperService`
class with `async def scrape_next_week() -> dict` and
`async def scrape_this_week() -> dict`. Credentials injected via constructor.

Validation: `pytest tests/test_inovar_scraper.py` passes with no network
access, no env vars, no Playwright binaries required.

---

#### Step 3 — Sync endpoint (mock scraper service)

**File:** `tests/test_sync_endpoint.py`

Tests to write:

- `POST /api/v1/horarios/sync` with a mocked scraper that returns two new
  records → HTTP 200 with `{ inserted: 2, skipped: 0, errors: [] }`
- One of those records already exists (`repo.exists()` returns `True` for it)
  → `{ inserted: 1, skipped: 1, errors: [] }`
- All records already exist → `{ inserted: 0, skipped: 2, errors: [] }`
- Mocked scraper raises `InovarAuthError` → HTTP 401 with the standard
  `{ success: false, error: { code: "INOVAR_AUTH_ERROR", ... } }` shape
- Mocked scraper raises `InovarEmptyScheduleError` → HTTP 200 with
  `{ inserted: 0, skipped: 0, errors: [] }` (not an error to the caller)
- Mocked scraper raises `InovarNavigationError` → HTTP 502

Mocking strategy: use `app.dependency_overrides` to inject a mock
`InovarScraperService` the same way existing tests inject `HorarioRepository`.

**Implementation:** `POST /api/v1/horarios/sync` handler in
`app/routers/horario.py`. The handler depends on `InovarScraperService` via a
FastAPI `Depends()`. It calls `scraper.scrape_next_week()`, pipes the result
through `map_inovar_to_horarios()`, and loops through the mapped items with
the existing `repo.exists()` / `repo.add()` pattern.

Validation: `pytest tests/test_sync_endpoint.py` passes. Zero network access.
The entire existing test suite (`pytest`) still passes — no regressions.

---

#### Step 4 — Config wiring

**File:** `tests/test_config.py`

Tests to write:

- When `INOVAR_USERNAME` and `INOVAR_PASSWORD` are set in the environment,
  `get_settings()` returns a `Settings` object with those values
- When either variable is absent, `get_settings()` raises `ValidationError`
  (Pydantic's own error — no custom exception needed here)
- The `InovarScraperService` constructed from `Depends(get_settings)` receives
  the correct credentials

**Implementation:** populate `app/config.py` with a `pydantic-settings`
`Settings` class. Add `get_settings()` as a cached dependency
(`@lru_cache` or `functools.cache`). Update `inovar_scraper.py` to accept
a `Settings` object instead of raw strings.

Validation: `pytest tests/test_config.py` passes.

---

#### Step 5 — Dockerfile and requirements

No new test needed here — this is infrastructure.

Add to `requirements.txt`:

```
playwright>=1.44.0
```

Add to `Dockerfile` (after `pip install`):

```dockerfile
RUN playwright install --with-deps chromium
```

Validation: `docker compose build` succeeds and
`docker compose run app python -c "from playwright.async_api import async_playwright; print('ok')`
prints `ok`.

---

### What NOT to Do (Pitfalls from the Reflection)

- **Do not model the sync operation as a `GET` endpoint.** It writes to the
  database and triggers a browser launch. `POST` is the correct verb.
- **Do not hard-code the Inovar URL in the router.** It belongs in `Settings`.
- **Do not assume the Gizmox IDs are stable.** `#TRG_62` and `#VWG_116` can
  change with a server update. Every selector in the scraper needs the same
  primary-selector-with-fallback pattern used in `browser_management.ts`.
- **Do not skip the empty-schedule case.** An empty result from Inovar is a
  legitimate outcome during holiday weeks. It must not become a 500.
- **Do not launch a browser inside the router.** The router calls the service.
  The service owns the browser lifecycle. This is required for testability.
- **Do not synthesise `end_time` with a magic number.** Define
  `LESSON_DURATION_MINUTES = 50` as a named constant in `inovar_mapper.py`.
  If the institution changes lesson duration, there is one place to update.
