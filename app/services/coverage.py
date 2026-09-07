"""Bidirectional Feature <-> System coverage, keyed on each workbook's first column.

Two questions, asked of the same pair of testing layers:

    Feature -> System   is every feature planned for this release covered by
                        a system requirement?
    System  -> Feature  is every item in system scope represented at feature
                        level?

Both are set comparisons over one shared key. Nothing here touches the database
or the parser: it takes rows as they were ingested and returns the comparison,
so the answer is computed at read time and can never drift from the data it
describes.

**Where the key comes from.** The first column of the workbook the row came
from — per file, not per layer, because two workbooks feeding one layer need
not be shaped alike. That column is read for the *first* identifier-shaped
token it holds, which is what lets a sheet with no separate id column work:
`FL-5773 - Helix: support of multi-camera sync` yields `FL-5773` and the rest
is description.

**No identifier format is assumed.** The token pattern is a generic
prefix-and-number, so `FL-5773`, `PS101`, `REQ_88` and `ABC-1234` are all read
the same way; nothing in this module knows what "FL" means. The dominant
prefix a side's first column produces is its *family*, discovered rather than
configured.

**One key, and only one.** Identifiers of any other family that the row
mentions — the CS ids, typically — are collected as supporting detail and
shown beside the gap. They are never keys, never compared across sides and
never counted: a coverage figure that quietly matched on a second identifier
would be answering a question nobody asked.
"""
import re
from collections import Counter
from functools import lru_cache
from typing import Any

# A row's identifier is short. Anything longer is a description that happens to
# contain digits, not an id.
MAX_ID_LENGTH = 40
# Supporting ids are evidence, not a data dump: enough to recognise the gap.
MAX_RELATED = 20
# Gathered before the supporting family is known, so the cap has to allow for
# the ones that will be filtered back out.
GATHER_RELATED = 60

# The generic identifier token: a short alphabetic prefix, an optional
# separator, a number. Deliberately format-agnostic — an admin can override it
# for a workbook that does something stranger, but the default is meant to be
# the answer rather than a starting point.
ID_TOKEN = re.compile(r"\b([A-Za-z][A-Za-z0-9]{0,7})[-_ ]?(\d+)\b")

# "7-Sep-26" is shaped exactly like an identifier, and a sheet carrying a
# date column would otherwise list "Sep-26" as supporting evidence. Only the
# supporting scan is filtered: the identifier column is the identifier
# column, and second-guessing what it holds is what this rewrite exists to
# stop doing.
_MONTHS = frozenset((
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
    "nov", "dec", "january", "february", "march", "april", "june", "july",
    "august", "september", "october", "november", "december",
))


# --- reading one value --------------------------------------------------


def _clean(raw: Any) -> str:
    if raw is None:
        return ""
    return re.sub(r"\s+", " ", str(raw).replace("\xa0", " ")).strip()


@lru_cache(maxsize=64)
def _compiled(pattern: str) -> re.Pattern | None:
    """An admin's override pattern, or None when it will not compile.

    A bad pattern falls back to the built-in token rather than failing the
    whole report: the coverage screen is where you would go to notice it.
    """
    try:
        return re.compile(pattern)
    except re.error:
        return None


def _rx(pattern: str) -> tuple[re.Pattern, bool]:
    """The pattern in force, and whether it is an admin's rather than ours.

    A pattern that will not compile counts as no pattern: the report keeps
    working on the built-in token, and the mistake is visible on the screen
    where it was made rather than as a 500.
    """
    if pattern:
        compiled = _compiled(pattern)
        if compiled is not None:
            return compiled, True
    return ID_TOKEN, False


def tokens(text: str, pattern: str = "") -> list[str]:
    """Every identifier-shaped run in a value, in the order it reads."""
    rx, _override = _rx(pattern)
    return [m.group(0).strip() for m in rx.finditer(text) if m.group(0).strip()]


def canonical(token: str) -> str:
    """The match key: case and separators dropped.

    `FL-5773`, `fl 5773` and `FL_5773` are the same identifier written by three
    people. Matching on the text as spelled would report the difference as a
    coverage gap, which is the most annoying kind of wrong answer — the id is
    right there on both sheets.
    """
    return re.sub(r"[\s\-_]+", "", token).casefold()


def family_of(token: str) -> str:
    """The identifier's prefix, uppercased. `FL-5773` -> `FL`, `1.2.3` -> ``."""
    match = re.match(r"[A-Za-z]+", token)
    return match.group(0).upper() if match else ""


