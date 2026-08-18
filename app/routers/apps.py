"""Apps, layers, Excel ingestion, records and dashboards.

Reads are open to any authenticated role; uploads need qa+; structural
changes (create/update/delete apps and layers, purging uploaded data)
need admin.
"""
import re
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, UploadFile

from app import repositories as repo
from app.models import (
    AppCreate,
    AppSummary,
    AppUpdate,
    LayerCreate,
    LayerDashboardResponse,
    LayerInfo,
    LayerRecordsResponse,
    LayerUpdate,
    UploadResult,
)
from app.security import get_current_user, require_role
from app.services.excel_ingest import MAX_FILE_BYTES, IngestError, parse_workbook

router = APIRouter(tags=["apps"], dependencies=[Depends(get_current_user)])

Slug = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]

_admin = Depends(require_role("admin"))
_qa = Depends(require_role("qa"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]
    if not slug:
        raise HTTPException(status_code=422, detail="Name must contain letters or digits")
    return slug


async def _require_layer(app_id: str, layer_id: str) -> dict:
    layer = await repo.get_layer(app_id, layer_id)
    if layer is None:
        raise HTTPException(status_code=404, detail="Layer not found")
    return layer


# --- apps ---------------------------------------------------------------


@router.get("/apps", response_model=list[AppSummary])
async def get_apps():
    return await repo.list_apps()


@router.post("/apps", response_model=AppSummary, status_code=201, dependencies=[_admin])
async def post_app(body: AppCreate):
    app_id = slugify(body.name)
    if await repo.get_app(app_id) is not None:
        raise HTTPException(status_code=409, detail=f'An application "{app_id}" already exists')
    now = _now()
    await repo.create_app({
        "_id": app_id, "name": body.name, "tag": body.tag, "desc": body.desc,
        "icon": body.icon, "createdAt": now, "updatedAt": now,
    })
    return {"id": app_id, "name": body.name, "tag": body.tag, "desc": body.desc,
            "icon": body.icon, "layerCount": 0, "recordCount": 0}


@router.patch("/apps/{app_id}", response_model=AppSummary, dependencies=[_admin])
async def patch_app(app_id: Slug, body: AppUpdate):
    if await repo.get_app(app_id) is None:
        raise HTTPException(status_code=404, detail="Application not found")
    patch = body.model_dump(exclude_none=True)
    if patch:
        await repo.update_app(app_id, patch)
    apps = await repo.list_apps()
    return next(a for a in apps if a["id"] == app_id)


@router.delete("/apps/{app_id}", status_code=204, dependencies=[_admin])
async def delete_app(app_id: Slug):
    if await repo.get_app(app_id) is None:
        raise HTTPException(status_code=404, detail="Application not found")
    await repo.delete_app_cascade(app_id)


# --- layers -------------------------------------------------------------


@router.get("/apps/{app_id}/layers", response_model=list[LayerInfo])
async def get_layers(app_id: Slug):
    if await repo.get_app(app_id) is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return await repo.list_layers(app_id)


@router.post("/apps/{app_id}/layers", response_model=LayerInfo, status_code=201,
             dependencies=[_admin])
async def post_layer(app_id: Slug, body: LayerCreate):
    if await repo.get_app(app_id) is None:
        raise HTTPException(status_code=404, detail="Application not found")
    layer_id = slugify(body.name)
    if await repo.get_layer(app_id, layer_id) is not None:
        raise HTTPException(status_code=409, detail=f'A layer "{layer_id}" already exists')
    existing = await repo.list_layers(app_id)
    order = max((l["order"] for l in existing), default=-1) + 1
    now = _now()
    await repo.create_layer({
        "_id": f"{app_id}:{layer_id}", "appId": app_id, "layerId": layer_id,
        "name": body.name, "short": body.short, "desc": body.desc, "order": order,
        "createdAt": now, "updatedAt": now,
    })
    return {"id": layer_id, "name": body.name, "short": body.short, "desc": body.desc,
            "order": order, "recordCount": 0}


@router.patch("/apps/{app_id}/layers/{layer_id}", response_model=LayerInfo,
              dependencies=[_admin])
async def patch_layer(app_id: Slug, layer_id: Slug, body: LayerUpdate):
    await _require_layer(app_id, layer_id)
    patch = body.model_dump(exclude_none=True)
    if patch:
        await repo.update_layer(app_id, layer_id, patch)
    layers = await repo.list_layers(app_id)
    return next(l for l in layers if l["id"] == layer_id)


@router.delete("/apps/{app_id}/layers/{layer_id}", status_code=204, dependencies=[_admin])
async def delete_layer(app_id: Slug, layer_id: Slug):
    await _require_layer(app_id, layer_id)
    await repo.delete_layer_cascade(app_id, layer_id)


# --- Excel ingestion ----------------------------------------------------


@router.post("/apps/{app_id}/layers/{layer_id}/uploads", response_model=UploadResult)
async def upload_excel(app_id: Slug, layer_id: Slug, file: UploadFile,
                       mode: Literal["merge", "replace"] = Query(default="merge"),
                       user: dict = _qa):
    await _require_layer(app_id, layer_id)
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Only .xlsx files are supported")
    data = await file.read(MAX_FILE_BYTES + 1)
    try:
        parsed = parse_workbook(data)
    except IngestError as e:
        raise HTTPException(status_code=400, detail=e.message)

    upload_doc = {
        "_id": uuid.uuid4().hex, "appId": app_id, "layerId": layer_id,
        "fileName": file.filename, "fileSize": len(data),
        "columns": parsed.columns, "sections": parsed.sections,
        "totalRows": parsed.total_rows, "duplicatesSkipped": parsed.duplicates_skipped,
        "uploadedBy": user["username"], "uploadedAt": _now(),
    }
    counts = await repo.merge_layer_data(app_id, layer_id, upload_doc, parsed.rows,
                                         replace=(mode == "replace"))
    return {
        "uploadId": upload_doc["_id"], "fileName": upload_doc["fileName"],
        "columns": parsed.columns, "sections": parsed.sections,
        "totalRows": parsed.total_rows, "duplicatesSkipped": parsed.duplicates_skipped,
        "uploadedBy": upload_doc["uploadedBy"], "uploadedAt": upload_doc["uploadedAt"],
        **counts,
    }


def _upload_result(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    return {k: v for k, v in doc.items() if k not in ("appId", "layerId", "fileSize")}


@router.get("/apps/{app_id}/layers/{layer_id}/records", response_model=LayerRecordsResponse)
async def get_records(
    app_id: Slug, layer_id: Slug,
    search: str | None = Query(default=None, max_length=200),
    section: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=250, ge=1, le=500, alias="pageSize"),
):
    layer = await _require_layer(app_id, layer_id)
    columns = layer.get("columns", [])
    total, docs = await repo.list_records(
        app_id, layer_id, columns, search, section,
        skip=(page - 1) * page_size, limit=page_size,
    )
    sections: list[dict] = []
    for d in docs:
        if not sections or sections[-1]["name"] != d["section"]:
            sections.append({"name": d["section"], "rowCount": 0, "rows": []})
        sections[-1]["rows"].append({"section": d["section"], "data": d["data"]})
        sections[-1]["rowCount"] += 1
    return {
        "columns": columns,
        "lastUpload": _upload_result(await repo.get_latest_upload(app_id, layer_id)),
        "total": total, "page": page, "pageSize": page_size, "sections": sections,
    }


@router.get("/apps/{app_id}/layers/{layer_id}/dashboard", response_model=LayerDashboardResponse)
async def get_dashboard(app_id: Slug, layer_id: Slug):
    layer = await _require_layer(app_id, layer_id)
    stats = await repo.aggregate_dashboard(app_id, layer_id, layer.get("columns", []))
    return {
        "lastUpload": _upload_result(await repo.get_latest_upload(app_id, layer_id)),
        **stats,
    }


@router.delete("/apps/{app_id}/layers/{layer_id}/records", dependencies=[_admin])
async def delete_records(app_id: Slug, layer_id: Slug,
                         scope: Literal["all", "last"] = Query(default="all")):
    await _require_layer(app_id, layer_id)
    if scope == "last":
        deleted = await repo.clear_last_upload(app_id, layer_id)
    else:
        deleted = await repo.clear_layer_records(app_id, layer_id)
    return {"deleted": deleted, "scope": scope}
