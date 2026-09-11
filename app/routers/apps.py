"""Apps, releases, layers, Excel ingestion, records and dashboards.

The hierarchy is application -> release -> testing layer -> rows. A release
owns its layers outright: their column definitions, uploads and records are
all keyed by (appId, releaseId, layerId), so a new release can be loaded from
workbooks of an entirely different shape without touching what earlier
releases hold.

Data arrives one way: the server reads the release's own folder on disk and
records a snapshot per workbook (`POST .../snapshots`). One file is one
dataset — two workbooks feeding the same testing type keep separate records,
columns, metrics and history, and are never merged. A snapshot is complete and
read-only, so loading again writes a new one rather than changing what is
there, and every earlier reading stays exactly as it was taken. Browser upload is
commented out below rather than deleted — see the block near the end.

Reads are open to any authenticated role; syncs need qa+; structural changes
(create/update/delete apps, releases and layers, purging ingested data) need
admin.
"""
import re
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, UploadFile

from app import repositories as repo
from app.layer_defaults import copied_layer_docs, default_layer_docs, layer_id as layer_key
from app.models import (
    AppCreate,
    LayerFileInfo,
    AppSummary,
    AppUpdate,
    LayerCreate,
    LayerDashboardResponse,
    LayerInfo,
    LayerRecordsResponse,
    LayerUpdate,
    ReleaseCreate,
    ReleaseInfo,
    ReleaseUpdate,
    SnapshotInfo,
    SnapshotRequest,
    SnapshotRunResult,
    SnapshotUpdate,
    SourceStatus,
    UploadResult,
)
from app.security import get_current_user, require_role
from app.services import excel_source
from app.services import profile as prof
from app.services import quality
from app.services.excel_ingest import (
    MAX_FILE_BYTES,
    IngestError,
    diff_rows,
    parse_workbook,
)
from app.services.excel_source import SourceError

router = APIRouter(tags=["apps"], dependencies=[Depends(get_current_user)])

Slug = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]

_admin = Depends(require_role("admin"))
_qa = Depends(require_role("qa"))

# Read selectors, shared by the records, dashboard and history endpoints.
# `file` picks which workbook's dataset to read — the most recently loaded one
# when it is omitted. Snapshots are never combined across files.
SnapshotQuery = Query(default=None, max_length=64)
FileQuery = Query(default=None, max_length=400)
# metrics added up across every file's current snapshot, for the dashboard.
# A read-time view only — records are always stored one file per snapshot.
MergedQuery = Query(default=False)
MonthQuery = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
# which column a results dashboard breaks its figures down by. A column this
# workbook does not have falls back to the default rather than erroring.
DimensionQuery = Query(default="", max_length=120)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]
    if not slug:
        raise HTTPException(status_code=422, detail="Name must contain letters or digits")
    return slug


