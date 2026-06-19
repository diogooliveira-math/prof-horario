# Inovar Schedule Extraction — Architecture & Data Flow

This document traces the full pipeline used in the legacy `prof` CLI project
to reach inside the Inovar platform, capture the weekly schedule HTML, and
turn it into structured lesson data. It is preserved here as a reference for
rebuilding this capability inside `prof-horario`.

---

## The Typer Command

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

## Full Data Flow — Layer by Layer

### Layer 1 — `cli.py` (entry gate)

`cli.py` registers `data_extract_app` under the group name `data-extract`:

```python
_add_typer_group(data_extract_app, name="data-extract")
```

The sub-command `get-inovar-schedule` lives in `commands/data_extract.py`.

---

### Layer 2 — `commands/data_extract.py` (Typer command, line 98)

`get_Inovar_schedule()` orchestrates the high-level steps:

1. Instantiate `BrowserService("epralima.inovarmais.com/alunos/Inicial.wgx")`
2. Call `Inovar.open_sumario_inovar()` → returns `{ "html": "..." }`
3. Call `Inovar.extract_schedule_by_date(html=html)` → returns `{ "dd-mm-yyyy": [...] }`
4. Persist to CSV via `save_schedule_table("schedule/inovar_current.csv", schedule)`
5. Save metadata (`last_fetch`, `status`, `total_dates`) via `save_var()`
6. Print final JSON to stdout

---

### Layer 3 — `commands/services/browser_services.py` :: `BrowserService.open_sumario_inovar()` (line 844)

This is the **Python → TypeScript bridge**. It does exactly one thing:

```python
from commands.services.typescript import TypescriptService
service = TypescriptService()
result = service.run_script("getHtmlHorario")
```

---

### Layer 4 — `commands/services/typescript.py` :: `TypescriptService.run_script()`

Shells out via `subprocess`:

```python
npx tsx ~/typescript/getHtmlHorario.ts
```

Captures `stdout` as JSON and returns it as a Python `dict`. This is the
**Python ↔ TypeScript seam** — stdout is the entire contract between runtimes.

---

### Layer 5 — `typescript/getHtmlHorario.ts` (week-routing logic)

Checks the current day of the week and routes accordingly:

| Day         | Inovar state                           | Action                  |
|-------------|----------------------------------------|-------------------------|
| Fri (5), Sat (6) | Still showing the current week  | Fetch **next** week     |
| Mon–Thu, Sun    | Already showing the upcoming block | Fetch **this** week     |

Delegates to either `getHtmlNextWeekHorario()` or `getHtmlThisWeekHorario()`.

---

### Layer 6 — `typescript/getHtmlThisWeekHorario.ts` / `getHtmlNextWeekHorario.ts`

Both follow the same structure using `BrowserManagement`:

1. `chromium.launch({ headless: true })`
2. `inovar.openInovar()` — login
3. `inovar.openInovarSumario()` — navigate to Área Docente > Sumários
4. *(next-week only)* `inovar.openInovarNextWeekSumario()` — click "Semana Seguinte"
5. `page.waitForSelector('text=/\d{2}-\d{2}-\d{4}/')` — guard: date cells visible
6. `inovar.getFullHtml()` — `page.content()`, validates date pattern is present
7. `console.log(JSON.stringify({ success: true, html, html_length, timestamp }))`

---

### Layer 7 — `typescript/browser_management.ts` :: `BrowserManagement` (the Playwright driver)

#### `openInovar()`

- URL: `https://epralima.inovarmais.com/alunos/Inicial.wgx`
- Credentials from: `INOVAR_USERNAME` / `INOVAR_PASSWORD` env vars
- Waits for `networkidle` (Gizmox WebGUI framework bootstrap — thin shell that
  loads the full UI after network idle)
- Fills username into `#TRG_62`, password into `#TRG_61`
- Submits with `Enter` — the "Entrar" button gets a new dynamic ID every
  session, so `Enter` on the password field is the only reliable method
- Waits for `div[id^="VWG_"]` to confirm the main app UI has rendered

#### `openInovarSumario()`

- Clicks `#VWG_116` (Área Docente) with fallback
  `[data-vwgtype="control"].cda3 >> text="Área Docente"`
- Clicks `#VWG_172` (Sumários) with fallback
  `[data-vwgtype="control"].cda3 >> text="Sumários"`

#### `openInovarNextWeekSumario()`

- Reads the current week label from `#VWG_182` / `[title="Alterar Semana"]`
- Clicks `#VWG_189` / `[title="Semana Seguinte"]`
- Re-reads the week label and asserts it changed — navigation guard to catch
  silent failures

#### `getFullHtml()`

- `page.content()` — full DOM as a string
- Hard guard: throws if the HTML does not contain the pattern `/\d{2}-\d{2}-\d{4}/`
  (no date cells = page did not load correctly)

---

### Layer 8 — Back in Python: `extract_schedule_by_date()`

Takes the raw HTML returned by the TS layer and structures it:

1. `HtmlUtils(html).find_schedule_table()` — BeautifulSoup, locates the table
   with the most date-header cells (heuristic: score = date_rows×10 + time_rows + row_count)
2. `utils.table_to_matrix(table)` — flattens `rowspan`/`colspan` into a plain 2-D grid
3. `utils.extract_schedule_events(matrix)` — walks rows, tracks the current time
   slot (3–4 digit cells like `800`, `900`), emits `{ date, time, text }` events
4. Filters out hours outside `700–1700` (guard against row-index-label leakage)
5. `TeacherDataConverter().convert_inovar_class(text)` — maps Inovar's raw
   classroom string (e.g. `"MATEM - 11 N1 / 11 N2 - AV-08"`) to a clean
   `{ class_name, inovar_classroom }` dict
6. Returns `{ "dd-mm-yyyy": [{ class_name, inovar_classroom, hour }, ...], ... }`

---

## Artifact Chain (files that matter)

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
  -> commands/utils/utils_html.py          HTML → matrix → events
  -> commands/utils/utils_teacher.py       TeacherDataConverter: class name normalisation
  -> commands/utils/utils_csv.py           save_schedule_table → variables/schedule/
```

---

## Key Design Notes

**The stdout contract.** `TypescriptService.run_script()` in `typescript.py:11`
is the only place Python hands off to Playwright. The `npx tsx` subprocess
writes a single JSON object to stdout (`{ success, html, html_length, timestamp }`).
That stdout payload is the entire interface — no shared DB, no sockets, just a pipe.

**Gizmox WebGUI quirks.** Inovar runs a legacy Gizmox WebGUI framework. The
login page is a ~2 KB shell that bootstraps the full UI after `networkidle`.
DOM element IDs (e.g. `#TRG_62`, `#VWG_116`) are assigned dynamically by the
framework and *can* drift between sessions or updates. Every selector in
`browser_management.ts` therefore carries a stable fallback (text content or
`title` attribute) so navigation survives an ID change.

**Week-navigation strategy.** On Friday and Saturday Inovar's Sumários page
opens on the *current* (past) week, so the code must click "Semana Seguinte"
once to reach the upcoming Mon–Fri block. On all other days the page already
opens on the correct target week. `getHtmlHorario.ts` encodes this routing
logic explicitly with the day-index check (`today === 5 || today === 6`).

**Schedule table heuristic.** The Inovar HTML contains multiple `<table>`
elements. `HtmlUtils.find_schedule_table()` scores each table by
`date_rows × 10 + time_rows + total_rows` and picks the winner. Time slots
appear as bare 3–4 digit integers (`800`, `900`, `1000`); class names appear
in adjacent cells aligned to the same column as their date header.
