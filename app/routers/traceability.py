"""Bidirectional Feature <-> System coverage, per release.

Two validations over one pair of testing layers:

    Feature -> System   every feature planned for this release is covered by a
                        system requirement
    System  -> Feature  every item in system scope is represented at feature
                        level

Both are answered from one matrix, because they are two readings of the same
comparison and splitting them into two reports would let them disagree.

The comparison is computed when it is asked for, from the current snapshot of
every workbook feeding each layer. Nothing is precomputed and nothing is
written, so — exactly like the dashboards — the figures cannot drift from the
rows they describe.

The shared identifier is read from the first column of each workbook, so there
is nothing to configure for the comparison to work. What can be stored is an
override — a different token pattern, or a different column for a sheet whose
first column is unusable — and a release that has never been touched still
gets a correct answer.

Reads are open to any authenticated role; changing the configuration is admin.
"""
import re
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app import repositories as repo
from app.models import (
    CoverageResponse,
    TraceConfig,
    TraceConfigUpdate,
    TracePreview,
)
from app.security import get_current_user, require_role
from app.services import coverage as cov
from app.services import quality

router = APIRouter(tags=["traceability"], dependencies=[Depends(get_current_user)])

Slug = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
_admin = Depends(require_role("admin"))

# The two layers compared. They are the default pyramid's own ids, and a
# release that renamed or dropped one is reported rather than guessed at.
FEATURE_LAYER = "feature"
SYSTEM_LAYER = "system"


