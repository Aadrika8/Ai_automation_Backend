# Quality Insights — Backend

FastAPI + MongoDB API serving the Quality Insights frontend (`../Ai_automation_Frontend`).
Python 3.12+ · PyMongo (native async) · JWT auth with bcrypt-hashed passwords ·
server-side RBAC · Excel (.xlsx) read from disk per release and testing layer.

## How data flows

```
application  →  release  →  testing layer  →  rows
 cellSens        v4.4        Regression        206 rows
                 v4.3        Regression        198 rows   (kept, never overwritten)
```

The test team maintains one Excel sheet per testing layer, and a fresh set of
them for every release. A release ships roughly every six months and keeps its
own data for good: its layers, the columns discovered in its workbooks and every
row ingested from them are stored under `(appId, releaseId, layerId)`. Loading
v4.4 therefore cannot alter, overwrite or reshape anything v4.3 holds — even
when the new workbooks have different columns, sections, sizes or file names.

Data arrives one way: **the server reads the release's folder on disk and
records a snapshot per workbook.** A QA engineer or admin presses *Load from
Excel* and picks the month the data describes; nothing is uploaded from a
browser. Each load produces one complete, read-only **snapshot per file** —
never an edit to what is already stored — so history accumulates instead of
being consumed.

**One Excel file is one dataset.** Two workbooks feeding the same testing type
keep separate records, columns and per-file metrics; nothing merges them in
storage. The dashboard can *total* them on request — `?merged=true` adds up the
current snapshot of every file at read time — but that view writes nothing, and
reading the files singly afterwards returns exactly what it did before.

The parser discovers the columns from each sheet itself, so nothing about them
is hardcoded: sheets differ between testing types, between releases, and
between one month and the next. A sheet with no header row at all is read as
data with numbered columns rather than losing its first row to a header that
was never there.

The parser understands the testers' real sheet structure:

- **Section blocks** — a row with a single filled cell ("Camera testing") starts
  a section; the next multi-cell row is the header; repeated headers per section
  are skipped.
- **Forward-fill** — a spec name filled only on the first row of a group is
  inherited by the rows below it (resets at section boundaries).
- **Normalization** — whitespace/NBSP cleanup, numeric coercion, per-column
  string/number typing.
- **Deduplication** — row identity hashes the section + string-typed columns, so
  exact duplicates in a file are skipped, and re-uploading a sheet with changed
  counts *updates* rows in place instead of duplicating them. A unique Mongo
  index on `(appId, releaseId, layerId, rowKey)` backs this at the DB level —
  scoped per release, so the same row may legitimately appear in two of them.

### Snapshots

- **One snapshot = one release + one testing type + one file + one load.**
  Two workbooks feeding the same testing type produce two snapshots, numbered
  independently — `part-a.xlsx` has its own #1, #2, #3 and so does
  `part-b.xlsx`. A file is identified by its path relative to the release
  folder, so renaming one starts a new series and the old keeps its history.
- **The period is chosen by the person loading**, defaulting to the current
  month, and is correctable afterwards. It groups snapshots; it does not
  constrain them, so a month can hold several loads and resolves to the last.
- **Unchanged data records nothing.** The check hashes the *parsed content*,
  not the file bytes — saving a workbook rewrites its bytes without changing a
  cell, so byte hashes would mint a snapshot every time somebody pressed Save.
- **Rows are only ever inserted.** The unique index is
  `(snapshotId, rowKey)`, so a row's earlier versions sit beside its current
  one by construction.
- **Current** is the highest-numbered snapshot *of each file*. A view opens on
  the most recently loaded file unless another is named. A row deleted from a
  sheet leaves that file's current snapshot while every earlier one keeps it.
- **Record counts are summed across a testing type's files for the testing
  pyramid** — the pyramid needs one figure per type to compare.
- **The dashboard's merged view** (`?merged=true`) totals every file's current
  snapshot: measures are the union of the files' numeric columns, each summed
  over the files carrying it. Largest-rows appears only when the files share a
  measure — ranking a `test_count` against a `test_cases` would mean nothing.
  Asking to merge a testing type with a single workbook returns that workbook,
  so the dashboard can request merging by default without a special case.
  Records and history are always per file.
- **History is common to a testing type**: every file's snapshots together,
  newest load first, each flagged current for its own file. A diff still only
  ever compares a file with its own previous load.
- Each snapshot records the **diff** against the one before it (added /
  changed / removed), and the workbooks it was built from. When the columns
  that identify a row change, the diff is marked *not comparable* rather than
  reporting every row as removed and re-added.
