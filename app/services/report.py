"""A release's QA report: the facts it is written from, and the checks on it.

The report is prose; the figures are not. Everything a report may say is
gathered here into one small "facts pack" from what the dashboards already
compute — nothing is recalculated — and the model is asked to write only from
that. After it answers, every number it wrote is looked up in the pack, so a
figure the data does not contain is flagged rather than quietly shipped.

Nothing here reads the database or calls OpenAI. The router gathers the
inputs; `report_writer` makes the call.

What goes in is deliberately narrow: counts, rates, verdicts, feature ids and
titles. No raw rows and nothing that names a person — the acceptance sheet's
Tester column never leaves, and neither do warning messages, which can quote a
row's identity, tester included. Warnings travel as their code alone.
"""
import hashlib
import json
import re
from pathlib import PurePosixPath

from app.services import coverage as cov

# The sources an item may cite, beyond `layer:<id>`. Each maps to a page, so
# the screen can link a point back to where it came from.
SOURCES = ("pyramid", "traceability", "automation")
SECTIONS = ("findings", "gaps", "risks", "recommendations")

# Enough of a title to recognise the feature by.
TITLE_LENGTH = 90

# What each workbook warning means, for a reader who sees only its code.
WARNING_MEANINGS = {
    "conflicting_duplicate": "rows agreed on their text but not their numbers; only the first was kept",
    "text_in_measure": "a count column holds some text, so totals taken from it are short",
    "measure_column_not_numeric": "a column named like a count holds text and cannot be totalled",
    "mismatched_id": "a row's text names another row's id; one of the two is stale",
    "identical_workbooks": "two workbooks hold identical rows, so they are counted twice",
    "duplicate_rows": "exact duplicate rows were dropped; no figure changed",
    "separator_rows": "rows with no numbers were read as separators",
    "empty_columns": "some columns are never filled",
    "sparse_columns": "some columns are filled on almost no rows",
    "no_header": "the sheet has no header row, so its columns are only numbered",
}

INSTRUCTIONS = """You are a senior QA lead. Write a concise, professional quality report on one
software release for engineering and QA managers at a life-science microscopy
software company (the product is cellSens).

Write ONLY from the facts in the JSON you are given.
- Use every figure exactly as the facts give it (53.8 stays 53.8, never 54),
  written naturally in the sentence: "53.8% of planned features",
  "1,286 test cases". Never calculate, round, estimate or invent a number,
  and never introduce a date.
- Never mention field names or JSON keys, and never put values in quotes:
  write for a person reading about the release, not about the data.
- Compare nothing with the automation reference range except automation
  coverage itself.
- If something is not in the facts, do not mention it and do not guess at it.
- Name features by id and title where it helps, for example
  "FL-5761 (Support Blackwell Technology for Deep Learning)".
- Plain, specific language. No filler and no marketing tone.

Sections:
- summary: 3 to 4 sentences on the release's overall quality position.
- findings: the 3 to 6 most important observations, most important first.
- gaps: what is missing: untested or unplanned items, figures that are not
  measured, workbooks that are not loaded, data that could not be read.
- risks: what could go wrong for the release because of these facts, most
  severe first. Put the severity only in the severity field, not in the text.
- recommendations: 3 to 6 concrete next actions, most urgent first, each tied
  to a finding, gap or risk.

Always cover, somewhere in findings, gaps or risks:
- every pyramid pair whose verdict is "violated", with both figures and the unit;
- both traceability percentages, featuresWithSystemTestPct and
  systemItemsInFeaturePlanPct;
- every entry of plannedFeaturesWithoutSystemTest and of
  systemItemsNotInFeaturePlan, by id;
- every idMismatch, naming both ids;
- every warning whose severity is "problem", naming its layer.

Sources: every item lists the pages its facts come from.
- "layer:<id>" for one layer's own figures or warnings, using the id from the
  facts, for example "layer:system".
- "pyramid" only for comparing the sizes of two layers.
- "traceability" only for the Feature to System matching: the two
  percentages, the two lists, idMismatches and outsideReferences.
- "automation" only for automation coverage.

How to read the facts:
- A layer with "loaded": false has no workbook loaded into the app yet. That
  says nothing about whether testing happened: say "no data loaded", and
  never say its testing did not happen or needs to start.
- pyramid: each pair compares test cases when both layers declare them, and
  rows otherwise. "violated" means the upper layer is not smaller than the one
  below it; "not checked" means one of the two has no data.
- runResults: executed is passed + failed, and passRatePct is passed out of
  executed. reconciles is false when passed + failed + notRun differs from the
  sheet's own total; unaccounted is that total minus passed, failed and notRun,
  so a negative value means more results were recorded than the total.
- status: passRatePct is passed out of decided.
- inventory: linkedPct is the share of listed items that name the work behind
  them; unlinkedItems name none.
- traceability: plannedFeaturesWithoutSystemTest are features in the plan with
  no system test; systemItemsNotInFeaturePlan are system-tested items the plan
  does not list. idMismatches are rows whose text names another row's id, so
  one of the two is stale. outsideReferences name a feature the feature sheet
  does not list: a scope question, not an error.
- automation: "measured": false means no workbook records an automated count.
  Not measured is not zero: never describe it as zero automated tests. The
  reference range is a cross-industry survey estimate, for orientation only.
- Each file carries its data-quality warnings, by code and severity:
""" + "\n".join(f"  - {code}: {meaning}" for code, meaning in WARNING_MEANINGS.items())

