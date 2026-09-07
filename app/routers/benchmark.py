"""Automation coverage, measured against the published industry reference.

The two sides are assembled here and never blended. Evident's figure is
computed at read time from the current snapshot of every workbook in the
release — like the dashboards, it cannot drift from the rows it describes. The
industry reference is curated in `benchmark_defaults` and changes only when a
person edits it. They meet in one response so a screen can show them side by
side, and the distance between them is a third, derived thing rather than a
property of either.

The reference is shown whether or not Evident can answer it. A workbook with no
automated-test count leaves the measurement unmeasured while the reference
still reads — and "we do not measure this" is a finding worth surfacing rather
than an empty panel worth hiding.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path

from app import repositories as repo
from app.benchmark_defaults import automation_reference
from app.models import AutomationCoverageResponse, AutomationTrendResponse
from app.security import get_current_user
from app.services import benchmark as bm

router = APIRouter(tags=["benchmark"], dependencies=[Depends(get_current_user)])

Slug = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]


async def _measure_release(app_id: str, release_id: str) -> tuple[list[dict], dict | None]:
    """Every testing layer of one release, measured, plus the month it was read
    from. Shared by the single-release panel and the release series so the two
    can never disagree about what a release's coverage is."""
    layers, latest = [], None
    for doc in sorted(await repo.list_layer_docs(app_id, release_id),
                      key=lambda d: d.get("order", 0)):
        layer_id = doc["layerId"]
        side = await repo.coverage_rows(app_id, release_id, layer_id)
        layers.append(bm.measure_layer(
            layer_id, doc.get("name", layer_id), side["rows"], side["columns"]))
        if side["latestPeriod"] and latest is None:
            latest = side["latestPeriod"]
    return layers, latest


@router.get("/apps/{app_id}/releases/{release_id}/benchmark/automation",
            response_model=AutomationCoverageResponse)
async def automation_coverage_route(app_id: Slug, release_id: Slug):
    """Automation coverage for one release, beside the industry reference.

    Every testing layer the release holds is measured, so the release figure is
    count-weighted across all of them rather than an average of percentages.
    """
    if await repo.get_app(app_id) is None:
        raise HTTPException(status_code=404, detail="Application not found")
    release = await repo.get_release(app_id, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")

    layers, latest = await _measure_release(app_id, release_id)
    report = bm.coverage_report(layers, automation_reference())
    return {
        "releaseId": release_id,
        "releaseName": release.get("name", release_id),
        "latestPeriod": latest,
        **report,
    }


@router.get("/apps/{app_id}/benchmark/automation/trend",
            response_model=AutomationTrendResponse)
async def automation_trend_route(app_id: Slug):
    """Automation coverage across every release, oldest first.

    Release against release is the comparison that holds: both sides are
    measured the same way, from the same kind of sheet, by the same rules. The
    industry reference travels along unchanged so a screen can draw it behind
    the series, but it is not what the series is scored against.
    """
    if await repo.get_app(app_id) is None:
        raise HTTPException(status_code=404, detail="Application not found")

    # list_releases returns the wire shape, where the release id is `id`
    releases = sorted(await repo.list_releases(app_id), key=lambda r: r.get("order", 0))
    points = []
    for rel in releases:
        release_id = rel["id"]
        layers, latest = await _measure_release(app_id, release_id)
        evident = bm.automation_coverage(layers)
        points.append({
            "releaseId": release_id,
            "releaseName": rel.get("name", release_id),
            "order": rel.get("order", 0),
            "current": bool(rel.get("current")),
            "measured": evident["measured"],
            "automated": evident["automated"],
            "total": evident["total"],
            "coveragePct": evident["coveragePct"],
            "basis": evident["basis"],
            "unmeasuredLayers": evident["unmeasuredLayers"],
            "latestPeriod": latest,
        })

    series = bm.automation_trend(points)
    return {
        "appId": app_id,
        "points": series,
        "reference": automation_reference(),
        "measuredReleases": sum(1 for p in series if p["measured"]),
    }