async def _require_app(app_id: str) -> dict:
    app = await repo.get_app(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


async def _require_release(app_id: str, release_id: str) -> dict:
    await _require_app(app_id)
    release = await repo.get_release(app_id, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")
    return release


async def _require_layer(app_id: str, release_id: str, layer_id: str) -> dict:
    await _require_release(app_id, release_id)
    layer = await repo.get_layer(app_id, release_id, layer_id)
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
    # an application needs somewhere to put data, so it opens with one
    # release carrying the global default pyramid
    release_id = slugify(body.release_name)
    # excelPath stays blank: the folder is derived from the names, and only an
    # application whose files sit somewhere else needs to override it
    await repo.create_release({
        "_id": f"{app_id}:{release_id}", "appId": app_id, "releaseId": release_id,
        "name": body.release_name, "desc": "", "excelPath": "",
        "current": True, "order": 0, "createdAt": now, "updatedAt": now,
    })
    await repo.create_layers(default_layer_docs(app_id, release_id, now))
    return {"id": app_id, "name": body.name, "tag": body.tag, "desc": body.desc,
            "icon": body.icon,
            "releaseCount": 1, "recordCount": 0,
            "currentRelease": body.release_name, "currentReleaseId": release_id}


@router.patch("/apps/{app_id}", response_model=AppSummary, dependencies=[_admin])
async def patch_app(app_id: Slug, body: AppUpdate):
    await _require_app(app_id)
    patch = body.model_dump(exclude_none=True, by_alias=True)
    if patch:
        await repo.update_app(app_id, patch)
    apps = await repo.list_apps()
    return next(a for a in apps if a["id"] == app_id)


@router.delete("/apps/{app_id}", status_code=204, dependencies=[_admin])
async def delete_app(app_id: Slug):
    await _require_app(app_id)
    await repo.delete_app_cascade(app_id)


# --- releases -----------------------------------------------------------


@router.get("/apps/{app_id}/releases", response_model=list[ReleaseInfo])
async def get_releases(app_id: Slug):
    await _require_app(app_id)
    return await repo.list_releases(app_id)


@router.post("/apps/{app_id}/releases", response_model=ReleaseInfo, status_code=201,
             dependencies=[_admin])
async def post_release(app_id: Slug, body: ReleaseCreate):
    await _require_app(app_id)
    release_id = slugify(body.name)
    if await repo.get_release(app_id, release_id) is not None:
        raise HTTPException(status_code=409, detail=f'A release "{release_id}" already exists')

    # the layer structure is copied from an existing release when asked for,
    # otherwise the release starts from the global default pyramid. Data and
    # column definitions are never copied — the new release is empty until its
    # own workbooks are loaded, and they may have a different shape.
    now = _now()
    if body.copy_layers_from:
        source = await repo.get_release(app_id, body.copy_layers_from)
        if source is None:
            raise HTTPException(
                status_code=404, detail="The release to copy layers from was not found")
        source_layers = await repo.list_layer_docs(app_id, body.copy_layers_from)
        layers = copied_layer_docs(app_id, release_id, source_layers, now)
    else:
        layers = default_layer_docs(app_id, release_id, now)

    order = await repo.next_release_order(app_id)
    await repo.create_release({
        "_id": f"{app_id}:{release_id}", "appId": app_id, "releaseId": release_id,
        "name": body.name, "desc": body.desc,
        # blank unless the caller pointed this release somewhere specific
        "excelPath": body.excel_path.strip(),
        "current": False, "order": order, "createdAt": now, "updatedAt": now,
    })
    await repo.create_layers(layers)
    if body.make_current:
        await repo.set_current_release(app_id, release_id)

    releases = await repo.list_releases(app_id)
    return next(r for r in releases if r["id"] == release_id)


@router.patch("/apps/{app_id}/releases/{release_id}", response_model=ReleaseInfo,
              dependencies=[_admin])
async def patch_release(app_id: Slug, release_id: Slug, body: ReleaseUpdate):
    await _require_release(app_id, release_id)
    patch = body.model_dump(exclude_none=True, by_alias=True)
    # the id is derived from the original name and stays put, so links to a
    # release survive a rename
    make_current = patch.pop("current", None)
    if patch:
        await repo.update_release(app_id, release_id, patch)
    if make_current:
        await repo.set_current_release(app_id, release_id)
    releases = await repo.list_releases(app_id)
    return next(r for r in releases if r["id"] == release_id)


@router.delete("/apps/{app_id}/releases/{release_id}", status_code=204, dependencies=[_admin])
async def delete_release(app_id: Slug, release_id: Slug):
    await _require_release(app_id, release_id)
    await repo.delete_release_cascade(app_id, release_id)


# --- layers -------------------------------------------------------------


@router.get("/apps/{app_id}/releases/{release_id}/layers", response_model=list[LayerInfo])
async def get_layers(app_id: Slug, release_id: Slug):
    await _require_release(app_id, release_id)
    return await repo.list_layers(app_id, release_id)


@router.post("/apps/{app_id}/releases/{release_id}/layers", response_model=LayerInfo,
             status_code=201, dependencies=[_admin])
async def post_layer(app_id: Slug, release_id: Slug, body: LayerCreate):
    await _require_release(app_id, release_id)
    layer_id = slugify(body.name)
    if await repo.get_layer(app_id, release_id, layer_id) is not None:
        raise HTTPException(status_code=409, detail=f'A layer "{layer_id}" already exists')
    # `order` is the pyramid slot the new layer is dropped into, bottom-first:
    # 0 is the base (most test cases), len(existing) the tip (fewest). Layers
    # from that slot upwards shift up one, and the stack is renumbered so
    # positions stay contiguous even after earlier deletions. Only this
    # release's pyramid changes — every other release keeps its own.
    existing = sorted(await repo.list_layers(app_id, release_id), key=lambda l: l["order"])
    order = len(existing) if body.order is None else min(body.order, len(existing))
    shifted: dict[str, int] = {}
    for i, layer in enumerate(existing):
        new_order = i if i < order else i + 1
        if new_order != layer["order"]:
            shifted[layer["id"]] = new_order
    await repo.set_layer_orders(app_id, release_id, shifted)

    now = _now()
    await repo.create_layer({
        "_id": layer_key(app_id, release_id, layer_id), "appId": app_id,
        "releaseId": release_id, "layerId": layer_id,
        "name": body.name, "short": body.short, "desc": body.desc, "order": order,
        "createdAt": now, "updatedAt": now,
    })
    return {"id": layer_id, "name": body.name, "short": body.short, "desc": body.desc,
            "order": order, "recordCount": 0}


@router.patch("/apps/{app_id}/releases/{release_id}/layers/{layer_id}",
              response_model=LayerInfo, dependencies=[_admin])
async def patch_layer(app_id: Slug, release_id: Slug, layer_id: Slug, body: LayerUpdate):
    await _require_layer(app_id, release_id, layer_id)
    patch = body.model_dump(exclude_none=True)
    if patch:
        await repo.update_layer(app_id, release_id, layer_id, patch)
    layers = await repo.list_layers(app_id, release_id)
    return next(l for l in layers if l["id"] == layer_id)


@router.delete("/apps/{app_id}/releases/{release_id}/layers/{layer_id}", status_code=204,
               dependencies=[_admin])
async def delete_layer(app_id: Slug, release_id: Slug, layer_id: Slug):
    await _require_layer(app_id, release_id, layer_id)
    await repo.delete_layer_cascade(app_id, release_id, layer_id)
    # close the gap the removed layer left, so pyramid positions stay contiguous
    remaining = sorted(await repo.list_layers(app_id, release_id), key=lambda l: l["order"])
    await repo.set_layer_orders(
        app_id, release_id, {l["id"]: i for i, l in enumerate(remaining) if l["order"] != i})


# --- Excel source (path-based ingestion) --------------------------------


async def _source_status(app_id: str, app: dict, release: dict) -> dict:
    """Inspect one release's folder: which workbook feeds which layer, and
    what has changed since the last sync. Never raises for a bad path — the
    problem is returned as a message the UI can show."""
    settings = await repo.get_settings_doc() or {}
    root = settings.get("excelRoot", "")
    # one path: the release's override if it has one, else <app>/<release>
    rel = excel_source.release_folder_path(app, release)
    release_id = release["releaseId"]
    layers = sorted(await repo.list_layers(app_id, release_id), key=lambda l: l["order"])

    base = {"root": root, "folder": rel,
            "customFolder": bool((release.get("excelPath") or "").strip()),
            "resolvedPath": "", "releaseId": release_id, "releaseName": release["name"],
            "ok": False, "layers": [], "unmatchedFiles": [], "changedCount": 0}
    try:
        folder = excel_source.resolve_release_folder(root, rel)
        base["resolvedPath"] = str(folder)
        files = excel_source.scan_release_folder(root, rel, layers)
    except SourceError as e:
        return {**base, "errorCode": e.code, "error": e.message}

    # the current snapshot of every file, so a workbook edited since its own
    # last load can be flagged — each file is judged on its own history
    last: dict[str, dict] = {}
    hashes: dict[str, dict[str, str]] = {}
    for layer in layers:
        current = await repo.list_current_snapshots(app_id, release_id, layer["id"])
        if current:
            last[layer["id"]] = current[0]  # most recently loaded file
        hashes[layer["id"]] = {
            doc["file"]: (doc.get("sources") or [{}])[0].get("fileHash", "")
            for doc in current
        }

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
            # a file the current snapshot was not built from counts as changed,
            # so the first load of a testing type is always offered
            changed = known.get(f.relative_path) != f.fingerprint
            if changed:
                changed_count += 1
            infos.append(as_info(f, changed))
        layer_blocks.append({
            "layerId": layer["id"], "layerName": layer["name"], "files": infos,
            "changed": any(i["changed"] for i in infos),
            "lastSyncedAt": prev.get("createdAt") if prev else None,
            "lastSyncedFile": PurePosixPath(prev["file"]).name if prev else None,
        })

    return {**base, "ok": True, "layers": layer_blocks,
            "unmatchedFiles": [as_info(f, False) for f in files if f.layer_id is None],
            "changedCount": changed_count}


@router.get("/apps/{app_id}/releases/{release_id}/source", response_model=SourceStatus)
async def get_source(app_id: Slug, release_id: Slug):
    app = await _require_app(app_id)
    release = await _require_release(app_id, release_id)
    return await _source_status(app_id, app, release)


@router.post("/apps/{app_id}/releases/{release_id}/snapshots",
             response_model=SnapshotRunResult)
async def create_snapshots(app_id: Slug, release_id: Slug, body: SnapshotRequest,
                           user: dict = _qa):
    """Read this release's workbooks and record a snapshot per workbook.

    One file is one dataset: two workbooks feeding the same testing type each
    get their own snapshot, their own columns and their own history, and are
    never put together. There is no merge-or-replace choice — an earlier
    snapshot is never touched — and a file whose data is unchanged since its
    own last snapshot records nothing.

    `layers` names the workbooks to read; omitting it reads every workbook
    matched to a testing type.
    """
    app = await _require_app(app_id)
    release = await _require_release(app_id, release_id)
    status = await _source_status(app_id, app, release)
    if not status["ok"]:
        raise HTTPException(status_code=400, detail=status["error"])

    now = _now()
    period = ({"year": body.period.year, "month": body.period.month}
              if body.period else {"year": now.year, "month": now.month})

    by_layer = {b["layerId"]: b for b in status["layers"]}
    if body.layers is None:
        chosen = [(b["layerId"], [f["relativePath"] for f in b["files"]])
                  for b in status["layers"] if b["files"]]
    else:
        chosen = [(c.layer_id, c.files) for c in body.layers]
    if not chosen:
        raise HTTPException(
            status_code=400,
            detail="Nothing to load — no workbook is matched to a testing type. "
                   "Name each file after its type, or choose the files explicitly.")

    root, rel = status["root"], status["folder"]
    # every path the scan just found, matched or not. A workbook missing from
    # this set is one that is no longer on disk — which is what distinguishes a
    # rename from a copy when a new name turns up holding identical content.
    present = {f["relativePath"] for b in status["layers"] for f in b["files"]}
    present |= {f["relativePath"] for f in status["unmatchedFiles"]}
    results: list[dict] = []

    for layer_id, rel_files in chosen:
        block = by_layer.get(layer_id)
        if block is None:
            results.append({"layerId": layer_id, "layerName": layer_id, "file": "",
                            "fileName": "",
                            "error": "This testing type no longer exists in this release."})
            continue
        # an explicit choice may name a workbook the name-matching couldn't
        # place (listed as unmatched) — the user's assignment wins
        known = {f["relativePath"] for f in block["files"]}
        known |= {f["relativePath"] for f in status["unmatchedFiles"]}
        picked = [p for p in rel_files if p in known]
        if not picked:
            results.append({"layerId": layer_id, "layerName": block["layerName"],
                            "file": "", "fileName": "",
                            "error": "No matching workbook was found for this testing type."})
            continue

        for rel_file in picked:
            results.append(
                await _snapshot_one_file(app_id, release_id, layer_id,
                                         block["layerName"], root, rel, rel_file,
                                         period, user["username"], present))

    return {"createdAt": now, "releaseId": release_id, "period": period,
            "files": results,
            "warnings": await _duplicate_file_warnings(
                app_id, release_id, {layer_id for layer_id, _ in chosen})}


async def _duplicate_file_warnings(app_id: str, release_id: str,
                                   layer_ids: set[str]) -> list[dict]:
    """Layers holding two workbooks with identical contents.

    A rename is detected and folded away, so what reaches here is a *copy*: both
    files are still on disk, both are real datasets by the rule this system
    runs on, and their rows are counted twice in the layer's total. Nothing can
    honestly merge them — files are never combined — so the load says so
    instead of quietly reporting double.
    """
    out: list[dict] = []
    for layer_id in sorted(layer_ids):
        by_hash: dict[str, list[str]] = {}
        for snapshot in await repo.list_current_snapshots(app_id, release_id, layer_id):
            digest = snapshot.get("contentHash") or ""
            if digest:
                by_hash.setdefault(digest, []).append(
                    PurePosixPath(snapshot.get("file", "")).name)
        for files in by_hash.values():
            if len(files) > 1:
                out.append({
                    "code": "identical_workbooks",
                    "severity": "problem",
                    "message": f"{' and '.join(sorted(files))} hold identical "
                               f"contents. They are separate datasets, so their "
                               f"rows are counted twice in this testing type's "
                               f"total — remove one, or change what it contains.",
                })
    return out


async def _snapshot_one_file(app_id: str, release_id: str, layer_id: str,
                             layer_name: str, root: str, rel: str, rel_file: str,
                             period: dict, username: str,
                             present: set[str] | None = None) -> dict:
    """Read one workbook and record it, if it says anything new.

    Everything here is scoped to this one file: its own previous snapshot, its
    own content hash, its own diff. Nothing consults or touches the other
    workbooks feeding the same testing type.
    """
    base = {"layerId": layer_id, "layerName": layer_name,
            "file": rel_file, "fileName": PurePosixPath(rel_file).name}
    try:
        path = excel_source.resolve_release_folder(root, rel) / rel_file
        data = excel_source.read_workbook_bytes(path)
        parsed = parse_workbook(data)
    except (SourceError, IngestError) as e:
        return {**base, "error": e.message}

    # What is wrong with this workbook is a property of the workbook: computed
    # from this parse alone, never from what was loaded before it, and reported
    # even when the load writes nothing — a bad sheet that has not changed is
    # still a bad sheet, and a reload that came back clean would say otherwise.
    warnings = quality.inspect(parsed)

    # A name with no history of its own might be a rename rather than a new
    # workbook. Renaming makes no new data, so forking here would leave the old
    # series behind to be counted alongside this one — and a layer's record
    # count sums across its files, so the pyramid check would read double.
    renamed_from = ""
    if present is not None and \
            await repo.latest_snapshot(app_id, release_id, layer_id, rel_file) is None:
        known = {s["file"]: s.get("contentHash", "")
                 for s in await repo.list_current_snapshots(app_id, release_id, layer_id)}
        renamed_from = excel_source.detect_rename(known, present, parsed.content_hash)
        if renamed_from:
            await repo.move_file_series(app_id, release_id, layer_id,
                                        renamed_from, rel_file)
    base = {**base, "renamedFrom": renamed_from}

    # the moved series is this file's history now, so everything below is the
    # ordinary path: identical content, therefore nothing to write
    previous = await repo.latest_snapshot(app_id, release_id, layer_id, rel_file)
    if previous is not None and previous.get("contentHash") == parsed.content_hash:
        moved = f"Renamed from {PurePosixPath(renamed_from).name}. " if renamed_from else ""
        return {**base, "created": False,
                "rowCount": previous.get("rowCount", 0),
                "totalRows": parsed.total_rows,
                "duplicatesSkipped": parsed.duplicates_skipped,
                "warnings": warnings,
                "reason": f'{moved}No change since snapshot #{previous.get("sequence")}.'}

    # compared with this file's own previous snapshot, when the two were keyed
    # the same way — a changed sheet format makes rows incomparable
    if previous is None:
        diff = {"comparable": True, "comparedTo": None,
                "added": len(parsed.rows), "changed": 0, "removed": 0}
    elif previous.get("identityKeys") != parsed.identity_keys:
        diff = {"comparable": False, "comparedTo": previous.get("sequence"),
                "added": 0, "changed": 0, "removed": 0,
                "reason": "The columns that identify a row changed, so rows "
                          "cannot be matched against the previous snapshot."}
    else:
        counts = diff_rows(await repo.snapshot_rows(previous["_id"]), parsed.rows)
        diff = {**counts, "comparedTo": previous.get("sequence")}

    stat = path.stat()
    snapshot = {
        "_id": uuid.uuid4().hex,
        "appId": app_id, "releaseId": release_id, "layerId": layer_id,
        "file": rel_file,
        "sequence": await repo.next_snapshot_sequence(
            app_id, release_id, layer_id, rel_file),
        "period": period,
        "contentHash": parsed.content_hash,
        "identityKeys": parsed.identity_keys,
        "columns": parsed.columns,
        "sections": parsed.sections,
        "sources": [{
            "relativePath": rel_file, "fileName": path.name,
            # the bytes actually parsed — re-reading the file to hash it would
            # record a version that was never ingested
            "fileHash": excel_source.fingerprint_bytes(data),
            "sizeBytes": len(data),
            "modifiedAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            "rowsRead": parsed.total_rows,
            "duplicatesSkipped": parsed.duplicates_skipped,
        }],
        "rowCount": len(parsed.rows),
        "totalRows": parsed.total_rows,
        "duplicatesSkipped": parsed.duplicates_skipped,
        "diff": diff,
        # kept with the reading they describe, the way `diff` is: the rows the
        # parse discarded are gone, so these cannot be recomputed later
        "warnings": warnings,
        "createdAt": _now(),
        "createdBy": username,
    }
    await repo.create_snapshot(snapshot, parsed.rows)
    return {**base, "created": True, "snapshotId": snapshot["_id"],
            "sequence": snapshot["sequence"], "rowCount": snapshot["rowCount"],
            "totalRows": parsed.total_rows,
            "duplicatesSkipped": parsed.duplicates_skipped, "diff": diff,
            "warnings": warnings}


@router.get("/apps/{app_id}/releases/{release_id}/layers/{layer_id}/files",
            response_model=list[LayerFileInfo])
async def get_layer_files(app_id: Slug, release_id: Slug, layer_id: Slug):
    """The workbooks this testing type holds data from, most recently loaded
    first — one entry per file, each an independent dataset."""
    await _require_layer(app_id, release_id, layer_id)
    current = await repo.list_current_snapshots(app_id, release_id, layer_id)
    return [
        {
            "file": doc["file"],
            "fileName": PurePosixPath(doc["file"]).name or doc["file"],
            "snapshotId": doc["_id"],
            "sequence": doc.get("sequence", 0),
            "period": doc.get("period"),
            "loadedAt": doc.get("createdAt"),
            "rowCount": doc.get("rowCount", 0),
            "columnCount": len(doc.get("columns", [])),
            "snapshotCount": doc.get("snapshotCount", 0),
            "combined": bool(doc.get("combined")),
        }
        for doc in current
    ]


@router.get("/apps/{app_id}/releases/{release_id}/layers/{layer_id}/snapshots",
            response_model=list[SnapshotInfo])
async def get_snapshots(app_id: Slug, release_id: Slug, layer_id: Slug,
                        file: str | None = FileQuery):
    """This testing type's history — every file's snapshots together, newest
    load first. Naming a file narrows it to that one."""
    await _require_layer(app_id, release_id, layer_id)
    return await repo.list_snapshots(app_id, release_id, layer_id, file)


@router.patch("/apps/{app_id}/releases/{release_id}/snapshots/{snapshot_id}",
              response_model=SnapshotInfo, dependencies=[_admin])
async def patch_snapshot(app_id: Slug, release_id: Slug, snapshot_id: str,
                         body: SnapshotUpdate):
    """Correct which month a snapshot is filed under. Its data is immutable."""
    await _require_release(app_id, release_id)
    snapshot = await repo.get_snapshot(app_id, release_id, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    await repo.update_snapshot_period(
        app_id, release_id, snapshot_id,
        {"year": body.period.year, "month": body.period.month})
    return await repo.get_snapshot(app_id, release_id, snapshot_id)


@router.delete("/apps/{app_id}/releases/{release_id}/snapshots/{snapshot_id}",
               dependencies=[_admin])
async def delete_one_snapshot(app_id: Slug, release_id: Slug, snapshot_id: str):
    """Remove one snapshot and its rows.

    Used when a load was wrong: every other snapshot keeps its data — including
    the other files of the same testing type — the previous snapshot of this
    file becomes current again, and the corrected workbook can be loaded as the
    next snapshot in its series.
    """
    await _require_release(app_id, release_id)
    snapshot = await repo.get_snapshot(app_id, release_id, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    deleted = await repo.delete_snapshot(app_id, release_id, snapshot_id)
    return {"deleted": deleted, "snapshotId": snapshot_id,
            "sequence": snapshot.get("sequence"), "file": snapshot.get("file")}


# --- direct browser upload — DISABLED ------------------------------------
#
# Data is extracted from the release's folder on disk, not pushed up from a
# browser, so this endpoint is switched off. It is kept rather than deleted, but
# it can no longer simply be uncommented: it was written against the old
# merge-in-place ingestion, and `repo.merge_layer_data` no longer exists.
#
# To restore it, keep the request handling below and replace the storage half
# with what create_snapshots does — combine_sheets over the one uploaded
# workbook, compare its content hash with the current snapshot, and call
# repo.create_snapshot. An upload would then be one more way to record a
# snapshot rather than a second, different way to store data.

# # --- direct browser upload (kept alongside folder sync) -----------------
#
#
# @router.post("/apps/{app_id}/releases/{release_id}/layers/{layer_id}/uploads",
#              response_model=UploadResult)
# async def upload_excel(app_id: Slug, release_id: Slug, layer_id: Slug, file: UploadFile,
#                        mode: Literal["merge", "replace"] = Query(default="merge"),
#                        user: dict = _qa):
#     await _require_layer(app_id, release_id, layer_id)
#     if not (file.filename or "").lower().endswith(".xlsx"):
#         raise HTTPException(status_code=400, detail="Only .xlsx files are supported")
#     data = await file.read(MAX_FILE_BYTES + 1)
#     try:
#         parsed = parse_workbook(data)
#     except IngestError as e:
#         raise HTTPException(status_code=400, detail=e.message)
#
#     upload_doc = {
#         "_id": uuid.uuid4().hex, "appId": app_id, "releaseId": release_id,
#         "layerId": layer_id,
#         "fileName": file.filename, "fileSize": len(data),
#         "columns": parsed.columns, "sections": parsed.sections,
#         "totalRows": parsed.total_rows, "duplicatesSkipped": parsed.duplicates_skipped,
#         "uploadedBy": user["username"], "uploadedAt": _now(),
#     }
#     counts = await repo.merge_layer_data(app_id, release_id, layer_id, upload_doc,
#                                          parsed.rows, replace=(mode == "replace"))
#     return {
#         "uploadId": upload_doc["_id"], "fileName": upload_doc["fileName"],
#         "columns": parsed.columns, "sections": parsed.sections,
#         "totalRows": parsed.total_rows, "duplicatesSkipped": parsed.duplicates_skipped,
#         "uploadedBy": upload_doc["uploadedBy"], "uploadedAt": upload_doc["uploadedAt"],
#         **counts,
#     }


async def _read_snapshot(app_id: str, release_id: str, layer_id: str,
                         file: str | None, snapshot: str | None,
                         month: str | None) -> dict | None:
    """Which snapshot a read is about.

    The file picks the dataset — the most recently loaded one when none is
    named — and the snapshot or month picks which of its loads. None when the
    testing type has never been loaded.
    """
    year, number = (int(month[:4]), int(month[5:])) if month else (None, None)
    return await repo.resolve_snapshot(
        app_id, release_id, layer_id, file=file,
        snapshot_id=snapshot, year=year, month=number)


@router.get("/apps/{app_id}/releases/{release_id}/layers/{layer_id}/records",
            response_model=LayerRecordsResponse)
async def get_records(
    app_id: Slug, release_id: Slug, layer_id: Slug,
    search: str | None = Query(default=None, max_length=200),
    section: str | None = Query(default=None, max_length=200),
    file: str | None = FileQuery,
    snapshot: str | None = SnapshotQuery,
    month: str | None = MonthQuery,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=250, ge=1, le=500, alias="pageSize"),
):
    await _require_layer(app_id, release_id, layer_id)
    found = await _read_snapshot(app_id, release_id, layer_id, file, snapshot, month)
    if found is None:
        return {"columns": [], "snapshot": None, "total": 0,
                "page": page, "pageSize": page_size, "sections": []}
    # the schema of this snapshot, which may differ from any other
    columns = found.get("columns", [])
    total, docs = await repo.list_records(
        found["id"], columns, search, section,
        skip=(page - 1) * page_size, limit=page_size,
    )
    sections: list[dict] = []
    for d in docs:
        if not sections or sections[-1]["name"] != d["section"]:
            sections.append({"name": d["section"], "rowCount": 0, "rows": []})
        sections[-1]["rows"].append({"section": d["section"], "data": d["data"]})
        sections[-1]["rowCount"] += 1
    return {
        "columns": columns, "snapshot": found,
        "total": total, "page": page, "pageSize": page_size, "sections": sections,
    }


@router.get("/apps/{app_id}/releases/{release_id}/layers/{layer_id}/dashboard",
            response_model=LayerDashboardResponse)
async def get_dashboard(app_id: Slug, release_id: Slug, layer_id: Slug,
                        file: str | None = FileQuery,
                        snapshot: str | None = SnapshotQuery,
                        month: str | None = MonthQuery,
                        merged: bool = MergedQuery,
                        dimension: str = DimensionQuery):
    """Metrics for one workbook, or — with `merged` — for every file at once.

    Merging adds up the current snapshot of each file at read time. The stored
    records never change: they stay one file per snapshot, and asking for the
    same testing type file by file gives the same numbers back.
    """
    await _require_layer(app_id, release_id, layer_id)
    if merged:
        files = await get_layer_files(app_id, release_id, layer_id)
        # merging one file would just be that file, reported confusingly — so a
        # testing type with a single workbook answers as itself. This lets the
        # dashboard ask for merged by default without a special case per type.
        if len(files) > 1:
            stats = await repo.aggregate_merged_dashboard(app_id, release_id, layer_id)
            return {"snapshot": None, "merged": True, "mergedFiles": files, **stats}
        file = files[0]["file"] if files else None
    found = await _read_snapshot(app_id, release_id, layer_id, file, snapshot, month)
    if found is None:
        return {"snapshot": None, "totalRows": 0, "sectionCount": 0,
                "numericColumns": [], "totals": {}, "bySection": [], "topRows": []}
    columns = found.get("columns", [])
    stats = await repo.aggregate_dashboard(found["id"], columns)
    profile = await _dashboard_profile(found["id"], columns, stats, dimension)
    # what was wrong with the workbook these figures came from, as recorded
    # when it was read. Older snapshots predate the check and carry none.
    return {"snapshot": found, **stats, "profile": profile,
            "warnings": found.get("warnings", [])}


async def _dashboard_profile(snapshot_id: str, columns: list[dict], stats: dict,
                             requested_dimension: str) -> dict:
    """What kind of data this workbook holds, and the breakdown that suits it.

    A sheet that only counts things keeps the view it always had — the profile
    comes back as `volume` and nothing else in the response changes. The extra
    queries below run only for a sheet that turned out to hold something the
    count-and-group view cannot show.
    """
    rows = stats.get("totalRows", 0)
    # one round trip; needed to tell a grouping column from an identifier, and
    # an outcome column from a column merely named like one
    col_stats = await repo.column_stats(snapshot_id, columns)
    # the raw per-column sums: the run profile looks up `Pass` and `Fail`
    # by the sheet's own names, not by a logical measure
    shape = prof.describe(columns, stats.get("rawTotals", stats.get("totals", {})),
                          col_stats, rows)
    if shape["kind"] == "volume":
        return shape

    distincts = {key: info["distinct"] for key, info in col_stats.items()}
    if shape["kind"] == "status":
        return await _status_profile(snapshot_id, columns, shape, col_stats,
                                     distincts, rows, requested_dimension)
    if shape["kind"] == "inventory":
        records = await repo.snapshot_rows(snapshot_id, prof.MAX_INVENTORY_ROWS)
        return {**shape,
                "inventory": prof.summarise_inventory(records, shape["inventory"])}

    run = shape["runResults"]
    dimensions = prof.rank_dimensions(columns, distincts, rows)
    dimension = prof.choose_dimension(dimensions, requested_dimension)

    measures = {role: run.get(f"{role}Column", "")
                for role in ("passed", "failed", "notRun", "total")}
    buckets = await repo.dimension_breakdown(
        snapshot_id, dimension, [k for k in measures.values() if k])
    # the aggregation keys buckets by column key; the screen reads them by role
    by_dimension = [
        prof.bucket_rate({
            "value": b["value"], "rowCount": b["rowCount"],
            **{role: float(b.get(key) or 0) for role, key in measures.items() if key},
        }, run)
        for b in buckets
    ]

    return {**shape, "dimensions": dimensions, "dimension": dimension,
            "byDimension": by_dimension,
            "failingRows": await repo.rows_with_failures(
                snapshot_id, measures["failed"],
                prof.label_columns(columns, dimensions), measures)}


async def _status_profile(snapshot_id: str, columns: list[dict], shape: dict,
                          col_stats: dict, distincts: dict, rows: int,
                          requested_dimension: str) -> dict:
    """A sheet that records an outcome word against each row.

    Weighted by whatever the sheet counts, because a row is not a test: the
    regression sheet's four rows carry 12, 18, 9 and 14 test cases, so three
    passing rows out of four is 75% while the cases behind them are 83%.
    """
    status = shape["status"]
    status_key = status["statusColumn"]
    measure = prof.primary_measure(columns)

    buckets = await repo.status_breakdown(snapshot_id, status_key,
                                          measure["key"] if measure else "")
    summary = prof.summarise_status(buckets, status, measure)

    # breaking the status down by the status would say nothing
    dimensions = [d for d in prof.rank_dimensions(columns, distincts, rows)
                  if d["key"] != status_key]
    dimension = prof.choose_dimension(dimensions, requested_dimension)
    by_dimension = []
    if dimension:
        raw = await repo.dimension_breakdown(
            snapshot_id, dimension, [measure["key"]] if measure else [])
        by_dimension = [{"value": b["value"], "rowCount": b["rowCount"]} for b in raw]

    # what is worth acting on: failures first, then whatever nobody has run
    open_values = [s["value"] for s in summary["statuses"]
                   if s["kind"] in ("failing", "pending")]
    return {**shape, "status": summary,
            "dimensions": dimensions, "dimension": dimension,
            "byDimension": by_dimension,
            "openRows": await repo.rows_with_status(
                snapshot_id, status_key, open_values,
                prof.label_columns(columns, dimensions, col_stats, (status_key,)),
                measure["key"] if measure else "")}


@router.delete("/apps/{app_id}/releases/{release_id}/layers/{layer_id}/records",
               dependencies=[_admin])
async def delete_records(app_id: Slug, release_id: Slug, layer_id: Slug,
                         file: str | None = FileQuery):
    """Remove every snapshot of one testing type, or of one of its files.

    To undo a single bad load, delete that snapshot instead — this drops a
    whole history.
    """
    await _require_layer(app_id, release_id, layer_id)
    deleted = await repo.delete_layer_snapshots(app_id, release_id, layer_id, file)
    return {"deleted": deleted, "scope": file or "all"}
