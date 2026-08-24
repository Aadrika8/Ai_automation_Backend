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
from app.layer_defaults import default_layer_docs
from app.models import (
    AppCreate,
    AppSummary,
    AppUpdate,
    LayerCreate,
    LayerDashboardResponse,
    LayerInfo,
    LayerRecordsResponse,
    LayerUpdate,
    SourceStatus,
    SyncRequest,
    SyncResult,
    UploadResult,
)
from app.security import get_current_user, require_role
from app.services import excel_source
from app.services.excel_ingest import MAX_FILE_BYTES, IngestError, parse_workbook
from app.services.excel_source import SourceError

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
        "icon": body.icon, "excelPath": body.excel_path or app_id,
        "createdAt": now, "updatedAt": now,
    })
    # every application starts from the same global pyramid; layers added
    # afterwards belong to that application alone
    layers = default_layer_docs(app_id, now)
    await repo.create_layers(layers)
    return {"id": app_id, "name": body.name, "tag": body.tag, "desc": body.desc,
            "icon": body.icon, "excelPath": body.excel_path or app_id,
            "layerCount": len(layers), "recordCount": 0}


@router.patch("/apps/{app_id}", response_model=AppSummary, dependencies=[_admin])
async def patch_app(app_id: Slug, body: AppUpdate):
    if await repo.get_app(app_id) is None:
        raise HTTPException(status_code=404, detail="Application not found")
    patch = body.model_dump(exclude_none=True, by_alias=True)
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
    # `order` is the pyramid slot the new layer is dropped into, bottom-first:
    # 0 is the base (most test cases), len(existing) the tip (fewest). Layers
    # from that slot upwards shift up one, and the stack is renumbered so
    # positions stay contiguous even after earlier deletions.
    existing = sorted(await repo.list_layers(app_id), key=lambda l: l["order"])
    order = len(existing) if body.order is None else min(body.order, len(existing))
    shifted: dict[str, int] = {}
    for i, layer in enumerate(existing):
        new_order = i if i < order else i + 1
        if new_order != layer["order"]:
            shifted[layer["id"]] = new_order
    await repo.set_layer_orders(app_id, shifted)

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
    # close the gap the removed layer left, so pyramid positions stay contiguous
    remaining = sorted(await repo.list_layers(app_id), key=lambda l: l["order"])
    await repo.set_layer_orders(
        app_id, {l["id"]: i for i, l in enumerate(remaining) if l["order"] != i})


# --- Excel source (path-based ingestion) --------------------------------


async def _source_status(app_id: str, app: dict) -> dict:
    """Inspect the configured folder: which workbook feeds which layer, and
    what has changed since the last sync. Never raises for a bad path — the
    problem is returned as a message the UI can show."""
    settings = await repo.get_settings_doc() or {}
    root = settings.get("excelRoot", "")
    rel = app.get("excelPath", "")
    layers = sorted(await repo.list_layers(app_id), key=lambda l: l["order"])

    base = {"root": root, "relativePath": rel, "resolvedPath": "", "ok": False,
            "layers": [], "unmatchedFiles": [], "changedCount": 0}
    try:
        folder = excel_source.resolve_app_folder(root, rel)
        base["resolvedPath"] = str(folder)
        files = excel_source.scan_app_folder(root, rel, layers)
    except SourceError as e:
        return {**base, "errorCode": e.code, "error": e.message}

    # what each layer was last built from, to flag changed workbooks
    last: dict[str, dict] = {}
    hashes: dict[str, dict[str, str]] = {}
    for layer in layers:
        doc = await repo.get_latest_upload(app_id, layer["id"])
        if doc:
            last[layer["id"]] = doc
        hashes[layer["id"]] = await repo.get_layer_file_hashes(app_id, layer["id"])

    def as_info(f, changed: bool) -> dict:
        return {"name": f.name, "relativePath": f.relative_path, "sizeBytes": f.size_bytes,
                "modifiedAt": f.modified_at, "layerId": f.layer_id,
                "changed": changed, "error": f.error}

    changed_count = 0
    layer_blocks: list[dict] = []
    for layer in layers:
        mine = [f for f in files if f.layer_id == layer["id"]]
        prev = last.get(layer["id"])
        infos = []
        known = hashes.get(layer["id"], {})
        for f in mine:
            # unknown fingerprint (never synced, or synced before this feature)
            # counts as changed, so the first sync is always offered
            changed = known.get(f.absolute_path) != f.fingerprint
            if changed:
                changed_count += 1
            infos.append(as_info(f, changed))
        layer_blocks.append({
            "layerId": layer["id"], "layerName": layer["name"], "files": infos,
            "conflict": len(mine) > 1,
            "changed": any(i["changed"] for i in infos),
            "lastSyncedAt": prev.get("uploadedAt") if prev else None,
            "lastSyncedFile": prev.get("fileName") if prev else None,
        })

    return {**base, "ok": True, "layers": layer_blocks,
            "unmatchedFiles": [as_info(f, False) for f in files if f.layer_id is None],
            "changedCount": changed_count}


