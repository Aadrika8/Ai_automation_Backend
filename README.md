# Quality Insights — Backend

FastAPI + MongoDB API serving the Quality Insights frontend (`../Frontend`).
Python 3.12 · PyMongo (native async) · JWT auth with bcrypt-hashed passwords ·
server-side RBAC.

## Architecture

```
app/
├── main.py           # app factory: lifespan (Mongo connect/close), CORS, logging,
│                     # global 500 handler, /health, routers mounted under /api
├── config.py         # pydantic-settings (.env): MONGO_URI, MONGO_DB, JWT_SECRET,
│                     # JWT_EXPIRES_MIN, CORS_ORIGINS
├── db.py             # AsyncMongoClient lifecycle + get_db()
├── security.py       # bcrypt hash/verify, JWT issue/verify, get_current_user,
│                     # ROLE_RANK {manager:0, qa:1, admin:2}, require_role(min)
├── models.py         # pydantic response models — field-for-field mirror of
│                     # Frontend/src/api/types.ts (camelCase aliases on the wire)
├── repositories.py   # thin async Mongo queries (projections, id reshaping)
├── routers/          # auth · apps (layers/dashboard/tests) · tests · runs ·
│                     # settings · users — RBAC enforced via dependencies
└── seed/             # python -m app.seed → deterministic demo dataset
tests/                # pytest: auth, RBAC matrix, contract shapes (29 tests)
```

Design notes: documents are stored **camelCase in the exact response shapes**
(dashboards precomputed at seed time), so the API layer is find → validate →
serve with no runtime generation. No services layer yet — the API is read-mostly
(`# ponytail:` marked; add one when write-logic like git-sync lands).

## MongoDB collections (db `quality_insights`)

| Collection | Docs | Contents |
|---|---|---|
| `users` | 5 | login users (bcrypt `passwordHash`) + display-only users (`null` hash) |
| `apps` | 3 | app summaries with precomputed totals/pass rates |
| `layers` | 12 | layer info **+ full dashboard payload** (snapshot, 90-day history, 24 h hourly, version, recent runs) |
| `tests` | ~5670 | test cases with embedded detail (path, 7-run history, failure w/ stack trace) |
| `runs` | 72 | recent executions across all apps/layers |
| `settings` | 1 | repo/hierarchy configuration |

## Setup & run

```bash
cd Backend
python3.12 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # optional — defaults work for local dev

python -m app.seed                   # fill Mongo (idempotent: drop + recreate)
uvicorn app.main:app --port 8000     # startup logs print the docs URLs
```

### Interactive API docs

FastAPI generates them automatically — once the server is up:

| Docs | URL |
|---|---|
| Swagger UI (try endpoints) | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| OpenAPI schema (JSON) | http://localhost:8000/openapi.json |

In Swagger UI, click **Authorize** and paste a token from `POST /api/auth/login`
to call the protected routes.

Requires MongoDB running locally (default `mongodb://127.0.0.1:27017`).

**Set a real `JWT_SECRET`.** The server refuses to start while the secret is the
placeholder — put a random value in `.env` (never committed):

```bash
echo "JWT_SECRET=$(python -c 'import secrets; print(secrets.token_hex(32))')" >> .env
```

Tokens are signed with this secret; anyone who knows it can forge an admin token,
so keep it out of version control (`.env` is gitignored).

## API

Interactive docs at `/docs` (OpenAPI). All routes under `/api`, JWT Bearer auth:

| Route | Access |
|---|---|
| `POST /api/auth/login` → `{token, user}` | public |
| `GET /api/apps`, `…/layers`, `…/dashboard`, `GET/…settings` | any authenticated |
| `GET …/tests`, `/api/tests/{id}`, `/api/runs` | qa + admin |
| `PUT /api/settings`, `GET /api/users` | admin |

Demo accounts: `admin/admin123`, `qa/qa123`, `manager/manager123`.

## Tests

```bash
.venv/bin/python -m pytest           # 29 tests against a separate qi_test DB
```

Covers: login success/failure per role, display-only users rejected, every route
401 without a token, the full RBAC 403 matrix, and contract-shape checks
(camelCase field sets, array lengths 90/24/6, test counts reconciling with
snapshots, failure details, settings round-trip).

## Future phases (deliberately not built yet)

- **Git sync**: an admin-triggered `/api/sync` that clones the configured test-suite
  repo and upserts real test cases — `../test_runner/core/git_manager.py` and
  `repo_scanner.py` are pure-Python and port directly.
- **Tests pagination** (`?skip=&limit=`) — the frontend currently consumes the full
  array (largest ≈ 1441 rows, fine).
- **Token revocation / refresh tokens** — JWT claims are trusted for their lifetime;
  add a per-request user lookup when needed.
- Real test-execution ingestion replacing the seeded demo data.
