"""Which dashboard a workbook has earned, decided from what it actually holds.

The testing types do not carry the same kind of data. One sheet counts test
cases, another records what happened when they ran, a third is a list of
features with no measure at all. Reading them all the same way — sum the
rightmost numeric column, group by section — describes exactly one of them and
misreports the rest: a column of percentages summed to 3,691, a single-slice
donut, and "run results not connected yet" printed under 1,349 passes.

So the shape of the data picks the dashboard. Nothing here reads the database
or renders anything; it takes columns and their totals and says what they are.

Detection is by *label and type*, like `benchmark.detect_columns` — the label is
what the test team typed, and the type is what the parser inferred from every
value in the column. Type is what keeps this honest: `System_Test.xlsx` has
columns called Pass and Fail, but somebody typed "make only excel" into one of
them, so they are string columns and that sheet is correctly not read as a run.
"""
import re
from collections import Counter
from typing import Any

from app.services import coverage as cov
from app.services.benchmark import TOTAL_LABEL

# A results sheet names its outcomes. These are matched on the human label
# only, and only against numeric columns, so a "Passed by" reviewer column or a
# free-text "Failure notes" cannot be mistaken for a count.
PASS_LABEL = re.compile(r"\bpass(ed)?\b", re.IGNORECASE)
FAIL_LABEL = re.compile(r"\bfail(ed|ure|ures)?\b", re.IGNORECASE)
NOT_RUN_LABEL = re.compile(
    r"\bn\.?\s*/?\s*a\b|not\s*applicable|\bskip(ped)?\b|\bblocked\b|\bnot\s*run\b",
    re.IGNORECASE)

# A breakdown wants a column with a handful of repeated values — an OS, a
# tester, an install type. One value per row is an identifier, and twenty-five
# values over thirty-nine rows is prose; neither groups anything.
MIN_DIMENSION_VALUES = 2
MAX_DIMENSION_VALUES = 12

# Some sheets record the outcome as a word per row rather than as counts.
# The words themselves are what identify such a column — a label reading
# "Status" is a hint, never the proof, because `System_Test.xlsx` has a column
# called Pass holding the single value "make only excel".
PASSING_WORDS = frozenset((
    "pass", "passed", "passing", "ok", "success", "successful", "complete",
    "completed", "done", "closed", "fixed", "verified", "approved", "green"))
FAILING_WORDS = frozenset((
    "fail", "failed", "failing", "failure", "error", "blocked", "rejected",
    "broken", "ng", "red", "not ok"))
PENDING_WORDS = frozenset((
    "not started", "yet to start", "in progress", "in-progress", "wip",
    "pending", "open", "todo", "to do", "deferred", "on hold", "not run",
    "skipped", "skip", "new", "n/a", "na", "not applicable", "in review"))

STATUS_LABEL = re.compile(r"\bstatus\b|\bresult\b|\bstate\b|\boutcome\b|\bverdict\b",
                          re.IGNORECASE)
# Most of a column's values have to read as outcomes before it is one.
MIN_OUTCOME_SHARE = 0.5
# …and it has to be filled in. A results column nobody completed is a
# completeness problem, not a status dashboard.
MIN_STATUS_FILLED = 0.5

# Numbers that are not quantities. A percentage cannot be summed and a row
# number is not a measure, so neither may stand in as what a sheet counts.
PERCENT_LABEL = re.compile(r"%|\bpercent|\brate\b", re.IGNORECASE)
SERIAL_LABEL = re.compile(
    r"\b(no|nos|num|number|s\.?\s*no|sr|serial)\.?\s*$|^#", re.IGNORECASE)


def _numeric(columns: list[dict]) -> list[dict]:
    return [c for c in columns if c.get("type") == "number"]


def detect_run_columns(columns: list[dict]) -> dict:
    """Which columns hold passes, failures, tests not run, and the total.

    Claimed in that order out of a shrinking pool, so one column can never fill
    two roles: on the real acceptance sheet `Pass` is taken before the total is
    looked for, which stops `TOTAL_LABEL` matching it later.
    """
    pool = _numeric(columns)
    found: dict[str, str] = {}

    for role, pattern in (("passed", PASS_LABEL), ("failed", FAIL_LABEL),
                          ("notRun", NOT_RUN_LABEL), ("total", TOTAL_LABEL)):
        hit = next((c for c in pool if pattern.search(c.get("label", ""))), None)
        found[f"{role}Column"] = hit["key"] if hit else ""
        found[f"{role}Label"] = hit["label"] if hit else ""
        if hit is not None:
            pool = [c for c in pool if c["key"] != hit["key"]]

    return found


