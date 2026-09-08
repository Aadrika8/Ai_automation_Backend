"""Build small in-memory .xlsx files for parser and upload tests."""
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

FIXTURES = Path(__file__).parent / "fixtures"
REAL_FILE = FIXTURES / "cellSens-Count.xlsx"


def build_workbook(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


SIMPLE = [
    ["Camera testing"],
    ["Test spec name", "Test spec tab name", "Test Count"],
    ["DP23", "(TS) Color", 10],
    [None, "(TS) Gray", 20],
    ["Microscope testing"],
    ["Test spec name", "Test spec tab name", "Test Count"],
    ["IX73", "(TS) Devices", 5],
    ["IX73", "(TS) Devices", 5],  # exact duplicate
]


# --- traceability: a Feature and a System sheet with a deliberate gap each way
# FL-1001/1002 are covered, FL-1003 is planned but never verified, and the
# system sheet tests FL-2001, which no feature record claims.

FEATURE_SHEET = [
    ["Feature ID", "Feature Name", "Owner"],
    ["FL-1001", "Camera control rework", "team-a"],
    ["FL-1002", "Deconvolution modalities", "team-b"],
    ["FL-1003", "Sub array feature", "team-c"],       # -> missing_in_system
]

SYSTEM_SHEET = [
    ["System ID", "Scenario", "Target Date"],
    ["FL-1001", "End-to-end camera capture", "7-Sep-26"],
    ["FL-1002", "Deconvolution pipeline", "9-Sep-26"],
    ["FL-2001", "Licensing smoke test", "10-Sep-26"],  # -> missing_in_feature
]


# --- a run-results sheet, shaped like the real Acceptance workbook
# Pattern No. is a serial, not a measure; `type` and `Tester` repeat and so can
# group the results; `%age` is a per-row rate that must never be summed.
#
# Every row's (OS, type, Tester) triple is different on purpose. Row identity
# hashes the *string* columns only, so two runs of the same configuration with
# different counts are one row — which is right for a spec sheet and would
# silently drop a repeat run here.
#
# Passed 88, failed 5, not run 3, of 96 planned.

RUN_RESULTS = [
    ["Pattern No.", "OS ver.", "type", "Tester", "TC count", "Pass", "Fail", "NA", "%age"],
    [1, "Win11 Pro 64bit", "Clean Install", "Sreelakshmi", 28, 26, 1, 1, 92.9],
    [2, "Win10 Pro 64bit", "Version Update", "Rahul", 26, 24, 2, 0, 92.3],
    [3, "Win11 Pro 64bit", "Clean Install", "Meera", 20, 18, 1, 1, 90.0],
    [4, "Win10 Pro 64bit", "Repair Install", "Rahul", 10, 9, 0, 1, 90.0],
    [5, "Win11 Pro 64bit", "Silent Install", "Meera", 12, 11, 1, 0, 91.7],
]


# --- a sheet recording the outcome as a word per row, not as counts
# Shaped like the real Regression_Test (2).xlsx. The measure matters: three
# passing rows out of four is 75%, but the 44 test cases behind them out of 53
# is 83%, and those are different claims.

STATUS_SHEET = [
    ["Test ID", "Requirement ID", "Test Description", "Status", "Test Cases"],
    ["REG-1001", "FL-5773", "Microscope connection", "Passed", 12],
    ["REG-1002", "FL-5774", "Image acquisition", "Passed", 18],
    ["REG-1003", "FL-5775", "Export workflow", "Failed", 9],
    ["REG-1004", "FL-5776", "Settings persistence", "Passed", 14],
]


# --- the Feature workbook's continuation convention
# One feature can name several related items, and each goes on its own row with
# only the last column filled. Those rows continue the feature above them; the
# lone "Deconvolution" cell, in the *first* column, is a real section title.
# Both shapes are here because only the column tells them apart.

FEATURE_CONTINUATION = [
    ["ID", "Summary (cellSens)", "PBI / Related Item"],
    ["FL-5773", "Helix: support of new BX57/47", "CS-4614 - Helix US1: Manual control"],
    [None, None, "CS-4615 - Helix US2: Basic support"],
    [None, None, "CS-4616 - Helix US3: Motorized frame"],
    ["FL-5807", "Cicero Spinning Disk (CREST)", None],       # no related item at all
    ["Deconvolution"],                                        # a real section title
    ["FL-5775", "Reorganize CI modalities", "CS-4760 - Refactor modularities"],
    [None, None, "CS-4751 - PFE/GEM"],
]


# --- an inventory: identifiers and the work behind them, nothing to count
# FL-5807 names no work item, which is the whole point of the sheet's dashboard.

INVENTORY_SHEET = [
    ["ID", "Summary (cellSens)", "PBI / Related Item"],
    ["FL-5773", "Helix: support of new BX57/47", "CS-4614 - Helix US1: Manual control"],
    [None, None, "CS-4615 - Helix US2: Basic support"],
    [None, None, "CS-4616 - Helix US3: Motorized frame"],
    ["FL-5776", "FIJI/ImageJ Bridge for cellSens", "CS-4759 - PBI 1: >3 dimensions"],
    [None, None, "CS-4798 - PBI 2: APEX"],
    ["FL-5807", "Cicero Spinning Disk (CREST)", None],
]


def rows_of(parsed, file: str = "sheet.xlsx") -> list[dict]:
    """Parsed rows in the shape the coverage service consumes.

    `idColumn` mirrors what the repository stamps on every row: the key of the
    first column of the file the row came from, which is where the
    traceability identifier lives.
    """
    id_column = parsed.columns[0]["key"] if parsed.columns else ""
    return [{"section": r.section, "data": r.values, "file": file,
             "fileName": file, "snapshotId": f"snap-{file}",
             "rowIndex": i, "idColumn": id_column}
            for i, r in enumerate(parsed.rows)]
