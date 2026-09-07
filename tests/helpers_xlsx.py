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