def is_run_results(found: dict) -> bool:
    """A run needs its passes, and something to read them against.

    Pass alone is a number without a denominator. Pass with either the failures
    beside it or the total it came out of is a result.
    """
    return bool(found.get("passedColumn")) and bool(
        found.get("failedColumn") or found.get("totalColumn"))


def summarise_run(totals: dict[str, float], found: dict) -> dict:
    """What the run said, and which columns said it.

    The pass rate is taken over what was *executed* — passes plus failures —
    rather than over everything planned. Those are different questions and the
    sheets answer both: 1,349 of 1,386 executed is 97.3%, while 1,349 of 1,412
    planned is 95.5%. The executed rate is the one "pass rate" normally means,
    so it is the headline, and the tests never run are carried beside it rather
    than folded in.

    When a total column disagrees with passed + failed + not-run, that is
    reported instead of being smoothed over. A results sheet whose own columns
    do not add up is a finding.
    """
    def total_of(role: str) -> float:
        key = found.get(f"{role}Column") or ""
        value = totals.get(key, 0.0) if key else 0.0
        return float(value or 0.0)

    passed, failed, not_run = total_of("passed"), total_of("failed"), total_of("notRun")
    executed = passed + failed
    accounted = executed + not_run
    # a sheet with no total column says what it is by adding up
    total = total_of("total") if found.get("totalColumn") else accounted
    unaccounted = round(total - accounted, 2)

    return {
        "passed": passed,
        "failed": failed,
        "notRun": not_run,
        "executed": executed,
        "total": total,
        "passRatePct": round(passed / executed * 100, 1) if executed else None,
        "executedPct": round(executed / total * 100, 1) if total else None,
        "basisLabel": found.get("passedLabel", ""),
        # only meaningful when the sheet carries its own total to disagree with
        "reconciles": (not found.get("totalColumn")) or abs(unaccounted) < 0.5,
        "unaccounted": unaccounted if found.get("totalColumn") else 0.0,
        **found,
    }


# --- outcomes recorded as a word per row ---------------------------------


def outcome_of(value: Any) -> str:
    """Which way a status value points: passing, failing, pending, or unknown.

    Unknown is a real answer. A sheet using words nobody else uses still gets
    its distribution drawn; it just cannot be given a pass rate, and saying so
    beats inventing one.
    """
    text = re.sub(r"\s+", " ", str(value or "")).strip().strip(".!:").casefold()
    if not text:
        return ""
    if text in PASSING_WORDS:
        return "passing"
    if text in FAILING_WORDS:
        return "failing"
    if text in PENDING_WORDS:
        return "pending"
    return ""


def outcome_share(values: list) -> float:
    """How much of a column reads as an outcome, over its distinct values."""
    filled = [v for v in values if v is not None and str(v).strip()]
    if not filled:
        return 0.0
    return sum(1 for v in filled if outcome_of(v)) / len(filled)


def detect_status_column(columns: list[dict], stats: dict[str, dict],
                         row_count: int) -> dict:
    """The column recording what happened, when it is words rather than counts.

    Decided on the values, not the label. `System_Test.xlsx` carries a column
    called `Pass` holding one cell reading "make only excel" — labelled like an
    outcome, filled like an afterthought, and meaning neither. It fails both
    tests here: nothing in it reads as an outcome, and one row in nine is not a
    filled-in column.
    """
    best: tuple | None = None
    for col in columns:
        if col.get("type") != "string":
            continue
        info = stats.get(col["key"]) or {}
        values = info.get("values") or []
        if row_count and info.get("filled", 0) < MIN_STATUS_FILLED * row_count:
            continue
        share = outcome_share(values)
        if share < MIN_OUTCOME_SHARE:
            continue
        # a column that says it is the status wins over one that merely looks
        # like it; then the purest; then the simplest
        rank = (bool(STATUS_LABEL.search(col.get("label", ""))), share, -len(values))
        if best is None or rank > best[0]:
            best = (rank, col)

    if best is None:
        return {"statusColumn": "", "statusLabel": ""}
    return {"statusColumn": best[1]["key"], "statusLabel": best[1].get("label", "")}


