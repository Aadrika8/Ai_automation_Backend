"""What is wrong with one workbook, judged on its own.

Reading a sheet is lossy on purpose. Duplicate rows collapse, rows carrying no
measure are dropped as separators, a sheet with no header gets numbered
columns, and a column with one stray word in it stops being a number. Each of
those is the right call, and each is invisible afterwards — the real
regression workbook reads 218 rows and stores 206, and nothing anywhere said
so.

This module says so. It looks at exactly one parse and reports what that read
cost, in terms of the sheet rather than of the parser: what was lost, why, and
which cell to go and look at.

One check here is not about loss. A sheet keyed on identifiers can contradict
itself — a row keyed `FL-5777` whose text reads `CS-4800 - FL-5778 PBI: …`
names two different features and cannot be right about both. Nothing is
dropped and no total moves, so it would be invisible under the rule above; it
is reported because the row is attached to whichever id the *column* holds, and
if that is the stale half then the work is counted against the wrong feature.

**Nothing here compares against a previous load.** A warning is a property of
the file, so it is the same answer on the first read as on the fifth, in any
release, under any layer. The comparison between readings already exists as
`snapshot.diff` and is a different question.

**Nothing here blocks.** A load that refuses leaves the reader with nothing to
act on; one that runs and says what looked wrong leaves them with a task. Two
severities carry that distinction and no more:

    problem — data was lost, or a figure on screen is now wrong
    notice  — worth a look, nothing was lost
"""
import re
from collections import Counter
from typing import Any

from app.services import coverage as cov
from app.services.benchmark import TOTAL_LABEL
from app.services.excel_ingest import ParsedSheet
from app.services.profile import FAIL_LABEL, NOT_RUN_LABEL, PASS_LABEL

# A column filled this rarely is a column nobody is using.
SPARSE_SHARE = 0.2
# Enough names to recognise the problem; the count carries the rest.
MAX_NAMED = 3
# Past this the reader has stopped reading.
MAX_WARNINGS = 8
# A prefix only a third of the rows use is not a naming convention, it is a
# coincidence among product names: the regression workbook's first column holds
# microscope models — IX73, IX83, IX85 — and `IX3` falls out of part numbers
# like IX3-D6REA five times over. Comparing those to each other means nothing.
# Reused from the module that already decides whether a column holds
# identifiers at all.
MIN_IDENTIFIER_SHARE = 0.5
# Past this the sheet has a cross-reference column rather than a mistake in
# one: a warning on most of the rows is the thing this module exists not to do.
MAX_MISMATCH_SHARE = 0.5
# Enough of the offending cell to recognise it in the sheet.
SNIPPET = 70

# Problems first, then the notices in the order someone would act on them.
ORDER = [
    "conflicting_duplicate",
    "measure_column_not_numeric",
    "text_in_measure",
    "mismatched_id",
    "duplicate_rows",
    "separator_rows",
    "unknown_id_reference",
    "empty_columns",
    "sparse_columns",
    "no_header",
]

# Labels that promise a number. Reused from the two modules that already decide
# what a measure is, rather than kept in step with a third copy.
MEASURE_LABEL = (TOTAL_LABEL, PASS_LABEL, FAIL_LABEL, NOT_RUN_LABEL)


def _warn(code: str, severity: str, message: str) -> dict:
    return {"code": code, "severity": severity, "message": message}


def _looks_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    try:
        float(str(value).strip())
        return True
    except (TypeError, ValueError):
        return False


def _filled(rows: list, key: str) -> list:
    out = []
    for row in rows:
        value = row.values.get(key)
        if value is not None and str(value).strip():
            out.append(value)
    return out


def _name_list(labels: list[str]) -> str:
    """`End Date, Build, Status +1 more` — enough to recognise, not a dump."""
    shown = ", ".join(labels[:MAX_NAMED])
    rest = len(labels) - MAX_NAMED
    return f"{shown} +{rest} more" if rest > 0 else shown


def _row_label(values: dict, identity_keys: list[str]) -> str:
    """A dropped row named by what made it a duplicate, so it can be found."""
    parts = [str(values[k]) for k in identity_keys
             if values.get(k) is not None and str(values[k]).strip()]
    return " · ".join(parts)


# --- the checks ----------------------------------------------------------