async def _require_release(app_id: str, release_id: str) -> dict:
    if await repo.get_app(app_id) is None:
        raise HTTPException(status_code=404, detail="Application not found")
    release = await repo.get_release(app_id, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")
    return release


async def _load_side(app_id: str, release_id: str, layer_id: str) -> dict:
    """One side of the comparison: its rows, its shape and where they came from."""
    layer = await repo.get_layer(app_id, release_id, layer_id)
    side = await repo.coverage_rows(app_id, release_id, layer_id)
    side["layerId"] = layer_id
    side["layerName"] = (layer or {}).get("name", layer_id)
    side["exists"] = layer is not None
    return side


async def _load_sides(app_id: str, release_id: str) -> tuple[dict, dict]:
    return (await _load_side(app_id, release_id, FEATURE_LAYER),
            await _load_side(app_id, release_id, SYSTEM_LAYER))


def _layers_of(saved: dict | None) -> dict:
    """The per-layer overrides in force. Empty column means the rule."""
    layers = (saved or {}).get("layers") or {}
    return {name: {"column": (layers.get(name) or {}).get("column", "")}
            for name in (FEATURE_LAYER, SYSTEM_LAYER)}


def _config_payload(saved: dict | None, feature: dict, system: dict) -> dict:
    """The configuration in force, plus what it actually read.

    A release nobody has configured still gets a usable answer, because the
    identifier source is a rule rather than a setting. The admin screen
    exists to show what that rule read and to override it where a workbook
    defeats it — not to switch coverage on.
    """
    pattern = (saved or {}).get("pattern", "")
    layers = _layers_of(saved)
    read = {
        name: cov.describe_side(side["rows"], side["columns"], pattern,
                                layers[name]["column"])
        for name, side in ((FEATURE_LAYER, feature), (SYSTEM_LAYER, system))
    }
    return {
        "configured": bool(saved),
        "featureLayer": FEATURE_LAYER, "systemLayer": SYSTEM_LAYER,
        "pattern": pattern, "layers": layers, "read": read,
        "updatedAt": (saved or {}).get("updatedAt"),
        "updatedBy": (saved or {}).get("updatedBy", ""),
    }


def _id_columns(side: dict, override: str) -> list[dict]:
    """Which column each of this layer's workbooks was read for identifiers.

    Per file rather than per layer: two workbooks feeding one layer need not
    be shaped alike, and a reader who cannot see which column was used has no
    way to tell a real gap from a misread sheet.
    """
    labels = {c["key"]: c.get("label", c["key"]) for c in side["columns"]}
    names = side.get("fileNames") or {}
    # one row per file is enough to say whether an override's column is in it
    sample: dict[str, dict] = {}
    for row in side["rows"]:
        sample.setdefault(row.get("snapshotId", ""), row.get("data") or {})

    out = []
    for snapshot, column in (side.get("idColumns") or {}).items():
        if override and override in sample.get(snapshot, {}):
            column = override
        out.append({"fileName": names.get(snapshot, ""), "column": column,
                    "columnLabel": labels.get(column, column)})
    return out


def _layer_info(side: dict, override: str = "", family: str = "") -> dict:
    return {
        "layerId": side["layerId"], "layerName": side["layerName"],
        "rowCount": len(side["rows"]), "files": side["files"],
        "snapshotIds": side["snapshotIds"], "latestPeriod": side["latestPeriod"],
        "missing": not side["rows"],
        "idColumns": _id_columns(side, override), "family": family,
    }


def _structural_problem(feature: dict, system: dict) -> tuple[str, str] | None:
    """Why there is nothing to compare, or None when there is.

    Only about the data being there at all. Nothing about the identifier can
    fail here any more: every workbook has a first column, so the read always
    runs and what it produced is reported rather than pre-empted.
    """
    for side, name in ((feature, FEATURE_LAYER), (system, SYSTEM_LAYER)):
        if not side["exists"]:
            return "layer_missing", f"This release has no {name} testing layer."
    for side, name in ((feature, FEATURE_LAYER), (system, SYSTEM_LAYER)):
        if not side["rows"]:
            return "no_data", f"No {name} data has been loaded for this release yet."
    return None


def _no_ids(result: dict) -> tuple[str, str] | None:
    """A side whose first column yielded no identifiers at all.

    Reported as a failure rather than as coverage, because the arithmetic is
    vacuous: nothing on that side can be matched, nothing can be missing, and
    the percentages would read 100% off data nobody could read. That is the
    one way a coverage report is worse than no report.
    """
    for name in (FEATURE_LAYER, SYSTEM_LAYER):
        if not result["sides"][name]["occurrences"]:
            return ("no_ids",
                    f"No identifiers could be read from the first column of "
                    f"the {name} workbooks. Check the sheet, or set a pattern "
                    f"or column override under Matching.")
    return None


def _references(rows: list[dict]) -> dict[str, list[dict]]:
    """Feature rows whose text names another feature id, split two ways.

    Found by `quality.id_references`, the rule the load already ran, and per
    workbook exactly as the load read it: rows grouped by the snapshot they
    came from, keyed on that file's first column.

    `outside` names an id the row's own workbook does not list. Nothing is
    wrong with the file; whether that feature belongs in this release is a
    question about scope, so it is listed here and nowhere else.

    `mismatched` names an id that is another row of the same workbook —
    FL-5777 reading "CS-4800 - FL-5778 PBI: …". The load reports that as a
    fault in the file. It is listed here as well because it decides which
    feature the row's work belongs to, and so what a match on FL-5777 is worth.
    """
    by_file: dict[str, list[dict]] = {}
    for row in rows:
        by_file.setdefault(row.get("snapshotId", ""), []).append(row)

    out: dict[str, list[dict]] = {"outside": [], "mismatched": []}
    for group in by_file.values():
        found = quality.id_references(group[0].get("idColumn") or "",
                                      [row.get("data") or {} for row in group])
        if not found:
            continue
        name = group[0].get("fileName", "")
        for kind, half in (("outside", "outside"), ("mismatched", "inside")):
            out[kind] += [{"id": own, "names": named, "text": text, "fileName": name}
                          for own, named, text in found[half]]
    return out


async def _compute(app_id: str, release_id: str) -> dict:
    feature, system = await _load_sides(app_id, release_id)
    saved = await repo.get_trace_config(app_id, release_id)
    config = _config_payload(saved, feature, system)
    layers = config["layers"]

    payload = {
        "configured": config["configured"], "config": config,
        "feature": _layer_info(feature, layers[FEATURE_LAYER]["column"],
                               config["read"][FEATURE_LAYER]["family"]),
        "system": _layer_info(system, layers[SYSTEM_LAYER]["column"],
                              config["read"][SYSTEM_LAYER]["family"]),
    }
    problem = _structural_problem(feature, system)
    if problem is not None:
        payload["errorCode"], payload["error"] = problem
        return payload

    result = cov.build_coverage(feature["rows"], system["rows"], config)
    references = _references(feature["rows"])
    result["outsideReferences"] = references["outside"]
    result["idMismatches"] = references["mismatched"]
    # the per-side read state is an internal of the comparison; the response
    # carries its conclusions, not its working
    empty = _no_ids(result)
    result.pop("sides")
    if empty is not None:
        payload["errorCode"], payload["error"] = empty
    return {**payload, **result}


# --- configuration ------------------------------------------------------


@router.get("/apps/{app_id}/releases/{release_id}/traceability/config",
            response_model=TraceConfig)
async def get_trace_config_route(app_id: Slug, release_id: Slug):
    """The configuration in force, with what detection makes of the data.

    Never 404s on a release that simply has not been configured: it answers
    with the detected defaults and `configured: false`, which is what the
    admin screen opens on.
    """
    await _require_release(app_id, release_id)
    feature, system = await _load_sides(app_id, release_id)
    saved = await repo.get_trace_config(app_id, release_id)
    return _config_payload(saved, feature, system)


@router.put("/apps/{app_id}/releases/{release_id}/traceability/config",
            response_model=TraceConfig, dependencies=[_admin])
async def put_trace_config_route(app_id: Slug, release_id: Slug,
                                 body: TraceConfigUpdate,
                                 user=Depends(get_current_user)):
    await _require_release(app_id, release_id)
    if body.pattern:
        try:
            re.compile(body.pattern)
        except re.error as exc:
            raise HTTPException(status_code=422,
                                detail=f"Not a valid pattern: {exc}") from exc

    doc = body.model_dump(by_alias=True)
    doc["updatedAt"] = datetime.now(timezone.utc)
    doc["updatedBy"] = user["username"]
    await repo.put_trace_config(app_id, release_id, doc)

    feature, system = await _load_sides(app_id, release_id)
    return _config_payload(doc, feature, system)


@router.get("/apps/{app_id}/releases/{release_id}/traceability/preview",
            response_model=TracePreview, dependencies=[_admin])
async def preview_trace_key_route(
    app_id: Slug, release_id: Slug,
    layer: str = Query(..., pattern=r"^[a-z0-9][a-z0-9-]{0,63}$"),
    column: str = Query(default="", max_length=120),
    pattern: str = Query(default="", max_length=200),
):
    """What a proposed override would read out of this layer.

    With no override, this is the rule itself: the first column of each of
    the layer's workbooks. Either way the answer is real extracted ids, not
    a promise about a column.
    """
    await _require_release(app_id, release_id)
    side = await _load_side(app_id, release_id, layer)
    if not side["exists"]:
        raise HTTPException(status_code=404, detail="Layer not found")
    return {
        "layerId": layer, "column": column, "pattern": pattern,
        "read": cov.describe_side(side["rows"], side["columns"], pattern, column),
    }


# --- the comparison -----------------------------------------------------


@router.get("/apps/{app_id}/releases/{release_id}/traceability",
            response_model=CoverageResponse)
async def get_coverage_route(app_id: Slug, release_id: Slug):
    """Feature <-> System coverage for this release, both directions.

    One entry per distinct identifier across both sides. Gaps sort first —
    the report exists to be acted on, and what is already covered is the part
    nobody needs to look at.
    """
    await _require_release(app_id, release_id)
    return await _compute(app_id, release_id)


# --- CSV export — DISABLED ------------------------------------------------
# Kept, not deleted, the way browser upload is: uncomment this block, the
# `csv`/`io`/`StreamingResponse` imports above, `coverageCsvUrl` in the
# frontend's api/client.ts, and the download button in pages/CoveragePage.tsx.
#
# def _details(row: dict | None) -> str:
#     """A row flattened for a spreadsheet cell — every column it filled in."""
#     if not row:
#         return ""
#     return "; ".join(f"{k}={v}" for k, v in (row.get("data") or {}).items()
#                      if v is not None and str(v).strip())
#
#
# @router.get("/apps/{app_id}/releases/{release_id}/traceability.csv")
# async def get_coverage_csv_route(app_id: Slug, release_id: Slug):
#     """The same matrix as a file, for an audit trail or a review meeting."""
#     release = await _require_release(app_id, release_id)
#     result = await _compute(app_id, release_id)
#     if result.get("errorCode"):
#         raise HTTPException(status_code=409, detail=result["error"])
#
#     buf = io.StringIO()
#     writer = csv.writer(buf, lineterminator="\n")
#     related_family = result["summary"].get("relatedFamily") or "Related"
#     writer.writerow([
#         "ID", "Status", "Duplicate", "In feature", "In system",
#         "Feature file", "Feature section", "Feature details",
#         f"{related_family} ids (feature side, supporting only)",
#         "System file", "System section", "System details",
#     ])
#     for entry in result["entries"]:
#         feature, system = entry.get("feature"), entry.get("system")
#         writer.writerow([
#             entry["id"], entry["status"], "yes" if entry["duplicate"] else "",
#             entry["featureCount"], entry["systemCount"],
#             (feature or {}).get("fileName", ""), (feature or {}).get("section", ""),
#             _details(feature),
#             "; ".join(r["id"] for r in entry.get("relatedIds") or []),
#             (system or {}).get("fileName", ""), (system or {}).get("section", ""),
#             _details(system),
#         ])
#
#     name = f"coverage-{app_id}-{release.get('releaseId', release_id)}.csv"
#     return StreamingResponse(
#         iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
#         headers={"Content-Disposition": f'attachment; filename="{name}"'},
#     )