def read_identifier(raw: Any, pattern: str = "") -> tuple[str, str, str] | None:
    """The primary identifier a cell holds, as `(key, as written, family)`.

    The *first* token wins and the remainder of the cell is description. Where
    the built-in token finds nothing the cell is taken whole, provided it is
    short enough to be an id and carries a digit — that is what makes a clean
    column of `5773` or `1.2.3` work, while a first column of prose yields
    nothing rather than inventing keys and reporting perfect coverage of
    them. An admin's own pattern gets no such fallback: it is a statement
    about what an id looks like here, and quietly widening it would make the
    override useless. Returns None when the cell yields no identifier.
    """
    text = _clean(raw)
    if not text:
        return None
    found = tokens(text, pattern)
    if found:
        return canonical(found[0]), found[0], family_of(found[0])
    if _rx(pattern)[1]:
        return None
    if len(text) <= MAX_ID_LENGTH and any(c.isdigit() for c in text):
        return canonical(text), text, family_of(text)
    return None


def related_ids(row: dict, id_column: str, primary_family: str,
                pattern: str = "") -> list[tuple[str, str]]:
    """Supporting identifiers the row mentions, as `(id, the cell it is in)`.

    The whole row is scanned, not one configured column, because a supporting
    id moves between columns from one release's workbook to the next and this
    is evidence rather than a key. Identifiers of the primary family are left
    out: those are keys, and listing one here would invite it to be read as a
    second traceability link.

    Trailing tokens in the identifier column count too — a first column reading
    `FL-5773 / CS-114 - description` carries both.

    The cell travels with the id because the id alone does not say what it
    refers to: `CS-4312` means nothing until you can see it sitting in
    "CS-4312 - GPU: Support Blackwell Technology". Which of the two the
    reader is shown is the screen's business, not this function's.
    """
    data = row.get("data") or {}
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def take(value: Any, skip_first: bool = False) -> None:
        text = _clean(value)
        found = tokens(text, pattern)
        for token in found[1:] if skip_first else found:
            fam = family_of(token)
            if fam == primary_family or fam.lower() in _MONTHS:
                continue
            key = canonical(token)
            if key in seen:
                continue
            seen.add(key)
            out.append((token, text))

    take(data.get(id_column), skip_first=True)
    for column, value in data.items():
        if column == id_column:
            continue
        take(value)
    return out


# --- reading one side ---------------------------------------------------


def id_column_for(row: dict, override: str = "") -> str:
    """Which column this row's identifier is read from.

    The first column of the row's own file, unless an admin has named one and
    the file actually has it — an override from a previous release's shape
    would otherwise match nothing and read as 0%.
    """
    if override and override in (row.get("data") or {}):
        return override
    return row.get("idColumn") or ""


def read_side(rows: list[dict], pattern: str = "", override: str = "") -> dict:
    """Everything one side of the comparison contributes.

    Rows arrive in file order, grouped by file and section. That ordering is
    what makes forward-fill legible: the parser fills a sparse leading column
    down from the row above it, so a workbook that writes an id once per group
    leaves it repeated on every row beneath. Those repeats are *contiguous*,
    and are counted as one occurrence of the id rather than as duplicates —
    the same id turning up again further down the sheet still is one.
    """
    occurrences: dict[str, list[dict]] = {}
    blocks: Counter = Counter()
    display: dict[str, str] = {}
    families: Counter = Counter()
    numeric = total = 0
    unresolved: list[dict] = []
    columns_used: dict[str, str] = {}
    previous: tuple[str, str, str] | None = None  # snapshot, section, key

    for row in rows:
        column = id_column_for(row, override)
        if column:
            columns_used.setdefault(row.get("snapshotId", ""), column)
        found = read_identifier((row.get("data") or {}).get(column), pattern)
        if found is None:
            unresolved.append(row)
            previous = None
            continue
        key, original, fam = found
        display.setdefault(key, original)
        families[fam] += 1
        total += 1
        if not fam:
            numeric += 1
        occurrences.setdefault(key, []).append(row)
        here = (row.get("snapshotId", ""), row.get("section", ""), key)
        if previous != here:
            blocks[key] += 1
        previous = here

    family = families.most_common(1)[0][0] if families else ""

    # An id can also land in a section title: the parser turns a row holding
    # nothing but its id into one, so the id never reaches a column. Those are
    # read too, but only when they carry the family this side is already
    # speaking — otherwise an ordinary heading like "Camera testing 2" would
    # be mined for an identifier that was never there.
    if family:
        first_of: dict[str, dict] = {}
        for row in rows:
            first_of.setdefault(row.get("section") or "", row)
        for section, heading in first_of.items():
            found = read_identifier(section, pattern)
            if found is None or found[2] != family:
                continue
            key, original, _fam = found
            if key in occurrences:
                continue
            display.setdefault(key, original)
            blocks[key] += 1
            occurrences[key] = [{
                "section": section, "data": {},
                "file": heading.get("file", ""),
                "fileName": heading.get("fileName", ""),
                "snapshotId": heading.get("snapshotId", ""),
                "idColumn": heading.get("idColumn", ""),
                "fromSection": True,
            }]

    return {
        "occurrences": occurrences, "blocks": blocks, "display": display,
        "unresolved": unresolved, "family": family, "families": families,
        "columnsUsed": columns_used,
        # a first column of bare 1, 2, 3 keys every row uniquely and reads as
        # total failure; the ratio is what lets the report say so
        "numericRatio": round(numeric / total, 3) if total else 0.0,
    }