def _duplicates(parsed: ParsedSheet) -> list[dict]:
    """Rows the read collapsed, split by whether that cost anything.

    Two rows identical in every column are one row written twice. Two rows that
    agree on their text and disagree on their numbers are two different claims,
    and keeping the first is a coin toss — the sheet does not say which is
    right, and neither can this. So the warning reports both values and leaves
    the judgement where it belongs, suggesting the fix that usually applies:
    a column that tells the two runs apart.
    """
    if not parsed.duplicates_skipped:
        return []

    conflicting = [d for d in parsed.dropped_duplicates if d["conflicts"]]
    identical = parsed.duplicates_skipped - len(conflicting)
    out: list[dict] = []

    if conflicting:
        first = conflicting[0]
        label = _row_label(first["kept"], parsed.identity_keys)
        labels = {c["key"]: c.get("label", c["key"]) for c in parsed.columns}
        detail = "; ".join(
            f'{labels.get(key, key)} {kept} against {gone}'
            for key, (kept, gone) in list(first["conflicts"].items())[:2])
        more = (f" {len(conflicting) - 1} other row(s) disagree the same way."
                if len(conflicting) > 1 else "")
        out.append(_warn(
            "conflicting_duplicate", "problem",
            f"Rows were dropped that did not agree. “{label}” appears more than "
            f"once recording different values — {detail} — and only the first "
            f"was kept.{more} The sheet does not say which is right; if these "
            f"are two separate runs, add a column that tells them apart."))

    if identical > 0:
        out.append(_warn(
            "duplicate_rows", "notice",
            f"{identical} row(s) repeat an earlier row exactly, so only the "
            f"first of each was kept. No figure changed."))
    return out


def _measure_columns(parsed: ParsedSheet) -> list[dict]:
    """Columns that were meant to hold numbers and partly do not.

    Two shapes, distinguished by what the parser made of the column.

    A column it counts as a *measure* with a few cells that are not numbers:
    those cells are silently left out of every total, so a figure on screen is
    quietly short. The column still works — it is the number that is wrong.

    A column it counts as *text* despite being named like a count: nothing can
    be summed from it at all. `System_Test.xlsx` had one of these, a column
    called `Pass` whose only filled cell read “make only excel”.
    """
    out: list[dict] = []
    for column in parsed.columns:
        key, label = column["key"], column.get("label", column["key"])
        values = _filled(parsed.rows, key)
        if not values:
            continue
        strays = [v for v in values if not _looks_numeric(v)]

        if column.get("type") == "number":
            if strays:
                shown = ", ".join(f"“{v}”" for v in strays[:MAX_NAMED])
                out.append(_warn(
                    "text_in_measure", "problem",
                    f"{label} is short by {len(strays)} row(s). "
                    f"{len(values) - len(strays)} of {len(values)} cells hold "
                    f"numbers; the rest ({shown}) are not numbers and are left "
                    f"out of every total taken from this column."))
        elif any(rx.search(label) for rx in MEASURE_LABEL):
            shown = ", ".join(f"“{v}”" for v in values[:2])
            out.append(_warn(
                "measure_column_not_numeric", "problem",
                f"{label} cannot be totalled. It is named like a count but "
                f"holds text ({shown}), so nothing can be summed from it and "
                f"any dashboard reading this sheet will skip it."))
    return out


def _column_fill(parsed: ParsedSheet) -> list[dict]:
    """Columns nobody filled in, grouped so a bare tracker is one line.

    The System workbook has four columns filled on no rows at all and two more
    on one row in nine. Reported one per column that is six warnings for one
    fact, and the reader stops at the third.
    """
    total = len(parsed.rows)
    if not total:
        return []
    empty, sparse = [], []
    for column in parsed.columns:
        label = column.get("label", column["key"])
        filled = len(_filled(parsed.rows, column["key"]))
        if filled == 0:
            empty.append(label)
        elif filled / total < SPARSE_SHARE:
            sparse.append(f"{label} ({filled} of {total})")

    out: list[dict] = []
    if empty:
        out.append(_warn(
            "empty_columns", "notice",
            f"{len(empty)} column(s) are never filled: {_name_list(empty)}."))
    if sparse:
        out.append(_warn(
            "sparse_columns", "notice",
            f"{len(sparse)} column(s) are filled on almost no rows: "
            f"{_name_list(sparse)}."))
    return out


def _structure(parsed: ParsedSheet) -> list[dict]:
    out: list[dict] = []
    if parsed.dropped_separators:
        out.append(_warn(
            "separator_rows", "notice",
            f"{parsed.dropped_separators} row(s) carried no value in any "
            f"numeric column and were read as separators rather than data."))
    if parsed.headerless:
        names = ", ".join(c.get("label", "") for c in parsed.columns[:MAX_NAMED])
        out.append(_warn(
            "no_header", "notice",
            f"This sheet has no header row, so its columns are only numbered "
            f"({names}). Everything still reads, but nothing can be told from "
            f"a column's name."))
    return out