_POINT = {
    "type": "object",
    "properties": {"text": {"type": "string"},
                   "sources": {"type": "array", "items": {"type": "string"}}},
    "required": ["text", "sources"],
    "additionalProperties": False,
}
_RISK = {
    "type": "object",
    "properties": {"text": {"type": "string"},
                   "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                   "sources": {"type": "array", "items": {"type": "string"}}},
    "required": ["text", "severity", "sources"],
    "additionalProperties": False,
}
# the shape the model must answer in; strict, so every field is always there
REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": _POINT},
        "gaps": {"type": "array", "items": _POINT},
        "risks": {"type": "array", "items": _RISK},
        "recommendations": {"type": "array", "items": _POINT},
    },
    "required": ["summary", *SECTIONS],
    "additionalProperties": False,
}


# --- the facts ------------------------------------------------------------


def _clip(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text if len(text) <= TITLE_LENGTH else text[:TITLE_LENGTH - 1].rstrip() + "…"


def _title(data: dict) -> str:
    """A row's own description: the next filled cell after its identifier.

    The identifier is the first column, so the second filled value is the
    one that names what the row is — the same rule the inventory uses.
    """
    values = [v for v in data.values() if v is not None and str(v).strip()]
    return _clip(values[1]) if len(values) > 1 else ""


def _period(period: dict | None) -> str:
    return f"{period['year']}-{period['month']:02d}" if period else ""


def _pick(summary: dict, keys: tuple[str, ...]) -> dict:
    return {k: summary.get(k) for k in keys}


def file_facts(snapshot: dict, stats: dict, profile: dict, warnings: list[dict]) -> dict:
    """What one workbook's dashboard says, as counts and rates only.

    The breakdowns a dashboard draws by dimension, and its failing and open
    rows, are left out: those are where a tester's name turns up.
    """
    kind = profile.get("kind", "volume")
    out: dict = {
        "file": PurePosixPath(snapshot.get("file", "")).name,
        "period": _period(snapshot.get("period")),
        "rows": snapshot.get("rowCount", 0),
        "dashboard": kind,
    }
    total = (stats.get("totals") or {}).get("total_tests")
    if total is not None:
        out["testCases"] = total

    run, status, inventory = (profile.get("runResults"), profile.get("status"),
                              profile.get("inventory"))
    if kind == "run_results" and run:
        out["runResults"] = _pick(run, ("passed", "failed", "notRun", "executed", "total",
                                        "passRatePct", "executedPct", "reconciles",
                                        "unaccounted"))
    elif kind == "status" and status:
        out["status"] = _pick(status, ("statusLabel", "measureLabel", "passed", "failed",
                                       "pending", "unrecognised", "decided", "total",
                                       "passRatePct"))
    elif kind == "inventory" and inventory:
        out["inventory"] = {
            **_pick(inventory, ("items", "linked", "unlinked", "linkedPct", "linkFamily",
                                "duplicates", "unreadable")),
            "unlinkedItems": [{"id": item["id"], "title": _clip(item.get("detail", ""))}
                              for item in inventory.get("unlinkedItems", [])],
        }
    if warnings:
        out["warnings"] = [{"code": w["code"], "severity": w.get("severity", "")}
                           for w in warnings]
    return out


def layer_facts(layer: dict, files: list[dict]) -> dict:
    return {
        "id": layer["id"],
        "name": layer["name"],
        # an empty layer is a workbook nobody loaded, not testing that did not
        # happen — the first real report read 0 rows as "no tests conducted"
        "loaded": bool(layer.get("recordCount")),
        "rows": layer.get("recordCount", 0),
        "testCases": layer.get("testCount"),
        "files": files,
    }


def pyramid(layers: list[dict]) -> list[dict]:
    """Each layer against the one below it, by the rule the Layers page draws.

    Test cases where both layers declare them, rows otherwise — the page's own
    comparison, restated here so the report can quote its verdict rather than
    have the model judge sizes itself.
    """
    ordered = sorted(layers, key=lambda l: l.get("order", 0))
    out = []
    for lower, upper in zip(ordered, ordered[1:]):
        by_tests = lower.get("testCount") is not None and upper.get("testCount") is not None
        key = "testCount" if by_tests else "recordCount"
        low, high = lower.get(key) or 0, upper.get(key) or 0
        empty = not lower.get("recordCount") or not upper.get("recordCount")
        out.append({
            "lower": lower["name"], "upper": upper["name"],
            "unit": "test cases" if by_tests else "rows",
            "lowerValue": low, "upperValue": high,
            "verdict": "not checked" if empty else ("violated" if high >= low else "holds"),
        })
    return out


def traceability_facts(trace: dict) -> dict:
    """Feature ↔ System, as the Traceability page reads it."""
    if trace.get("errorCode"):
        return {"available": False, "reason": trace.get("error") or ""}
    summary = trace.get("summary") or {}
    entries = trace.get("entries") or []

    def listed(status: str) -> list[dict]:
        return [{"id": e["id"],
                 "title": _title(((e.get("feature") or e.get("system")) or {}).get("data") or {})}
                for e in entries if e.get("status") == status]

    return {
        "available": True,
        "featuresPlanned": summary.get("featureTotal", 0),
        "systemItems": summary.get("systemTotal", 0),
        "covered": summary.get("covered", 0),
        "featuresWithSystemTestPct": summary.get("forwardCoveragePct"),
        "systemItemsInFeaturePlanPct": summary.get("backwardCoveragePct"),
        # named for what they hold: the first real report called planned
        # features "system items" when these were missingIn… lists
        "plannedFeaturesWithoutSystemTestCount": summary.get("missingInSystem", 0),
        "plannedFeaturesWithoutSystemTest": listed("missing_in_system"),
        "systemItemsNotInFeaturePlanCount": summary.get("missingInFeature", 0),
        "systemItemsNotInFeaturePlan": listed("missing_in_feature"),
        "duplicateIds": summary.get("duplicates", 0),
        "unreadableIds": summary.get("unresolved", 0),
        "idMismatches": [{"id": m["id"], "names": m["names"], "text": _clip(m.get("text", ""))}
                         for m in trace.get("idMismatches") or []],
        "outsideReferences": [{"id": o["id"], "names": o["names"]}
                              for o in trace.get("outsideReferences") or []],
    }


def automation_facts(report: dict, layer_names: dict[str, str]) -> dict:
    """Automation coverage beside its reference, as the Benchmark page reads it."""
    evident, reference = report["evident"], report["reference"]
    distance = report.get("distance") or {}
    out = {
        "measured": evident.get("measured", False),
        "notMeasuredLayers": [layer_names.get(i, i) for i in evident.get("unmeasuredLayers", [])],
        "referenceRangePct": [reference.get("low"), reference.get("high")],
        "referenceScope": reference.get("scope", ""),
        "position": distance.get("position"),
    }
    # Unmeasured has no counts to give. The measurement reports 0 of 0 there,
    # and a model handed that zero wrote "0 automated tests recorded" — the
    # opposite of "not measured". Without the counts there is nothing to misquote.
    if out["measured"]:
        out.update({
            "coveragePct": evident.get("coveragePct"),
            "automated": evident.get("automated"),
            "total": evident.get("total"),
            "pointsFromRange": distance.get("points"),
        })
    return out

def assemble(app_name: str, release_name: str, layers: list[dict], pyramid_pairs: list[dict],
             traceability: dict, automation: dict) -> dict:
    periods = sorted({f["period"] for layer in layers for f in layer["files"] if f["period"]})
    return {
        "application": app_name,
        "release": release_name,
        "period": periods[-1] if periods else "",
        "layers": layers,
        "pyramid": pyramid_pairs,
        "traceability": traceability,
        "automation": automation,
    }


def facts_hash(facts: dict) -> str:
    """Whether the data under a saved report has changed since it was written."""
    blob = json.dumps(facts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# --- the checks on what came back ------------------------------------------

_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def figures(text: str) -> set[str]:
    """Every number in a text, written one way: `140,789` and `140789.0` agree.

    Read the same way from the report and from the facts, so an id's digits
    (`FL-5777`), a release name (`4.5.1`) and a percentage all line up.
    """
    out = set()
    for token in _NUMBER.findall(_THOUSANDS.sub("", text)):
        value = float(token)
        out.add(str(int(value)) if value.is_integer() else repr(value))
    return out


def unverified(body: dict, facts: dict) -> list[str]:
    """Numbers the report uses that the facts it was written from do not hold."""
    texts = [body.get("summary", "")] + [point.get("text", "")
                                         for section in SECTIONS
                                         for point in body.get(section, [])]
    written = set().union(*(figures(t) for t in texts))
    known = figures(json.dumps(facts, ensure_ascii=False, default=str))
    return sorted(written - known, key=float)


def clean_sources(body: dict, layer_ids: list[str]) -> dict:
    """Keep only sources the screen can link to, each once."""
    allowed = set(SOURCES) | {f"layer:{i}" for i in layer_ids}
    for section in SECTIONS:
        for point in body.get(section, []):
            point["sources"] = [s for s in dict.fromkeys(point.get("sources", [])) if s in allowed]
    return body


# --- what the report was told to cover, and did not ---------------------------

# The problem-level warnings a report must name, the words it would name them
# with, and how the screen lists one that is missing. Matched within a single
# point that also names the layer — a heuristic, so when unsure it lists the
# warning as left out rather than claiming the report covered it.
PROBLEM_WARNINGS = {
    "conflicting_duplicate": ("conflicting rows", ("conflict", "disagree")),
    "text_in_measure": ("text in a count column", ("text",)),
    "measure_column_not_numeric": ("a count column that holds text",
                                   ("text", "numeric", "totalled")),
    "identical_workbooks": ("identical workbooks", ("identical", "twice")),
    "mismatched_id": ("ID mismatch", ("mismatch", "stale")),
}


def _num(value) -> str:
    value = float(value)
    return f"{int(value):,}" if value.is_integer() else f"{value:,}"


def missing_items(body: dict, facts: dict) -> list[dict]:
    """What the instructions say every report must cover, and this one left out.

    Checked like `unverified`: against the words on the page, not the model's
    word for it. Figures and ids must appear; ids are compared whole, so
    FL-576 does not count as FL-5761. A problem warning must be named in a
    point that also names its layer.

    Each item carries a plain sentence built from the facts, so a gap can be
    filled without asking the model again — see `fill_gaps`.
    """
    points = [body.get("summary", "")] + [p.get("text", "") for section in SECTIONS
                                          for p in body.get(section, [])]
    text = " ".join(points)
    numbers = figures(text)
    ids = {cov.canonical(token) for token in cov.tokens(text)}
    out: list[dict] = []

    def add(label: str, section: str, sentence: str, sources: list[str]) -> None:
        out.append({"label": label, "section": section, "text": sentence, "sources": sources})

    for pair in facts.get("pyramid", []):
        needed = figures(str(pair["upperValue"])) | figures(str(pair["lowerValue"]))
        if pair.get("verdict") == "violated" and not needed <= numbers:
            upper, lower = pair["upper"], pair["lower"]
            high, low = _num(pair["upperValue"]), _num(pair["lowerValue"])
            add(f"Pyramid violation: {upper} ({high} {pair['unit']}) is not smaller than "
                f"{lower} ({low})", "findings",
                f"{upper} has {high} {pair['unit']}, not fewer than the {low} in {lower}, "
                f"so the testing pyramid is inverted between these two layers.", ["pyramid"])

    trace = facts.get("traceability") or {}
    mismatches = trace.get("idMismatches", []) if trace.get("available") else []
    if trace.get("available"):
        for key, label in (("featuresWithSystemTestPct", "of planned features have a system test"),
                           ("systemItemsInFeaturePlanPct", "of system items are in the feature plan")):
            value = trace.get(key)
            if value is not None and not figures(str(value)) <= numbers:
                add(f"Traceability: {_num(value)}% {label}", "findings",
                    f"{_num(value)}% {label}.", ["traceability"])
        for key, label in (("plannedFeaturesWithoutSystemTest",
                            "Planned features without a system test"),
                           ("systemItemsNotInFeaturePlan",
                            "System items not in the feature plan")):
            missing = [entry for entry in trace.get(key, [])
                       if cov.canonical(entry["id"]) not in ids]
            if missing:
                named = "; ".join(f"{e['id']} ({e['title']})" if e.get("title") else e["id"]
                                  for e in missing)
                add(f"{label}: {', '.join(e['id'] for e in missing)}", "gaps",
                    f"{label}: {named}.", ["traceability"])
        for m in mismatches:
            if not {cov.canonical(m["id"]), cov.canonical(m["names"])} <= ids:
                add(f"ID mismatch: {m['id']}’s text names {m['names']}", "gaps",
                    f"{m['id']}’s description names {m['names']}, a separate feature in "
                    f"the same sheet, so one of the two ids is stale.", ["traceability"])

    lowered = [p.lower() for p in points]
    for layer in facts.get("layers", []):
        word = layer["name"].split()[0].lower()
        for file in layer.get("files", []):
            for warning in file.get("warnings", []):
                spec = PROBLEM_WARNINGS.get(warning.get("code", ""))
                if warning.get("severity") != "problem" or spec is None:
                    continue
                # the traceability item above already asks for the ids
                if warning["code"] == "mismatched_id" and mismatches:
                    continue
                label, terms = spec
                if not any(word in p and any(term in p for term in terms) for p in lowered):
                    meaning = WARNING_MEANINGS.get(warning["code"], label)
                    add(f"{layer['name']}: {label}", "gaps",
                        f"{layer['name']}: {meaning}.", [f"layer:{layer['id']}"])

    seen: set[str] = set()
    return [item for item in out if not (item["label"] in seen or seen.add(item["label"]))]


def not_covered(body: dict, facts: dict) -> list[str]:
    """The left-out items, as the sentences the screen lists."""
    return [item["label"] for item in missing_items(body, facts)]


def fill_gaps(body: dict, facts: dict) -> tuple[dict, list[str]]:
    """Add everything the draft left out, as plain lines built from the facts.

    The model is not asked again. Each line is assembled from the same figures
    the report was written from, so it can be neither wrong nor invented, and
    it is marked `added` so a reader can tell it from the model's own words.
    Returns the report and what was added.
    """
    items = missing_items(body, facts)
    for item in items:
        body.setdefault(item["section"], []).append(
            {"text": item["text"], "sources": item["sources"], "added": True})
    return body, [item["label"] for item in items]