# --- the comparison -----------------------------------------------------


def _natural(value: str) -> list:
    """Sort FL-9 before FL-10 rather than after it."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", value)]


_STATUS_ORDER = {"missing_in_system": 0, "missing_in_feature": 1,
                 "unresolved": 2, "covered": 3}


def _warnings(feature: dict, system: dict) -> list[dict]:
    """What would make these figures misleading if read at face value.

    None of these stop the comparison. A report that refuses to run teaches
    nobody anything; one that runs and says why it looks odd is actionable.
    """
    out: list[dict] = []
    f_family, s_family = feature["family"], system["family"]
    if f_family and s_family and f_family != s_family:
        out.append({
            "code": "family_mismatch", "side": "",
            "message": (f"Feature ids start with '{f_family}' and system ids "
                        f"with '{s_family}'. The two sheets are not naming the "
                        f"same things, so almost nothing will match."),
        })
    for side, name in ((feature, "feature"), (system, "system")):
        if side["numericRatio"] >= 0.9 and side["occurrences"]:
            out.append({
                "code": "numeric_key_column", "side": name,
                "message": (f"The first column of the {name} workbooks holds "
                            f"plain numbers. If that is a row number rather "
                            f"than an identifier, set a pattern or column "
                            f"under Matching."),
            })
    return out


def build_coverage(feature_rows: list[dict], system_rows: list[dict],
                   config: dict) -> dict:
    """The bidirectional comparison, as one matrix plus its summary.

    One entry per distinct identifier across both sides, so a single table
    answers both directions at once. Rows whose id could not be read are kept
    as `unresolved` entries rather than dropped: an unreadable id is absent
    from both directions, which is precisely how a coverage report reaches
    100% while being wrong.

    Supporting ids are read from the *feature* side only, and so appear on an
    entry exactly when that entry has a feature row. An id missing from feature
    has no feature row to read them from, and none is guessed at.
    """
    pattern = config.get("pattern") or ""
    layers = config.get("layers") or {}
    feature_column = (layers.get("feature") or {}).get("column", "")
    feature = read_side(feature_rows, pattern, feature_column)
    system = read_side(system_rows, pattern,
                       (layers.get("system") or {}).get("column", ""))

    entries: list[dict] = []
    gathered: dict[str, list[tuple[str, str]]] = {}
    related_family: Counter = Counter()
    for key in set(feature["occurrences"]) | set(system["occurrences"]):
        f_hits = feature["occurrences"].get(key, [])
        s_hits = system["occurrences"].get(key, [])
        status = ("covered" if f_hits and s_hits
                  else "missing_in_system" if f_hits else "missing_in_feature")

        # gathered across the whole forward-filled block, not just its first
        # row: one id can span a group of specs, each naming its own CS ids
        related: list[tuple[str, str]] = []
        seen: set[str] = set()
        for row in f_hits:
            for token, text in related_ids(row, id_column_for(row, feature_column),
                                           feature["family"], pattern):
                folded = canonical(token)
                if folded not in seen:
                    seen.add(folded)
                    related.append((token, text))
                    related_family[family_of(token)] += 1
            if len(related) >= GATHER_RELATED:
                break
        gathered[key] = related

        entries.append({
            "key": key,
            "id": feature["display"].get(key) or system["display"].get(key, key),
            "status": status,
            # repeats produced by the parser filling a sparse leading column
            # down a group are one occurrence, not a data-quality problem
            "duplicate": feature["blocks"][key] > 1 or system["blocks"][key] > 1,
            "featureCount": feature["blocks"][key],
            "systemCount": system["blocks"][key],
            "feature": f_hits[0] if f_hits else None,
            "system": s_hits[0] if s_hits else None,
            "relatedIds": [],
        })

    # One supporting family survives — whichever the feature sheet uses most.
    # A row that names a customer story, a work item and a product code has
    # said one useful thing and two incidental ones, and a gap listing all
    # three reads as noise. Deciding it across the sheet rather than per row
    # keeps every gap listing the same kind of thing.
    supporting = related_family.most_common(1)[0][0] if related_family else ""
    for entry in entries:
        entry["relatedIds"] = [
            {"id": token, "text": text}
            for token, text in gathered.get(entry["key"], [])
            if family_of(token) == supporting
        ][:MAX_RELATED]

    entries.sort(key=lambda e: (_STATUS_ORDER[e["status"]], _natural(e["key"])))

    for side, rows in (("feature", feature["unresolved"]),
                       ("system", system["unresolved"])):
        for index, row in enumerate(rows):
            entries.append({
                "key": f"unresolved:{side}:{index}", "id": "",
                "status": "unresolved", "side": side, "duplicate": False,
                "featureCount": 0, "systemCount": 0, "relatedIds": [],
                "feature": row if side == "feature" else None,
                "system": row if side == "system" else None,
            })

    covered = sum(1 for e in entries if e["status"] == "covered")
    feature_total = len(feature["occurrences"])
    system_total = len(system["occurrences"])
    unresolved = len(feature["unresolved"]) + len(system["unresolved"])
    summary = {
        "featureTotal": feature_total,
        "systemTotal": system_total,
        "covered": covered,
        "missingInSystem": sum(1 for e in entries if e["status"] == "missing_in_system"),
        "missingInFeature": sum(1 for e in entries if e["status"] == "missing_in_feature"),
        "duplicates": sum(1 for e in entries if e["duplicate"]),
        "unresolved": unresolved,
        "family": feature["family"] or system["family"],
        # names the supporting column honestly ("Related CS ids") instead of
        # hardcoding a prefix this module is not supposed to know
        "relatedFamily": supporting,
        # a side with nothing in it is vacuously covered; reporting 0% would
        # read as a failure when the truth is that there is nothing to check
        "forwardCoveragePct": round(covered / feature_total * 100, 1) if feature_total else 100.0,
        "backwardCoveragePct": round(covered / system_total * 100, 1) if system_total else 100.0,
    }
    return {"summary": summary, "entries": entries,
            "warnings": _warnings(feature, system),
            "sides": {"feature": feature, "system": system}}


# --- configuration ------------------------------------------------------

DEFAULT_LAYERS = ("feature", "system")


def describe_side(rows: list[dict], columns: list[dict], pattern: str = "",
                  override: str = "") -> dict:
    """What this layer's identifier read actually did, for the admin screen.

    Choosing a key blind, on sheets whose columns may only be called
    "Column 1", is how a coverage report ends up quietly measuring the wrong
    thing — so the screen shows the column each file was read from, the family
    that came out, and a handful of real ids.
    """
    labels = {c["key"]: c.get("label", c["key"]) for c in columns}
    side = read_side(rows, pattern, override)
    files = {}
    for row in rows:
        snapshot = row.get("snapshotId", "")
        if snapshot in side["columnsUsed"] and snapshot not in files:
            column = side["columnsUsed"][snapshot]
            files[snapshot] = {"fileName": row.get("fileName", ""),
                               "column": column,
                               "columnLabel": labels.get(column, column)}
    matched = sum(len(hits) for hits in side["occurrences"].values())
    return {
        "family": side["family"],
        # every column these workbooks carry, so the screen can offer a real
        # choice: the first column is the default, not a law — plenty of sheets
        # open with a serial number and keep the identifier in the second
        "columns": columns,
        "files": list(files.values()),
        "extracted": [side["display"][k] for k in list(side["display"])[:20]],
        "distinctIds": len(side["occurrences"]),
        "matchedRows": matched,
        "totalRows": len(rows),
        "unresolvedRows": len(side["unresolved"]),
        "numericRatio": side["numericRatio"],
    }


def detect_config(sides: dict[str, dict]) -> dict:
    """The configuration in force when nothing has been saved.

    There is nothing to detect any more — the first column of each file is the
    identifier, by rule. What is returned is what that rule produced, so the
    admin screen opens on the truth rather than on an empty form.

    `sides` maps a layer id to `{"columns": [...], "rows": [...]}`.
    """
    return {
        "pattern": "",
        "layers": {name: {"column": ""} for name in sides},
        "read": {name: describe_side(side.get("rows") or [],
                                     side.get("columns") or [])
                 for name, side in sides.items()},
    }
