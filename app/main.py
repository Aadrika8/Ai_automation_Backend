"""Quality Insights API — FastAPI app factory."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db
from app.config import PLACEHOLDER_SECRET, get_settings
from app.routers import apps, auth, reports, runs, settings as settings_router, tests, users

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("qi")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if get_settings().jwt_secret == PLACEHOLDER_SECRET:
        raise RuntimeError(
            "JWT_SECRET is unset — tokens would be signed with a public placeholder. "
            "Copy .env.example to .env and set a random secret: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )
    await db.connect()
    log.info("Connected to MongoDB at %s", get_settings().mongo_uri)
    log.info("Swagger UI:   http://localhost:8000/docs")
    log.info("ReDoc:        http://localhost:8000/redoc")
    log.info("OpenAPI JSON: http://localhost:8000/openapi.json")
    yield
    await db.close()


app = FastAPI(title="Quality Insights API", version="1.0.0", lifespan=lifespan)


# Catch-all is a middleware (not @app.exception_handler) so its 500 response
# passes back out through CORSMiddleware and carries the CORS headers the
# browser needs — otherwise the frontend sees an opaque "Failed to fetch".
# CORS is added last so it wraps this handler (later add_middleware = outer).
@app.middleware("http")
async def catch_unhandled(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
async def health():
    await db.get_db().command("ping")
    return {"status": "ok"}


for router in (auth.router, apps.router, tests.router, runs.router, settings_router.router, users.router, reports.router):
    app.include_router(router, prefix="/api")
