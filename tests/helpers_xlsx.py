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