def is_status(found: dict) -> bool:
    return bool(found.get("statusColumn"))


def primary_measure(columns: list[dict]) -> dict | None:
    """What a sheet counts, ignoring the numbers that are not quantities.

    The rightmost numeric column is the sheets' own convention for the measure,
    but a percentage column sits to the right of everything on the acceptance
    sheet and a row number sits to the left of everything — neither is a count
    of anything, and summing either produces a figure with no meaning.
    """
    usable = [c for c in columns
              if c.get("type") == "number"
              and not PERCENT_LABEL.search(c.get("label", ""))
              and not SERIAL_LABEL.search(c.get("label", ""))]
    return usable[-1] if usable else None


def summarise_status(buckets: list[dict], status: dict,
                     measure: dict | None) -> dict:
    """The outcome split, weighted by what each row is worth.

    A row is not a test. This sheet's four rows carry 12, 18, 9 and 14 test
    cases, so three passing rows out of four is 75% while the cases they stand
    for are 83%. Weighting by the measure is the same choice the benchmark
    makes for the same reason, and the basis is reported either way — a figure
    that silently switched between them would be worse than no figure.

    The rate is taken over what was *decided*: pending rows are carried beside
    it rather than counted as failures, exactly as tests never run are on a
    results sheet.
    """
    measure_key = measure["key"] if measure else ""
    weighted = bool(measure_key)

    def weight(bucket: dict) -> float:
        return float(bucket.get("measure") or 0) if weighted else float(
            bucket.get("rowCount") or 0)

    statuses = [{
        "value": str(b.get("value", "")),
        "kind": outcome_of(b.get("value")),
        "rowCount": int(b.get("rowCount") or 0),
        "measure": weight(b),
    } for b in buckets]
    statuses.sort(key=lambda s: (-s["measure"], -s["rowCount"], s["value"]))

    def total_of(kind: str) -> float:
        return sum(s["measure"] for s in statuses if s["kind"] == kind)

    passed, failed = total_of("passing"), total_of("failing")
    pending = total_of("pending")
    unknown = sum(s["measure"] for s in statuses if not s["kind"])
    decided = passed + failed
    total = sum(s["measure"] for s in statuses)

    return {
        "statusColumn": status.get("statusColumn", ""),
        "statusLabel": status.get("statusLabel", ""),
        "measureColumn": measure_key,
        "measureLabel": measure["label"] if measure else "rows",
        "basis": "measure" if weighted else "row_count",
        "passed": passed, "failed": failed, "pending": pending,
        "unrecognised": unknown,
        "decided": decided, "total": total,
        "passRatePct": round(passed / decided * 100, 1) if decided else None,
        "rowCount": sum(s["rowCount"] for s in statuses),
        "statuses": statuses,
    }


# --- a list of things, with no measure and no outcome ---------------------
# Some sheets are inventories: the Feature workbook is thirteen features, each
# naming the work items behind it. There is nothing to sum and nothing to score,
# so the count-and-group view has three tiles and a one-slice donut to show for
# it. What such a sheet can be asked is what it lists and how completely it is
# filled in — and the identifier rules for that already exist, in the coverage
# service, where they are the same rules the traceability screen uses.

MIN_IDENTIFIER_SHARE = 0.5
# Enough to characterise a sheet without reading a huge one into memory. An
# inventory has no measure, so these are small sheets in practice.
MAX_INVENTORY_ROWS = 2000
MAX_LISTED_ITEMS = 20
# A linkage convention is something a sheet does throughout, not once. Below
# these it is a stray token in a description, and reporting the sheet as barely
# linked would be worse than saying it carries no links.
MIN_LINK_ENTRIES = 2
MIN_LINK_SHARE = 0.25


def detect_inventory(columns: list[dict], stats: dict[str, dict]) -> dict:
    """Whether the first column is a column of identifiers.

    The first column of a file is where the identifier lives — the same rule
    the traceability comparison runs on, rather than a second opinion about the
    same sheets.
    """
    if not columns:
        return {"idColumn": "", "idLabel": ""}
    first = columns[0]
    values = (stats.get(first["key"]) or {}).get("values") or []
    if not values:
        return {"idColumn": "", "idLabel": ""}
    read = sum(1 for v in values if cov.read_identifier(v) is not None)
    if read / len(values) < MIN_IDENTIFIER_SHARE:
        return {"idColumn": "", "idLabel": ""}
    return {"idColumn": first["key"], "idLabel": first.get("label", first["key"])}