- Deleting a snapshot removes only that snapshot and its rows; the one before
  it becomes current again, and the corrected workbook loads as the next.

### Releases

- Every release starts from the global default pyramid, or copies another
  release's **layer structure** (never its data or column definitions, since the
  new release's workbooks may look nothing like the old ones).
- Exactly one release per application is *current* — the one the UI opens on.
  Creating a release promotes it by default; an admin can promote an older one.
- Renaming a release keeps its id, so existing links keep resolving.
- Deleting a release removes only its own layers, uploads and rows.

### Feature ↔ System coverage

Two validations over one pair of testing layers, answered from one comparison:

- **Feature → System** — is every feature planned for this release covered by a
  system requirement?
- **System → Feature** — is every item in system scope represented at feature
  level?

Both come back as a single matrix, one entry per identifier across both sides,
each marked `covered`, `missing_in_system`, `missing_in_feature` or
`unresolved`, and carrying the row behind it so a gap can be acted on rather
than merely counted. Repeated ids are flagged as a data-quality problem
alongside the coverage verdict rather than replacing it, and an id that cannot
be read is reported rather than dropped — an unreadable id is absent from
*both* directions, which is exactly how a coverage report reaches 100% while
being wrong.

**No identifier format is assumed.** One team's sheets use `FL-5773`,
another's `PS-101`, `REQ-88`, `ABC_123` or bare numbers, and a headerless
workbook has no column names to go on at all. So the identifier column is
*discovered*: every column is generalised to a shape (`FL-5773` and `PS-101`
are both `A+-9+`) and the one whose values share a shape, are mostly distinct,
carry digits and are short enough wins — a date column, which generalises just
like an id, is detected and demoted. The extraction pattern is then derived
from the winning shape, so it fits whatever prefix the ids actually use. An
admin can override the column in **Matching**, which previews what the choice
reads before it is saved.

A row holding nothing but its id is turned into a *section heading* by the
parser, so the id never reaches a column. Those are read as ids too, once per
heading rather than once per row beneath it.

Coverage is computed when it is asked for, from the current snapshot of every
workbook feeding each layer — like the dashboards, it can never drift from the
rows it describes. Only the matching configuration is stored, per release,
since a new release's workbooks may be shaped nothing like the last one's.

## Architecture

```
app/
├── main.py                  # app factory: lifespan (Mongo connect/close), CORS, logging,
│                            # global 500 handler, /health, routers mounted under /api
├── config.py                # pydantic-settings (.env): MONGO_URI, MONGO_DB, JWT_SECRET,
│                            # JWT_EXPIRES_MIN, CORS_ORIGINS
├── db.py                    # AsyncMongoClient lifecycle + get_db() + ensure_indexes()
│                            # (drops the pre-release indexes it replaces)
├── security.py              # bcrypt hash/verify, JWT issue/verify, get_current_user,
│                            # ROLE_RANK {manager:0, qa:1, admin:2}, require_role(min)
├── models.py                # pydantic wire models (camelCase aliases), schema-flexible
│                            # ColumnDef / records / release / dashboard shapes
├── layer_defaults.py        # the global default pyramid + per-release layer keys
├── repositories.py          # async Mongo queries: CRUD, merge-upsert ingestion,
│                            # aggregation pipelines — all scoped by (appId, releaseId)
├── migrate.py               # python -m app.migrate: moves a pre-release database
│                            # onto this schema without losing a row
├── services/excel_ingest.py # the generic .xlsx parser (openpyxl)
├── services/excel_source.py # folder discovery: <root>/<app>/<release>/<layer>.xlsx
├── services/coverage.py     # Feature <-> System coverage: identifier discovery
│                            # (shape detection), extraction, bidirectional compare
├── routers/                 # auth · apps (apps, releases, layers, loads, records,
│                            # dashboard) · traceability · settings · users
└── seed/                    # python -m app.seed → users + cellSens + v4.3/v4.4, no data
tests/                       # pytest: parser units, folder sync, release isolation,
                             # merge/dedup, RBAC matrix, admin CRUD, auth (92 tests)
```

## MongoDB collections (db `quality_insights`)

