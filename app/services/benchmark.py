"""Evident's own automation coverage, measured from the ingested rows.

    coverage = automated test cases / total test cases

**Count-weighted, not an average of percentages.** A layer holding 140,000
tests and one holding 1,400 do not carry equal weight in a figure that claims
to describe the release, and averaging their percentages would let the small
one swing the answer. The surveys this is read against count test cases too,
so weighting by count is also the only way the two are talking about the same
quantity.

**The measure is `test_count` where a workbook has it, row count where it does
not** — and which one was used is reported rather than assumed, because the two
disagree: one real sheet holds 206 rows summing to 140,736 tests. A figure that
silently switched basis between releases would be worse than no figure.

Nothing here reads the database or knows what an industry reference is. It
takes rows as they were ingested and returns what they say.
"""
import re
from typing import Any

# A column of automated counts announces itself: "Automated", "Automation
# count", "Auto Test Count". Matched on the human label, not the sanitised key,
# because the label is what the test team actually typed — and abbreviated to
# "Auto" often enough that requiring "autom" would miss real sheets. Only
# numeric columns are ever scanned, so an "Author" column cannot be caught.
AUTOMATED_LABEL = re.compile(r"\bauto", re.IGNORECASE)
# The total it is a share of. Checked only after the automated column is taken
# out, so "Automated Test Count" cannot also be read as the total.
TOTAL_LABEL = re.compile(r"test\s*count|test\s*cases|\btotal\b|\bcount\b", re.IGNORECASE)


def _numeric(columns: list[dict]) -> list[dict]:
    return [c for c in columns if c.get("type") == "number"]


def detect_columns(columns: list[dict]) -> dict:
    """Which column holds the automated count, and which the total.

    Discovered rather than configured, so a workbook that gains an automated
    column starts being measured the moment it is loaded — no settings screen
    stands between the test team adding the column and the number appearing.
    """
    numeric = _numeric(columns)
    automated = next((c for c in numeric if AUTOMATED_LABEL.search(c.get("label", ""))), None)

    rest = [c for c in numeric if automated is None or c["key"] != automated["key"]]
    total = next((c for c in rest if TOTAL_LABEL.search(c.get("label", ""))), None)
    # a sheet with exactly one other numeric column has said what its measure is
    if total is None and len(rest) == 1:
        total = rest[0]

    # deliberately not called "automated"/"total": those names carry the summed
    # counts further down, and a dict merge would silently replace a number
    # with a column key
    return {
        "automatedColumn": automated["key"] if automated else "",
        "automatedLabel": automated["label"] if automated else "",
        "totalColumn": total["key"] if total else "",
        "totalLabel": total["label"] if total else "",
    }


def _sum(rows: list[dict], key: str) -> float:
    if not key:
        return 0.0
    out = 0.0
    for row in rows:
        value = (row.get("data") or {}).get(key)
        if isinstance(value, (int, float)):
            out += value
    return out


def measure_layer(layer_id: str, name: str, rows: list[dict],
                  columns: list[dict]) -> dict:
    """One testing layer's automation coverage, and how it was arrived at.

    A layer with no automated column is *unmeasured*, which is not the same as
    zero: reporting 0% for a sheet that simply never recorded the figure would
    invent a finding. It is carried through as `measured: false` and left out
    of the release total's denominator.
    """
    found = detect_columns(columns)
    total_rows = len(rows)

    if not found["automatedColumn"]:
        return {
            "layerId": layer_id, "name": name, "measured": False,
            "reason": "no automated-count column in this layer's workbooks",
            "automated": 0.0, "total": 0.0, "coveragePct": None,
            "basis": "", "basisLabel": "", "rowCount": total_rows, **found,
        }

    automated = _sum(rows, found["automatedColumn"])
    if found["totalColumn"]:
        total = _sum(rows, found["totalColumn"])
        basis, basis_label = "test_count", found["totalLabel"]
    else:
        # no measure column: every row counts as one test, and the screen says so
        total = float(total_rows)
        basis, basis_label = "row_count", "rows"

    return {
        "layerId": layer_id, "name": name, "measured": total > 0,
        "reason": "" if total > 0 else "the total is zero, so a share cannot be taken",
        "automated": automated, "total": total,
        "coveragePct": round(automated / total * 100, 1) if total > 0 else None,
        "basis": basis, "basisLabel": basis_label,
        "rowCount": total_rows, **found,
    }


def automation_coverage(layers: list[dict]) -> dict:
    """The release figure: every measured layer's counts added, then divided.

    Layers that carry no automated count are listed but excluded from both
    sides of the division — a release is not less automated because one of its
    sheets does not record the figure.
    """
    measured = [l for l in layers if l["measured"]]
    automated = sum(l["automated"] for l in measured)
    total = sum(l["total"] for l in measured)

    bases = {l["basis"] for l in measured}
    if not bases:
        basis = ""
    elif len(bases) == 1:
        basis = bases.pop()
    else:
        basis = "mixed"

    return {
        "measured": bool(measured) and total > 0,
        "automated": automated,
        "total": total,
        "coveragePct": round(automated / total * 100, 1) if total > 0 else None,
        "basis": basis,
        "unmeasuredLayers": [l["layerId"] for l in layers if not l["measured"]],
        "layers": layers,
    }


def distance_to_range(value: float | None, low: float, high: float) -> dict:
    """Where the measurement sits relative to the reference range.

    Rounded to whole points on purpose. The reference is a spread of
    self-reported survey estimates; a distance carried to a decimal would claim
    a precision neither side has.
    """
    if value is None:
        return {"position": "unmeasured", "points": None}
    if value < low:
        return {"position": "below", "points": round(low - value)}
    if value > high:
        return {"position": "above", "points": round(value - high)}
    return {"position": "within", "points": 0}


def coverage_report(layers: list[dict], reference: dict) -> dict:
    """Both sides, side by side, and the distance between them.

    The two are assembled here but never merged: the measurement keeps its own
    numbers, the reference keeps its own provenance, and the distance is a
    third thing derived from them rather than a property of either.
    """
    evident = automation_coverage(layers)
    return {
        "evident": evident,
        "reference": reference,
        "distance": distance_to_range(
            evident["coveragePct"] if evident["measured"] else None,
            reference["low"], reference["high"],
        ),
    }


# --- across releases -----------------------------------------------------


def automation_trend(points: list[dict]) -> list[dict]:
    """A release series with each point's change from the one before it.

    This is the comparison worth having. Release against release is measured
    against measured — the same definition, the same counting rules, the same
    sheets — where the industry reference is a spread of self-reported
    estimates of a unit nobody defines the same way. A number that moved 4
    points since the last release says something exact; a number 12 points
    below a survey band does not.

    `points` arrive oldest first. The comparison is against the previous
    *measured* release rather than the previous one, so a release that never
    recorded an automated count breaks the chain of evidence without also
    breaking the trend.
    """
    out: list[dict] = []
    previous: dict | None = None
    for point in points:
        entry = {**point, "deltaPct": None, "comparedTo": ""}
        if point["measured"] and previous is not None:
            entry["deltaPct"] = round(point["coveragePct"] - previous["coveragePct"], 1)
            entry["comparedTo"] = previous["releaseName"]
        out.append(entry)
        if point["measured"]:
            previous = point
    return out
