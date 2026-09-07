"""Published industry reference figures — curated, never computed.

Kept in its own module for the same reason it is kept in its own panel on the
screen: an industry reference and Evident's own measurement answer different
questions and must not be blended. Nothing here is derived from ingested data,
and nothing here changes when a workbook is loaded.

Every entry carries its provenance, because the honest reading of these numbers
depends on knowing three things about them:

* **They are estimates, not measurements.** Each is a survey respondent's own
  guess at their automation level. Evident's figure is an exact count of rows.
  Comparing them is orientation, never a precise gap — which is why no code
  here computes a distance to a decimal place.
* **They are vendor- or consultancy-published.** Every publisher sells QA
  services or automation tooling, so the commercial interest runs one way.
* **They are cross-sector.** No life-science-specific automation-coverage
  percentage is publicly published. The World Quality Report segments by
  industry but its life-sciences cut sits behind the report's gate.

The range is a *synthesis*: no source states an industry range. It is the
spread of the three studies below, which have different methods, populations
and years. Their convergence is what makes it worth showing at all.
"""

# What the reference describes, so the screen can say it without hardcoding it.
AUTOMATION_METRIC = "share of test cases automated"
AUTOMATION_SCOPE = "all-industry, cross-sector"

AUTOMATION_SOURCES: list[dict] = [
    {
        "value": 33.0,
        "label": "World Quality Report 2025-26",
        "publisher": "Capgemini / Sogeti",
        "publisherKind": "consultancy",
        "sample": "",
        "year": 2025,
    },
    {
        "value": 40.0,
        "label": "TestRail Software Testing & Quality Report, 4th ed.",
        "publisher": "Idera",
        "publisherKind": "test management vendor",
        "sample": "n = 2,751",
        "year": 2025,
    },
    {
        "value": 44.0,
        "label": "Katalon State of Software Quality 2025",
        "publisher": "Katalon",
        "publisherKind": "automation tooling vendor",
        "sample": "n = 1,500",
        "year": 2025,
    },
]

# Caveats the screen is required to show. They are data rather than UI copy so
# that a reference can never be rendered without them.
AUTOMATION_CAVEATS: list[str] = [
    "The range is a synthesis of three separate studies, not a published figure.",
    "Every figure is a self-reported survey estimate; Evident's is an exact count.",
    "All three publishers sell QA services or automation tooling.",
    "None is life-science-specific — no such figure is publicly published.",
    "All reached us through secondary reporting; the primary reports are gated.",
]


def automation_reference() -> dict:
    """The reference in force, with the range derived from its own sources.

    The low and high are read off the studies rather than written down twice,
    so the band on screen can never drift from the pills beneath it.
    """
    values = [s["value"] for s in AUTOMATION_SOURCES]
    return {
        "metric": AUTOMATION_METRIC,
        "scope": AUTOMATION_SCOPE,
        "low": min(values),
        "high": max(values),
        "synthesised": True,
        "selfReported": True,
        "lifeScienceSpecific": False,
        "sources": AUTOMATION_SOURCES,
        "caveats": AUTOMATION_CAVEATS,
    }
