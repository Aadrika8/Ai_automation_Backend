"""The release QA report: written by OpenAI from the app's own figures.

One report per request, saved per release, so opening it again costs nothing
and a later reader sees exactly what was written and from what. Generating is
QA's — each one is a paid call — and reading is anyone's.

Nothing here changes a dashboard figure. The facts are read from the same
functions the pages use, and the only thing ever written is the report itself,
to `qa_reports`.
"""
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import ValidationError

from app import repositories as repo
from app.benchmark_defaults import automation_reference
from app.models import MOVED_TO_TRACEABILITY, QAReportBody, ReportResponse
from app.routers import apps as apps_router
from app.routers import benchmark as benchmark_router
from app.routers import traceability as trace_router
from app.security import get_current_user, require_role
from app.services import benchmark as bm
from app.services import report as rpt
from app.services.report_writer import ReportError, ReportWriter, get_report_writer

router = APIRouter(tags=["reports"], dependencies=[Depends(get_current_user)])
_qa = Depends(require_role("qa"))

Slug = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]

NOT_CONFIGURED = ("Report generation isn't set up: add OPENAI_API_KEY to the backend's "
                  ".env and restart the backend.")


async def _require_release(app_id: str, release_id: str) -> tuple[dict, dict]:
    app = await repo.get_app(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    release = await repo.get_release(app_id, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")
    return app, release


async def _facts(app_id: str, release_id: str, app: dict, release: dict) -> dict:
    """Everything the report may say, read from what the pages already compute."""
    layers = await repo.list_layers(app_id, release_id)
    layer_facts = []
    for layer in layers:
        files = []
        for snap in await repo.list_current_snapshots(app_id, release_id, layer["id"]):
            columns = snap.get("columns", [])
            stats = await repo.aggregate_dashboard(snap["_id"], columns)
            profile = await apps_router._dashboard_profile(snap["_id"], columns, stats, "")
            warnings = [w for w in snap.get("warnings", [])
                        if w.get("code") not in MOVED_TO_TRACEABILITY]
            files.append(rpt.file_facts(snap, stats, profile, warnings))
        layer_facts.append(rpt.layer_facts(layer, files))

    trace = await trace_router._compute(app_id, release_id)
    measured, _latest = await benchmark_router._measure_release(app_id, release_id)
    automation = bm.coverage_report(measured, automation_reference())

    return rpt.assemble(
        app.get("name", app_id), release.get("name", release_id), layer_facts,
        rpt.pyramid(layers), rpt.traceability_facts(trace),
        rpt.automation_facts(automation, {l["id"]: l["name"] for l in layers}))


def _saved(doc: dict) -> dict:
    return {
        "id": doc["_id"],
        "createdAt": doc["createdAt"],
        "createdBy": doc.get("createdBy", ""),
        "model": doc.get("model", ""),
        "releaseName": doc.get("releaseName", ""),
        "period": doc.get("period", ""),
        "layerNames": doc.get("layerNames", {}),
        "report": doc["report"],
        "unverified": doc.get("unverified", []),
        "notCovered": doc.get("notCovered", []),
        "addedFromData": doc.get("addedFromData", []),
    }


@router.get("/apps/{app_id}/releases/{release_id}/report", response_model=ReportResponse)
async def get_report_route(app_id: Slug, release_id: Slug,
                           writer: ReportWriter | None = Depends(get_report_writer)):
    """The last report saved for this release, and whether its data has moved on."""
    app, release = await _require_release(app_id, release_id)
    doc = await repo.latest_report(app_id, release_id)
    stale = False
    if doc is not None:
        facts = await _facts(app_id, release_id, app, release)
        stale = rpt.facts_hash(facts) != doc.get("factsHash")
    return {"configured": writer is not None, "model": writer.model if writer else "",
            "report": _saved(doc) if doc else None, "stale": stale}


@router.post("/apps/{app_id}/releases/{release_id}/report", response_model=ReportResponse)
async def generate_report_route(app_id: Slug, release_id: Slug, user: dict = _qa,
                                writer: ReportWriter | None = Depends(get_report_writer)):
    """Write a new report from the release's current figures, and save it."""
    app, release = await _require_release(app_id, release_id)
    if writer is None:
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED)

    facts = await _facts(app_id, release_id, app, release)
    try:
        raw = await writer.write(facts)
    except ReportError as e:
        raise HTTPException(status_code=e.status, detail=e.message) from None
    try:
        body = QAReportBody.model_validate(raw).model_dump()
    except ValidationError:
        raise HTTPException(status_code=502, detail="The model's reply did not have the "
                                                    "report's sections. Try again.") from None

    body = rpt.clean_sources(body, [layer["id"] for layer in facts["layers"]])
    # whatever the draft left out is added from the data, not asked for again
    body, added = rpt.fill_gaps(body, facts)
    doc = {
        "_id": uuid4().hex,
        "appId": app_id,
        "releaseId": release_id,
        "createdAt": datetime.now(timezone.utc),
        "createdBy": user["username"],
        "model": writer.model,
        "releaseName": facts["release"],
        "period": facts["period"],
        "layerNames": {layer["id"]: layer["name"] for layer in facts["layers"]},
        # kept with the report: what it was written from, for anyone checking it
        "factsHash": rpt.facts_hash(facts),
        "facts": facts,
        "report": body,
        "unverified": rpt.unverified(body, facts),
        "notCovered": rpt.not_covered(body, facts),
        "addedFromData": added,
    }
    await repo.insert_report(doc)
    return {"configured": True, "model": writer.model, "report": _saved(doc), "stale": False}
