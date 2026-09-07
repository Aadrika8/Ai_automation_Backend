"""The fingerprint that decides whether a load is worth recording, and the
diff between two readings of the same workbook.

Both are per file: a workbook is compared only with its own previous snapshot,
never with another file's.
"""
from tests.helpers_xlsx import FIXTURES, SIMPLE, build_workbook

from app.services.excel_ingest import content_fingerprint, diff_rows, parse_workbook


def sheet(rows):
    return parse_workbook(build_workbook(rows))


# --- fingerprint ---------------------------------------------------------


def test_same_data_gives_the_same_fingerprint():
    assert sheet(SIMPLE).content_hash == sheet(SIMPLE).content_hash


def test_a_changed_measure_changes_the_fingerprint():
    changed = [row[:] if isinstance(row, list) else row for row in SIMPLE]
    changed[2] = ["DP23", "(TS) Color", 999]
    assert sheet(SIMPLE).content_hash != sheet(changed).content_hash


def test_a_renamed_column_changes_the_fingerprint():
    renamed = [row[:] if isinstance(row, list) else row for row in SIMPLE]
    renamed[1] = ["Test spec name", "Test spec tab name", "Cases"]
    assert sheet(SIMPLE).content_hash != sheet(renamed).content_hash


def test_two_different_workbooks_have_different_fingerprints():
    other = [
        ["Camera testing"],
        ["Test spec name", "Test spec tab name", "Test Count"],
        ["DP99", "(TS) Fresh", 7],
    ]
    assert sheet(SIMPLE).content_hash != sheet(other).content_hash


def test_a_resaved_workbook_keeps_its_fingerprint():
    """Saving a workbook rewrites its bytes even when no cell changed — the
    metadata Excel stamps on save is enough. The fingerprint is taken from the
    parsed content, so an untouched sheet stays identical to the framework.

    The saves below differ only by the author recorded in the file, which is
    the smallest faithful stand-in for what a real save does.
    """
    import hashlib
    import io

    from openpyxl import load_workbook

    original = (FIXTURES / "cellSens-Count.xlsx").read_bytes()

    def resave(data, author):
        wb = load_workbook(io.BytesIO(data))
        wb.properties.creator = author
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    once, twice = resave(original, "Sreelakshmi"), resave(original, "Rahul")
    assert hashlib.sha256(once).hexdigest() != hashlib.sha256(twice).hexdigest()
    assert parse_workbook(once).content_hash == parse_workbook(twice).content_hash


def test_identity_keys_are_the_string_columns():
    parsed = sheet(SIMPLE)
    assert parsed.identity_keys == ["test_spec_name", "test_spec_tab_name"]
    assert content_fingerprint(parsed.columns, parsed.rows) == parsed.content_hash


# --- diff ----------------------------------------------------------------


def test_diff_counts_added_changed_and_removed():
    before = sheet(SIMPLE)
    stored = [{"rowKey": r.row_key, "data": r.values} for r in before.rows]

    after_rows = [row[:] if isinstance(row, list) else row for row in SIMPLE]
    after_rows[2] = ["DP23", "(TS) Color", 999]          # changed measure
    after_rows.append(["DP99", "(TS) Brand New", 42])     # added
    after_rows.remove(["IX73", "(TS) Devices", 5])        # removed (both copies)
    after_rows.remove(["IX73", "(TS) Devices", 5])

    assert diff_rows(stored, sheet(after_rows).rows) == {
        "added": 1, "changed": 1, "removed": 1, "comparable": True}


def test_diff_of_identical_data_is_empty():
    parsed = sheet(SIMPLE)
    stored = [{"rowKey": r.row_key, "data": r.values} for r in parsed.rows]
    assert diff_rows(stored, parsed.rows) == {
        "added": 0, "changed": 0, "removed": 0, "comparable": True}


def test_diff_against_nothing_is_all_added():
    parsed = sheet(SIMPLE)
    assert diff_rows([], parsed.rows)["added"] == len(parsed.rows)
