# Quality Insights — Backend

FastAPI + MongoDB API serving the Quality Insights frontend (`../Ai_automation_Frontend`).
Python 3.12+ · PyMongo (native async) · JWT auth with bcrypt-hashed passwords ·
server-side RBAC · Excel (.xlsx) ingestion per testing layer.

## How data flows

The test team maintains one Excel sheet per testing layer (e.g.
`cellSens-Count.xlsx` for Regression). A QA engineer or admin uploads the sheet
for a layer; the backend parses it generically and stores every unique row in
MongoDB. Sheets differ between layers (different columns), so nothing about the
columns is hardcoded — the parser discovers them from the sheet's header row.

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
  index on `(appId, layerId, rowKey)` backs this at the DB level.

Re-uploads offer two modes, chosen in the upload dialog: **merge** (default —
new rows inserted, changed rows updated, unchanged left alone; rows that
disappeared from the sheet are kept) or **replace** (the layer is wiped first,
so the file becomes the whole dataset). *Delete uploaded data* likewise offers
two scopes: only the latest upload, or everything.

## Architecture

```
app/
├── main.py                  # app factory: lifespan (Mongo connect/close), CORS, logging,
│                            # global 500 handler, /health, routers mounted under /api
├── config.py                # pydantic-settings (.env): MONGO_URI, MONGO_DB, JWT_SECRET,
│                            # JWT_EXPIRES_MIN, CORS_ORIGINS
├── db.py                    # AsyncMongoClient lifecycle + get_db() + ensure_indexes()
├── security.py              # bcrypt hash/verify, JWT issue/verify, get_current_user,
│                            # ROLE_RANK {manager:0, qa:1, admin:2}, require_role(min)
├── models.py                # pydantic wire models (camelCase aliases), schema-flexible
│                            # ColumnDef / records / dashboard shapes
├── repositories.py          # async Mongo queries: CRUD, merge-upsert ingestion,
│                            # aggregation pipelines for dashboards
├── services/excel_ingest.py # the generic .xlsx parser (openpyxl)
├── routers/                 # auth · apps (apps/layers CRUD, uploads, records,
│                            # dashboard) · settings · users
└── seed/                    # python -m app.seed → users + cellSens + 4 layers, no data
tests/                       # pytest: parser units, upload/merge/dedup, RBAC matrix,
                             # admin CRUD, auth (50 tests)
```

## MongoDB collections (db `quality_insights`)

| Collection | Contents |
|---|---|
| `users` | login users (bcrypt `passwordHash`) + display-only users (`null` hash) |
| `apps` | applications (admin-managed), e.g. `cellsens` |
| `layers` | testing layers per app (`regression`, `system`, `feature`, `acceptance`), ordered; carries the active column definitions |
| `layer_uploads` | one doc per upload: file name/size, columns, sections, inserted/updated/unchanged/duplicate counts, uploader, timestamp |
| `layer_records` | one doc per unique ingested row: `{section, data: {col: value}, rowKey}` — unique index on `(appId, layerId, rowKey)` |
| `settings` | repo/hierarchy configuration |

Dashboards are computed at read time with aggregation pipelines (totals and sums
per section for every numeric column, top-10 rows) — nothing is precomputed, so
they can never drift from the ingested data.

## Setup & run

```bash
cd Ai_automation_Backend
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # then set a real JWT_SECRET (see below)

python -m app.seed                   # users + cellSens app + 4 empty layers
uvicorn app.main:app --port 8000     # startup logs print the docs URLs
```

Requires MongoDB running locally (default `mongodb://127.0.0.1:27017`).

**Set a real `JWT_SECRET`.** The server refuses to start while the secret is the
placeholder — put a random value in `.env` (never committed):

```bash
echo "JWT_SECRET=$(python -c 'import secrets; print(secrets.token_hex(32))')" >> .env
```

### Interactive API docs

| Docs | URL |
|---|---|
| Swagger UI (try endpoints) | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| OpenAPI schema (JSON) | http://localhost:8000/openapi.json |

In Swagger UI, click **Authorize** and paste a token from `POST /api/auth/login`.

### Upload a sheet from the command line

```bash
TOKEN=$(curl -s localhost:8000/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"qa","password":"qa123"}' | python -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -H "Authorization: Bearer $TOKEN" \
  -F "file=@cellSens-Count.xlsx" \
  localhost:8000/api/apps/cellsens/layers/regression/uploads
```

## API

All routes under `/api`, JWT Bearer auth:

| Route | Access |
|---|---|
| `POST /api/auth/login` → `{token, user}` | public |
| `GET /api/apps`, `…/layers`, `…/records`, `…/dashboard`, `GET /api/settings` | any authenticated |
| `POST …/layers/{layer}/uploads?mode=merge\|replace` — Excel ingestion | qa + admin |
| `POST/PATCH/DELETE /api/apps`, `…/layers` — structure CRUD | admin |
| `DELETE …/layers/{layer}/records?scope=all\|last` — purge a layer's data, or only its latest upload | admin |
| `PUT /api/settings`, `GET/POST /api/users`, `DELETE /api/users/{username}` | admin |

Deleting an application or layer cascades to its uploads and records. Admins
cannot delete their own account. Deleting a user does not revoke tokens already
issued to them (they expire with `JWT_EXPIRES_MIN`).

Unknown app/layer ids → 404; ids are lowercase slugs (`^[a-z0-9][a-z0-9-]*$`).
Demo accounts: `admin/admin123`, `qa/qa123`, `manager/manager123`.

## Tests

```bash
.venv/bin/python -m pytest           # 55 tests against a separate qi_test DB
```

Covers: the parser against synthetic sheets *and* the real
`tests/fixtures/cellSens-Count.xlsx` (218 rows → 206 unique, 12 duplicates,
total 140 736), upload + merge/update semantics, records
grouping/search/pagination, dashboard sums, admin CRUD with cascades, the full
RBAC 401/403 matrix, and auth flows.

## Future phases (deliberately not built yet)

- **Run-result ingestion** — pass/fail/duration per test; unlocks the pass-rate
  trend area already placeholdered in the frontend dashboard.
- **Upload history UI** — `layer_uploads` already stores every upload's metadata.
- **Token revocation / refresh tokens** — JWT claims are trusted for their
  lifetime; add a per-request user lookup when needed.