@router.get("/apps/{app_id}/source", response_model=SourceStatus)
async def get_source(app_id: Slug):
    app = await repo.get_app(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return await _source_status(app_id, app)


@router.post("/apps/{app_id}/sync", response_model=SyncResult)
async def sync_from_source(app_id: Slug, body: SyncRequest, user: dict = _qa):
    """Read the configured workbooks and ingest them.

    `mode` mirrors the old upload choice: merge upserts row-by-row, replace
    wipes the layer first. `layers` names the workbooks to use — several files
    for one layer are read in order into the same layer, which is how a split
    catalog gets merged. Omitting it syncs every layer that has exactly one
    matched workbook, so an unresolved conflict is never guessed at.
    """
    app = await repo.get_app(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    status = await _source_status(app_id, app)
    if not status["ok"]:
        raise HTTPException(status_code=400, detail=status["error"])

    by_layer = {b["layerId"]: b for b in status["layers"]}
    if body.layers is None:
        chosen = [(b["layerId"], [f["relativePath"] for f in b["files"]])
                  for b in status["layers"] if len(b["files"]) == 1]
    else:
        chosen = [(c.layer_id, c.files) for c in body.layers]
    if not chosen:
        raise HTTPException(
            status_code=400,
            detail="Nothing to sync — no workbook is matched to a layer. "
                   "Name each file after its layer, or choose the files explicitly.")

    root = status["root"]
    rel = status["relativePath"]
    now = _now()
    results: list[dict] = []

    for layer_id, rel_files in chosen:
        block = by_layer.get(layer_id)
        if block is None:
            results.append({"layerId": layer_id, "layerName": layer_id, "files": rel_files,
                            "error": "This layer no longer exists."})
            continue
        # an explicit choice may name a workbook the name-matching couldn't
        # place (listed as unmatched) — the user's assignment wins
        known = {f["relativePath"] for f in block["files"]}
        known |= {f["relativePath"] for f in status["unmatchedFiles"]}
        picked = [p for p in rel_files if p in known] or []
        if not picked:
            results.append({"layerId": layer_id, "layerName": block["layerName"],
                            "files": rel_files,
                            "error": "No matching workbook was found for this layer."})
            continue

        totals = {"totalRows": 0, "inserted": 0, "updated": 0, "unchanged": 0,
                  "duplicatesSkipped": 0}
        error: str | None = None
        # replace applies once, to the first file; the rest merge on top so a
        # layer split across workbooks ends up with all of their rows
        mode_for_file = body.mode
        for rel_file in picked:
            try:
                path = excel_source.resolve_app_folder(root, rel) / rel_file
                data = excel_source.read_workbook_bytes(path)
                parsed = parse_workbook(data)
            except (SourceError, IngestError) as e:
                error = e.message
                break
            upload_doc = {
                "_id": uuid.uuid4().hex, "appId": app_id, "layerId": layer_id,
                "fileName": path.name, "fileSize": len(data),
                "sourcePath": str(path), "fileHash": excel_source.fingerprint_file(path),
                "columns": parsed.columns, "sections": parsed.sections,
                "totalRows": parsed.total_rows,
                "duplicatesSkipped": parsed.duplicates_skipped,
                "uploadedBy": user["username"], "uploadedAt": _now(),
            }
            counts = await repo.merge_layer_data(
                app_id, layer_id, upload_doc, parsed.rows,
                replace=(mode_for_file == "replace"))
            totals["totalRows"] += parsed.total_rows
            totals["duplicatesSkipped"] += parsed.duplicates_skipped
            for k in ("inserted", "updated", "unchanged"):
                totals[k] += counts[k]
            mode_for_file = "merge"

        results.append({"layerId": layer_id, "layerName": block["layerName"],
                        "files": picked, "error": error, **totals})

    return {"syncedAt": now, "mode": body.mode, "layers": results}


# --- direct browser upload (kept alongside folder sync) -----------------


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
