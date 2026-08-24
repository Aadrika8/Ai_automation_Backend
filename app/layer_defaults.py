"""The global default testing pyramid.

This list is the one layer definition shared across the whole system: every
application — seeded or created through the API — starts with exactly these
layers. Anything added to an application afterwards belongs to that
application alone and never appears anywhere else.

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


def default_layer_docs(app_id: str, now: datetime) -> list[dict]:
    """Layer documents for a newly created application, ordered bottom-first."""
    return [
        {"_id": f'{app_id}:{layer["layerId"]}', "appId": app_id, **layer, "order": i,
         "createdAt": now, "updatedAt": now}
        for i, layer in enumerate(DEFAULT_LAYERS)
    ]