def _id_references(parsed: ParsedSheet) -> list[dict]:
    """Rows whose text names an identifier of the sheet’s own family.

    The identifier is the first column’s first token, by the same rule the
    coverage comparison and the inventory dashboard run on. Tokens of that
    family found anywhere *else* in the row are deliberately never read as
    links — doing so would invent traceability out of a description — so this
    is the only place they are looked at, and looking is all it does.

    Two findings, because pointing inside the sheet and pointing outside it
    mean opposite things. `FL-5777` naming `FL-5778`, a feature sitting a few
    rows above it, is a copy of a neighbouring row whose id was never changed.
    `FL-5786` naming `FL-5651`, which this release does not contain, reads as
    the earlier feature it follows on from, and calling that an error would be
    wrong.

    Two guards keep it quiet. A family fewer than half the rows carry is not a
    naming convention: the regression workbook is keyed on microscope models,
    and `IX3` falls out of part numbers like `IX3-D6REA` five times over. And a
    sheet where most rows name another id has a cross-reference column rather
    than a mistake in one.
    """
    if not parsed.rows or not parsed.columns:
        return []
    id_key = parsed.columns[0]["key"]

    own: list[tuple[Any, str, str]] = []   # row, match key, as written
    families: Counter = Counter()
    for row in parsed.rows:
        read = cov.read_identifier(row.values.get(id_key))
        if read is None:
            continue
        key, display, family = read
        own.append((row, key, display))
        if family:
            families[family] += 1
    if not families:
        return []

    family, carrying = families.most_common(1)[0]
    if carrying / len(parsed.rows) < MIN_IDENTIFIER_SHARE:
        return []

    present = {key for _row, key, _display in own}
    inside: list[tuple[str, str, str]] = []
    outside: list[tuple[str, str, str]] = []

    for row, key, display in own:
        if cov.family_of(display) != family:
            continue    # a stray id of another family is not this row’s key
        # one entry per distinct id named, so a cell repeating it down five
        # continuation lines is one finding rather than five
        named: dict[str, tuple[str, str]] = {}
        for column, value in row.values.items():
            if value is None:
                continue
            text = str(value)
            found = cov.tokens(text)
            # the identifier column’s first token is the key itself
            for token in (found[1:] if column == id_key else found):
                if cov.family_of(token) != family:
                    continue
                folded = cov.canonical(token)
                if folded != key:
                    named.setdefault(folded, (token, text))
        for folded, (token, text) in named.items():
            snippet = re.sub(r"\s+", " ", text).strip()[:SNIPPET]
            bucket = inside if folded in present else outside
            bucket.append((display, token, snippet))

    if len(inside) + len(outside) > MAX_MISMATCH_SHARE * len(parsed.rows):
        return []

    out: list[dict] = []
    if inside:
        first, other, snippet = inside[0]
        more = (f" {len(inside) - 1} other row(s) do the same."
                if len(inside) > 1 else "")
        out.append(_warn(
            "mismatched_id", "problem",
            f"{len(inside)} row(s) name a different {family} id than their "
            f"own. “{first}” reads “{snippet}”, and {other} is a separate row "
            f"in this sheet — most likely a copy of a neighbouring row whose id "
            f"was not changed.{more} Which half is stale is not something the "
            f"sheet says: if the id is right the description is out of date, "
            f"and if the description is right this row’s work is counted "
            f"against the wrong {family} id."))
    if outside:
        named_ids = sorted({token for _first, token, _s in outside})
        out.append(_warn(
            "unknown_id_reference", "notice",
            f"{len(outside)} row(s) reference {family} ids this sheet does "
            f"not contain ({_name_list(named_ids)}). If those are earlier "
            f"items the work follows on from, nothing is wrong; if they belong "
            f"in this release, they are missing from it."))
    return out


# --- the report ----------------------------------------------------------


def inspect(parsed: ParsedSheet) -> list[dict]:
    """Everything worth saying about one read of one workbook.

    An empty list is the answer for a clean sheet, and most sheets are clean —
    a report that always finds something is one nobody reads.
    """
    found = (_duplicates(parsed) + _measure_columns(parsed)
             + _id_references(parsed)
             + _column_fill(parsed) + _structure(parsed))
    found.sort(key=lambda w: ORDER.index(w["code"]) if w["code"] in ORDER else 99)
    return found[:MAX_WARNINGS]