def is_inventory(found: dict) -> bool:
    return bool(found.get("idColumn"))


def summarise_inventory(rows: list[dict], found: dict) -> dict:
    """What the sheet lists, and how completely each entry is filled in.

    Two passes, because the family has to be known before the supporting
    identifiers can be told apart from it: the first reads the identifier out of
    every row, the second collects the ids of *other* families each row
    mentions. That is the same two-step the coverage service does, and it is
    reused rather than restated.

    An entry naming nothing is the finding here. On the real Feature sheet
    twelve of thirteen features name the work items behind them and one names
    none, which is the single thing that dashboard should have been saying.
    """
    id_column = found.get("idColumn", "")
    seen: dict[str, dict] = {}
    unreadable = 0
    duplicates = 0

    for row in rows:
        data = row.get("data") or {}
        read = cov.read_identifier(data.get(id_column))
        if read is None:
            unreadable += 1
            continue
        key, display, _family = read
        if key in seen:
            duplicates += 1
            continue
        seen[key] = {"key": key, "id": display, "row": row}

    families: Counter = Counter()
    for entry in seen.values():
        fam = cov.family_of(entry["id"])
        if fam:
            families[fam] += 1
    family = families.most_common(1)[0][0] if families else ""

    # supporting identifiers, gathered the way a coverage gap gathers them
    #
    # Counted by how many *entries* mention each family, not by how many tokens
    # turn up: a linkage convention is something the sheet does throughout. The
    # System workbook has one row reading "Helix: Support of new BX57/47
    # microscopes", and BX57 is a microscope rather than a work item. Taken as
    # the family it would report that sheet as 11% linked, when the truth is
    # that it carries no links at all.
    family_items: Counter = Counter()
    for entry in seen.values():
        entry["links"] = cov.related_ids(entry["row"], id_column, family)
        for fam in {cov.family_of(t) for t, _ in entry["links"] if cov.family_of(t)}:
            family_items[fam] += 1

    link_family = ""
    if family_items:
        candidate, entries = family_items.most_common(1)[0]
        if entries >= MIN_LINK_ENTRIES and entries >= MIN_LINK_SHARE * max(1, len(seen)):
            link_family = candidate

    linked, unlinked_items, links = 0, [], 0
    ranked = []
    for entry in seen.values():
        kept = [t for t, _ in entry["links"] if cov.family_of(t) == link_family]
        links += len(kept)
        data = entry["row"].get("data") or {}
        # the row's own description: the next filled cell after the identifier
        detail = next((str(v) for k, v in data.items()
                       if k != id_column and v is not None and str(v).strip()), "")
        if kept:
            linked += 1
            ranked.append({"id": entry["id"], "detail": detail, "links": len(kept)})
        elif link_family:
            unlinked_items.append({"id": entry["id"], "detail": detail, "links": 0})

    ranked.sort(key=lambda e: (-e["links"], e["id"]))

    items = len(seen)
    return {
        "idColumn": id_column,
        "idLabel": found.get("idLabel", ""),
        "family": family,
        "items": items,
        "unreadable": unreadable,
        "duplicates": duplicates,
        "linkFamily": link_family,
        "linked": linked,
        "unlinked": len(unlinked_items),
        "links": links,
        "linkedPct": round(linked / items * 100, 1) if items and link_family else None,
        "unlinkedItems": unlinked_items[:MAX_LISTED_ITEMS],
        "mostLinked": ranked[:MAX_LISTED_ITEMS],
    }


def rank_dimensions(columns: list[dict], distincts: dict[str, int],
                    row_count: int) -> list[dict]:
    """The columns worth breaking the run down by, richest first.

    A dimension has to repeat: at least two values, no more than a dozen, and
    fewer than there are rows. On the real acceptance sheet that keeps `type`
    (8), `Tester` (7), `Browser ver.` (3), `Authentication` (3) and `OS ver.`
    (2), and drops `cellSens Edition` (25 values over 39 rows) and the three
    columns nobody filled in.
    """
    out = []
    for col in columns:
        if col.get("type") != "string":
            continue
        n = distincts.get(col["key"], 0)
        if n < MIN_DIMENSION_VALUES or n > MAX_DIMENSION_VALUES or n >= row_count:
            continue
        out.append({"key": col["key"], "label": col.get("label", col["key"]),
                    "distinct": n})
    out.sort(key=lambda d: (-d["distinct"], d["label"]))
    return out


