"""The global default testing pyramid.

This list is the one layer definition shared across the whole system: every
release — seeded, created with an application, or added later — starts from
exactly these layers. Anything added to a release afterwards belongs to that
release alone and never appears anywhere else, so an old release keeps the
layers it shipped with even after a newer one drops or adds some.

Bottom-first: `order` 0 is the base of the pyramid (most test cases) and the
last entry is its tip (fewest). The rule the dashboard validates —
Unit > Regression > Feature > System > Acceptance — is defined by this order.
"""
from datetime import datetime

DEFAULT_LAYERS: list[dict] = [
    {"layerId": "unit", "name": "Unit Testing", "short": "Unit",
     "desc": "Isolated checks of individual functions and components"},
    {"layerId": "regression", "name": "Regression Testing", "short": "Regression",
     "desc": "Full re-verification of previously shipped behaviour"},
    {"layerId": "feature", "name": "Feature Testing", "short": "Feature",
     "desc": "Focused verification of new and changed features"},
    {"layerId": "system", "name": "System Testing", "short": "System",
     "desc": "Complete builds validated end to end"},
    {"layerId": "acceptance", "name": "Acceptance Testing", "short": "Acceptance",
     "desc": "Customer-facing acceptance criteria and sign-off"},
]


def layer_id(app_id: str, release_id: str, layer: str) -> str:
    """Layer documents are keyed by the triple they belong to. Slugs cannot
    contain ':', so the parts are unambiguous."""
    return f"{app_id}:{release_id}:{layer}"


def default_layer_docs(app_id: str, release_id: str, now: datetime) -> list[dict]:
    """Layer documents for a brand-new release, ordered bottom-first."""
    return [
        {"_id": layer_id(app_id, release_id, layer["layerId"]),
         "appId": app_id, "releaseId": release_id, **layer, "order": i,
         "createdAt": now, "updatedAt": now}
        for i, layer in enumerate(DEFAULT_LAYERS)
    ]


def copied_layer_docs(app_id: str, release_id: str, source: list[dict],
                      now: datetime) -> list[dict]:
    """Layer *structure* copied from another release — never its data.

    `columns` is deliberately dropped: it describes the Excel schema of the
    source release's uploads, and the new release has not been loaded yet.
    Its workbooks may have a completely different shape.
    """
    return [
        {"_id": layer_id(app_id, release_id, layer["layerId"]),
         "appId": app_id, "releaseId": release_id, "layerId": layer["layerId"],
         "name": layer["name"], "short": layer.get("short", ""),
         "desc": layer.get("desc", ""), "order": i,
         "createdAt": now, "updatedAt": now}
        for i, layer in enumerate(sorted(source, key=lambda l: l.get("order", 0)))
    ]