| Collection | Contents |
|---|---|
| `users` | login users (bcrypt `passwordHash`) + display-only users (`null` hash) |
| `apps` | applications (admin-managed), e.g. `cellsens` |
| `releases` | versions of an application, e.g. `v4-3`, `v4-4`; one is `current`; `order` rises with each new one |
| `layers` | testing layers **per release** (`_id` = `app:release:layer`), ordered; carries that release's active column definitions |
| `snapshots` | one doc per file per load that changed something: `file`, `sequence` (per file), `period`, `contentHash`, `identityKeys`, `columns`, `sections`, `sources[]`, `rowCount`, `diff`, who and when |
| `layer_records` | one doc per row *per snapshot*: `{snapshotId, file, period, section, data: {col: value}, rowKey}` — unique index on `(snapshotId, rowKey)`, insert-only |
| `layer_uploads` | pre-snapshot loads, left in place by the migration as a record of what was loaded before snapshots existed |
| `trace_configs` | one per release: which column on each of Feature and System holds the shared identifier, and how to read it |
| `settings` | repo/hierarchy configuration |

Dashboards are computed at read time with aggregation pipelines (totals and sums
per section for every numeric column, top-10 rows) — nothing is precomputed, so
they can never drift from the ingested data, and each release aggregates only
its own rows.

##

`settings.excelRoot` is set once, by an admin, in Settings. The two folders
under it are named after the application and the release **as they read in the
UI**, so a release created in the app tells you exactly which folder to make:

```
A:\office excel files\          <- settings.excelRoot, the only configured path
    cellSens\                   <- the application's name
        v4.3\                   <- the release's name
            regression.xlsx     <- layer matched from the file name
            feature.xlsx
        v4.4\
            regression.xlsx
            unit\               <- or a folder per layer, when one layer is
                part-a.xlsx        split across several workbooks
                part-b.xlsx
```

A release whose workbooks live somewhere else sets `releases.excelPath` to its
own path relative to the root — that override is the only per-release
configuration, and it is normally blank. Applications carry no path at all.

A workbook is matched to a layer by file name or parent folder name, ignoring a
trailing `-test`/`-tests`/`-testing`, so `regression.xlsx`, `Regression
Testing.xlsx` and `unit tests.xlsx` all land where you would expect. A file
matching no layer is listed as unmatched rather than silently dropped, and the
sync dialog lets you assign it by hand. Paths are confined to the root: one
climbing out with `..` is rejected, not followed. Local paths and UNC shares
both work — for a share, run the server as an account that can read it.

### Re-enabling upload

Browser upload is commented out, not removed. To restore it, uncomment:

| Where | What |
|---|---|
| `app/routers/apps.py` | the `# --- direct browser upload — DISABLED` block |
| `../Ai_automation_Frontend/src/api/client.ts` | `requestForm` and `uploadLayerExcel` |
| `../Ai_automation_Frontend/src/components/UploadDialog.tsx` | the whole file |
| `../Ai_automation_Frontend/src/pages/LayersPage.tsx`, `LayerPage.tsx` | the blocks marked `UPLOAD DISABLED` |
| `tests/test_uploads.py` | drop the module-level `pytestmark` skip |

### Re-enabling the coverage CSV export

The Feature ↔ System matrix downloaded as a file. Commented out, not removed:

| Where | What |
|---|---|
| `app/routers/traceability.py` | the `# --- CSV export — DISABLED` block, and the `csv` / `io` / `StreamingResponse` imports it owns |
| `../Ai_automation_Frontend/src/api/client.ts` | `coverageCsvUrl` |
| `../Ai_automation_Frontend/src/pages/CoveragePage.tsx` | the download button beside the search box |
| `tests/test_coverage_api.py` | drop the `@csv_disabled` marks |

## Setup & run

```bash
cd Ai_automation_Backend
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # then set a real JWT_SECRET (see below)

python -m app.seed                   # users + cellSens + v4.3/v4.4 + empty layers
uvicorn app.main:app --port 8000     # startup logs print the docs URLs
```

Requires MongoDB running locally (default `mongodb://127.0.0.1:27017`).

**Set a real `JWT_SECRET`.** The server refuses to start while the secret is the
placeholder — put a random value in `.env` (never committed):

```bash
echo "JWT_SECRET=$(python -c 'import secrets; print(secrets.token_hex(32))')" >> .env
```

### Migrating a database that predates releases

`python -m app.seed` drops everything. To keep data already ingested, migrate
instead — it puts each application's existing layers and rows into one release
and re-keys them, without deleting a row:

```bash
python -m app.migrate                        # dry run: prints what it would do
python -m app.migrate --apply                # release name defaults to "Initial release"
python -m app.migrate --apply --release "v4.3"
```

It is safe to run twice: documents that already carry a `releaseId` are skipped.

### Interactive API docs

| Docs | URL |
|---|---|
| Swagger UI (try endpoints) | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| OpenAPI schema (JSON) | http://localhost:8000/openapi.json |

In Swagger UI, click **Authorize** and paste a token from `POST /api/auth/login`.