def choose_dimension(dimensions: list[dict], requested: str = "") -> str:
    """The breakdown in force. A requested column the sheet does not have falls
    back to the default rather than answering with an empty chart."""
    keys = {d["key"] for d in dimensions}
    if requested and requested in keys:
        return requested
    return dimensions[0]["key"] if dimensions else ""


def label_columns(columns: list[dict], dimensions: list[dict],
                  stats: dict[str, dict] | None = None,
                  exclude: tuple = ()) -> list[str]:
    """How to name one row.

    The generic dashboard joins every text column, which on the acceptance
    sheet reads "Win11 Pro 64bit · MS Edge · Clean Install · Dimension · Full ·
    Online · Sreelakshmi". A row is better named by whatever the sheet numbers
    it with, plus the two things that distinguish it — "4 · Clean Install ·
    Sreelakshmi".

    A sheet with no serial and nothing to group by — a list of test cases with
    a status against each — falls back to its own leading column and its most
    descriptive one: "REG-1003 · Export workflow".
    """
    serial = next((c["key"] for c in columns
                   if c.get("type") == "number"
                   and SERIAL_LABEL.search(c.get("label", ""))), "")
    keys = [serial] if serial else []
    if not keys:
        keys += [c["key"] for c in columns
                 if c.get("type") == "string" and c["key"] not in exclude][:1]
    keys += [d["key"] for d in dimensions[:2] if d["key"] not in exclude]

    if len(keys) < 2 and stats:
        # nothing groups this sheet, so name the row by what describes it: the
        # remaining text column carrying the longest values
        def length_of(col: dict) -> float:
            values = (stats.get(col["key"]) or {}).get("values") or []
            return sum(len(str(v)) for v in values) / len(values) if values else 0.0

        rest = [c for c in columns
                if c.get("type") == "string"
                and c["key"] not in keys and c["key"] not in exclude]
        if rest:
            keys.append(max(rest, key=length_of)["key"])

    return [k for k in dict.fromkeys(keys) if k]


def describe(columns: list[dict], totals: dict[str, float],
             stats: dict[str, dict] | None = None, row_count: int = 0) -> dict:
    """The profile this workbook falls into, and why.

    Counted outcomes are looked for first: a sheet carrying both a `Pass`
    column and a `Status` column is reporting numbers, and the numbers are the
    more precise answer. `volume` — a measure summed and grouped — stays the
    answer for everything that is neither.
    """
    run = detect_run_columns(columns)
    if is_run_results(run):
        return {"kind": "run_results",
                "reason": f'columns {run["passedLabel"]!r} and '
                          f'{run["failedLabel"] or run["totalLabel"]!r} '
                          f'record what happened when the tests ran',
                "runResults": summarise_run(totals, run), "status": None}

    status = detect_status_column(columns, stats or {}, row_count)
    if is_status(status):
        return {"kind": "status",
                "reason": f'column {status["statusLabel"]!r} records an outcome '
                          f'against each row',
                "runResults": None, "status": status, "inventory": None}

    # only when there is nothing to sum: a sheet that counts something has a
    # measure worth grouping, and the volume view is the right one for it
    if primary_measure(columns) is None:
        inventory = detect_inventory(columns, stats or {})
        if is_inventory(inventory):
            return {"kind": "inventory",
                    "reason": f'column {inventory["idLabel"]!r} lists identifiers '
                              f'and the sheet has nothing to count',
                    "runResults": None, "status": None, "inventory": inventory}

    return {"kind": "volume", "reason": "no outcome columns found",
            "runResults": None, "status": None, "inventory": None}


def bucket_rate(bucket: dict[str, Any], found: dict) -> dict:
    """One breakdown row, with its own pass rate over what it executed."""
    passed = float(bucket.get("passed") or 0)
    failed = float(bucket.get("failed") or 0)
    executed = passed + failed
    return {**bucket,
            "executed": executed,
            "passRatePct": round(passed / executed * 100, 1) if executed else None}