### Load a release from the command line

```bash
TOKEN=$(curl -s localhost:8000/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"qa","password":"qa123"}' | python -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# what the release's folder currently holds, and which layer each file feeds
curl -H "Authorization: Bearer $TOKEN" \
  localhost:8000/api/apps/cellsens/releases/v4-4/source

# snapshot every testing type that has exactly one matched workbook
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"period":{"year":2026,"month":9}}' \
  localhost:8000/api/apps/cellsens/releases/v4-4/snapshots

# that testing type's history, newest first
curl -H "Authorization: Bearer $TOKEN" \
  localhost:8000/api/apps/cellsens/releases/v4-4/layers/regression/snapshots
```

## API

All routes under `/api`, JWT Bearer auth. Everything below an application is
addressed through a release:

| Route | Access |
|---|---|
| `POST /api/auth/login` → `{token, user}` | public |
| `GET /api/apps`, `…/releases`, `…/releases/{rel}/layers`, `…/layers/{layer}/files`, `…/records?file=`, `…/dashboard?file=`, `…/source`, `…/layers/{layer}/snapshots?file=`, `GET /api/settings` | any authenticated |
| `POST …/releases/{rel}/snapshots` — read the release's folder and record a snapshot per testing type | qa + admin |
| `PATCH …/releases/{rel}/snapshots/{id}` — correct a snapshot's period | admin |
| `DELETE …/releases/{rel}/snapshots/{id}` — remove one snapshot and its rows | admin |
| `POST/PATCH/DELETE /api/apps`, `…/releases`, `…/releases/{rel}/layers` — structure CRUD | admin |
| `DELETE …/releases/{rel}/layers/{layer}/records` — remove every snapshot of one testing type | admin |
| `GET …/releases/{rel}/traceability` — Feature ↔ System coverage, both directions | any authenticated |
| `GET …/releases/{rel}/traceability/config` — the matching in force, or the detected default | any authenticated |
| `PUT …/releases/{rel}/traceability/config` — correct the detected columns | admin |
| `GET …/releases/{rel}/traceability/preview` — what a column would read | admin |
| `PUT /api/settings`, `GET/POST /api/users`, `DELETE /api/users/{username}` | admin |

Creating an application also creates its first release (named by `releaseName`,
default "Initial release") carrying the default pyramid. `POST …/releases`
accepts `copyLayersFrom` to clone another release's layer structure and
`makeCurrent` (default true).

Deleting an application cascades to every release, layer, upload and row it
owns; deleting a release cascades to its own only. Admins cannot delete their
own account. Deleting a user does not revoke tokens already issued to them
(they expire with `JWT_EXPIRES_MIN`).

Unknown app/release/layer ids → 404; ids are lowercase slugs
(`^[a-z0-9][a-z0-9-]*$`), so a release named `v4.3` is addressed as `v4-3`.
Demo accounts: `admin/admin123`, `qa/qa123`, `manager/manager123`.

## Tests

```bash
.venv/bin/python -m pytest           # 230 tests against a separate qi_test DB
```

The 10 upload tests are skipped along with the endpoint; they are kept intact
and un-skip with it.

Covers: the parser against synthetic sheets *and* the real
`tests/fixtures/cellSens-Count.xlsx` (218 rows → 206 unique, 12 duplicates,
total 140 736); merge/update semantics; **release isolation** — two releases
holding differently shaped workbooks for the same layer, identical rows
coexisting across releases, replace/clear/delete in one release leaving the
others intact; folder resolution, overrides and path errors; records grouping/search/pagination;
dashboard sums; admin CRUD with cascades; the full RBAC 401/403 matrix; auth
flows; and Feature ↔ System coverage — identifier detection across prefixed,
numeric and underscored id formats, the section-heading case, both gap
directions, duplicates and unreadable ids, and release isolation.

## Future phases (deliberately not built yet)

- **Scheduled loads** — a background sweep so nobody has to press *Load from
  Excel*, plus a record of every attempt and its outcome. Worth adding now that
  reading from disk is the only way data arrives.
- **Cross-snapshot row diffs in the UI** — the counts are stored per snapshot;
  showing *which* rows changed is a query away.
- **Cross-release comparison** — the data model now supports it (same layer ids
  across releases); the UI shows one release at a time.
- **Run-result ingestion** — pass/fail/duration per test; unlocks the pass-rate
  trend area already placeholdered in the frontend dashboard.
- **Upload history UI** — `layer_uploads` already stores every load's metadata.
- **Token revocation / refresh tokens** — JWT claims are trusted for their
  lifetime; add a per-request user lookup when needed.
