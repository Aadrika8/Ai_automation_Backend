"""The release QA report: facts in, prose out, and nothing invented.

OpenAI is never called here. `get_report_writer` is overridden with a stand-in
that records the facts it was handed and answers with a fixed report, so every
test is offline and free — and what would have reached OpenAI can be checked.
"""
import json

import pytest_asyncio

from app import db as db_module
from app.main import app as fastapi_app
from app.services import report as rpt
from app.services.report_writer import ReportError, get_report_writer
from tests.helpers_xlsx import FEATURE_SHEET, SYSTEM_SHEET, build_workbook

RELEASE = "/api/apps/cellsens/releases/v4-4"
REPORT = f"{RELEASE}/report"
COVERAGE = f"{RELEASE}/traceability"
SEPTEMBER = {"year": 2026, "month": 9}
LAYERS = ("feature", "system", "acceptance")

# a results sheet with a Tester column: the names must never leave the app
ACCEPTANCE = [
    ["Pattern No.", "OS ver.", "Tester", "TC count", "Pass", "Fail"],
    [1, "Win11 Pro 64bit", "Rahul", 57, 54, 3],
    [2, "Win10 Pro 64bit", "Meera", 43, 41, 2],
]


def reply(forward_pct: float, extra: str = "") -> dict:
    """A report that quotes the facts — plus whatever `extra` slips in."""
    return {
        "summary": f"{forward_pct}% of planned features have a system test.{extra}",
        "findings": [{"text": "FL-1003 (Sub array feature) has no system test.",
                      "sources": ["traceability"]}],
        "gaps": [{"text": "Automation is not measured.", "sources": ["automation", "bogus"]}],
        "risks": [{"text": "An untested feature may ship.", "severity": "high",
                   "sources": ["traceability", "traceability"]}],
        "recommendations": [{"text": "Add a system test for FL-1003.",
                             "sources": ["layer:system"]}],
    }


class FakeWriter:
    model = "fake-model"

    def __init__(self, answer=None, error: Exception | None = None):
        self.answer, self.error, self.facts = answer, error, None

    async def write(self, facts: dict) -> dict:
        self.facts = facts
        if self.error:
            raise self.error
        if self.answer is not None:
            return self.answer
        return reply(facts["traceability"]["featuresWithSystemTestPct"])


def use(writer) -> None:
    fastapi_app.dependency_overrides[get_report_writer] = lambda: writer


async def forget(client, admin_headers):
    await db_module.get_db().qa_reports.delete_many({"appId": "cellsens"})
    await db_module.get_db().trace_configs.delete_many({"appId": "cellsens"})
    for layer in LAYERS:
        await client.delete(f"{RELEASE}/layers/{layer}/records", headers=admin_headers)


@pytest_asyncio.fixture
async def release(client, admin_headers, qa_headers, tmp_path):
    """v4.4 holding a feature, a system and an acceptance workbook."""
    folder = tmp_path / "cellSens" / "v4.4"
    folder.mkdir(parents=True)
    (folder / "feature.xlsx").write_bytes(build_workbook(FEATURE_SHEET))
    (folder / "system.xlsx").write_bytes(build_workbook(SYSTEM_SHEET))
    (folder / "acceptance.xlsx").write_bytes(build_workbook(ACCEPTANCE))

    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    await forget(client, admin_headers)
    res = await client.post(f"{RELEASE}/snapshots", headers=qa_headers,
                            json={"period": SEPTEMBER})
    assert res.status_code == 200, res.text

    yield folder

    fastapi_app.dependency_overrides.pop(get_report_writer, None)
    await forget(client, admin_headers)
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": ""})


# --- not set up --------------------------------------------------------------


async def test_an_unconfigured_server_says_so_rather_than_failing(
        client, qa_headers, release):
    use(None)
    body = (await client.get(REPORT, headers=qa_headers)).json()
    assert body["configured"] is False
    assert body["report"] is None
    res = await client.post(REPORT, headers=qa_headers)
    assert res.status_code == 503
    assert "OPENAI_API_KEY" in res.json()["detail"]


# --- who may do what -----------------------------------------------------------


async def test_a_manager_may_read_but_not_generate(client, manager_headers, release):
    use(FakeWriter())
    assert (await client.post(REPORT, headers=manager_headers)).status_code == 403
    assert (await client.get(REPORT, headers=manager_headers)).status_code == 200


async def test_reading_needs_a_token(client, release):
    assert (await client.get(REPORT)).status_code == 401


# --- generating and reading back ---------------------------------------------


async def test_a_report_is_generated_saved_and_read_back(client, qa_headers,
                                                         manager_headers, release):
    use(FakeWriter())
    res = await client.post(REPORT, headers=qa_headers)
    assert res.status_code == 200, res.text
    made = res.json()["report"]
    assert made["report"]["summary"].startswith("66.7%")
    assert made["createdBy"] == "qa"
    assert made["model"] == "fake-model"
    assert made["releaseName"] == "v4.4"
    assert made["period"] == "2026-09"
    assert made["unverified"] == []

    read = (await client.get(REPORT, headers=manager_headers)).json()
    assert read["configured"] is True
    assert read["stale"] is False
    assert read["report"]["id"] == made["id"]


async def test_a_figure_the_data_does_not_hold_is_flagged(client, qa_headers, release):
    """88.8% is nowhere in the facts. It is saved, and called out."""
    writer = FakeWriter()
    writer.answer = reply(66.7, " About 88.8% of the plan is at risk.")
    use(writer)
    made = (await client.post(REPORT, headers=qa_headers)).json()["report"]
    assert made["unverified"] == ["88.8"]


async def test_what_the_draft_left_out_is_added_from_the_data(client, qa_headers, release):
    """The stand-in names FL-1003 and 66.7% but never FL-2001, nor System's
    3 rows against Feature's 3. Both are added as plain lines from the data,
    marked as added, and nothing is left out of the saved report."""
    use(FakeWriter())
    made = (await client.post(REPORT, headers=qa_headers)).json()["report"]
    added = made["addedFromData"]
    assert "System items not in the feature plan: FL-2001" in added
    assert any(a.startswith("Pyramid violation: System Testing (3 rows)") for a in added)
    assert not any("Planned features without a system test" in a for a in added)
    assert made["notCovered"] == []
    assert made["unverified"] == []

    filled = [p for p in made["report"]["gaps"] if p["added"]]
    assert filled[0]["text"] == ("System items not in the feature plan: "
                                 "FL-2001 (Licensing smoke test).")
    assert filled[0]["sources"] == ["traceability"]
    assert not any(p["added"] for p in made["report"]["recommendations"])
    read = (await client.get(REPORT, headers=qa_headers)).json()["report"]
    assert read["addedFromData"] == added


async def test_sources_are_only_ones_the_screen_can_link(client, qa_headers, release):
    use(FakeWriter())
    made = (await client.post(REPORT, headers=qa_headers)).json()["report"]["report"]
    assert made["gaps"][0]["sources"] == ["automation"]               # "bogus" dropped
    assert made["risks"][0]["sources"] == ["traceability"]            # once, not twice
    assert made["recommendations"][0]["sources"] == ["layer:system"]


# --- what reaches OpenAI -------------------------------------------------------


async def test_the_facts_carry_ids_and_titles_but_never_people(client, qa_headers, release):
    writer = FakeWriter()
    use(writer)
    await client.post(REPORT, headers=qa_headers)
    sent = json.dumps(writer.facts)

    assert "Rahul" not in sent and "Meera" not in sent
    trace = writer.facts["traceability"]
    assert trace["featuresWithSystemTestPct"] == 66.7
    assert trace["plannedFeaturesWithoutSystemTest"] == [
        {"id": "FL-1003", "title": "Sub array feature"}]
    assert trace["systemItemsNotInFeaturePlan"] == [
        {"id": "FL-2001", "title": "Licensing smoke test"}]

    acceptance = next(l for l in writer.facts["layers"] if l["id"] == "acceptance")
    run = acceptance["files"][0]["runResults"]
    assert (run["passed"], run["failed"], run["passRatePct"]) == (95, 5, 95.0)
    assert writer.facts["automation"]["measured"] is False
    assert "automated" not in writer.facts["automation"]     # not measured is not zero
    unit = next(l for l in writer.facts["layers"] if l["id"] == "unit")
    assert unit["loaded"] is False


async def test_generating_changes_no_dashboard_figure(client, qa_headers, release):
    before_trace = (await client.get(COVERAGE, headers=qa_headers)).json()["summary"]
    before_dash = (await client.get(f"{RELEASE}/layers/acceptance/dashboard",
                                    headers=qa_headers)).json()
    use(FakeWriter())
    await client.post(REPORT, headers=qa_headers)
    assert (await client.get(COVERAGE, headers=qa_headers)).json()["summary"] == before_trace
    after_dash = (await client.get(f"{RELEASE}/layers/acceptance/dashboard",
                                   headers=qa_headers)).json()
    assert after_dash["totals"] == before_dash["totals"]
    assert after_dash["profile"] == before_dash["profile"]


# --- when things go wrong ---------------------------------------------------------


async def test_an_openai_failure_reaches_the_screen_as_a_sentence(client, qa_headers, release):
    use(FakeWriter(error=ReportError(429, "OpenAI's rate limit or quota was reached.")))
    res = await client.post(REPORT, headers=qa_headers)
    assert res.status_code == 429
    assert res.json()["detail"] == "OpenAI's rate limit or quota was reached."
    assert (await client.get(REPORT, headers=qa_headers)).json()["report"] is None


async def test_a_reply_without_the_reports_sections_is_refused(client, qa_headers, release):
    use(FakeWriter(answer={"findings": []}))
    res = await client.post(REPORT, headers=qa_headers)
    assert res.status_code == 502
    assert (await client.get(REPORT, headers=qa_headers)).json()["report"] is None


async def test_a_report_goes_stale_when_the_data_changes(client, qa_headers, release):
    use(FakeWriter())
    await client.post(REPORT, headers=qa_headers)
    (release / "system.xlsx").write_bytes(build_workbook(SYSTEM_SHEET + [
        ["FL-1003", "Sub array end-to-end", "12-Sep-26"]]))
    await client.post(f"{RELEASE}/snapshots", headers=qa_headers, json={"period": SEPTEMBER})
    assert (await client.get(REPORT, headers=qa_headers)).json()["stale"] is True


# --- the pure parts ---------------------------------------------------------------


def test_numbers_are_read_the_same_way_however_they_are_written():
    assert rpt.figures("140,789 cases, 53.8% and FL-5777 in 4.5.1") == {
        "140789", "53.8", "5777", "4.5", "1"}
    assert rpt.figures("40.0") == rpt.figures("40")


def test_the_pyramid_is_judged_as_the_layers_page_judges_it():
    """Test cases where both layers declare them, rows otherwise, and nothing
    judged against an empty layer."""
    layers = [
        {"name": "Regression", "order": 1, "recordCount": 210, "testCount": 140789},
        {"name": "Feature", "order": 2, "recordCount": 13, "testCount": None},
        {"name": "System", "order": 3, "recordCount": 15, "testCount": 315},
        {"name": "Acceptance", "order": 4, "recordCount": 39, "testCount": 1286},
        {"name": "Smoke", "order": 5, "recordCount": 0, "testCount": None},
    ]
    pairs = rpt.pyramid(layers)
    assert [(p["upper"], p["unit"], p["verdict"]) for p in pairs] == [
        ("Feature", "rows", "holds"),
        ("System", "rows", "violated"),            # 15 rows against Feature's 13
        ("Acceptance", "test cases", "violated"),  # 1,286 against System's 315
        ("Smoke", "rows", "not checked"),
    ]


def test_unmeasured_automation_carries_no_zero_to_misquote():
    """The first real report turned 0 of 0 into "0 automated tests recorded"."""
    facts = rpt.automation_facts({
        "evident": {"measured": False, "coveragePct": None, "automated": 0.0, "total": 0.0,
                    "unmeasuredLayers": ["unit"]},
        "reference": {"low": 33.0, "high": 44.0, "scope": "all-industry"},
        "distance": {"position": "unmeasured", "points": None},
    }, {"unit": "Unit Testing"})
    assert facts["measured"] is False
    assert not {"automated", "total", "coveragePct", "pointsFromRange"} & set(facts)
    assert facts["notMeasuredLayers"] == ["Unit Testing"]


def test_measured_automation_keeps_its_counts():
    facts = rpt.automation_facts({
        "evident": {"measured": True, "coveragePct": 30.0, "automated": 790.0, "total": 2635.0,
                    "unmeasuredLayers": []},
        "reference": {"low": 33.0, "high": 44.0, "scope": "all-industry"},
        "distance": {"position": "below", "points": 3},
    }, {})
    assert (facts["coveragePct"], facts["automated"], facts["total"],
            facts["pointsFromRange"]) == (30.0, 790.0, 2635.0, 3)


def test_an_empty_layer_is_marked_not_loaded():
    """0 rows means no workbook loaded — not that no testing happened."""
    empty = rpt.layer_facts({"id": "unit", "name": "Unit Testing", "recordCount": 0,
                             "testCount": None}, [])
    loaded = rpt.layer_facts({"id": "system", "name": "System Testing", "recordCount": 9,
                              "testCount": 165}, [])
    assert empty["loaded"] is False and loaded["loaded"] is True


FACTS = {
    "pyramid": [
        {"lower": "System Testing", "upper": "Acceptance Testing", "unit": "test cases",
         "lowerValue": 165, "upperValue": 1286, "verdict": "violated"},
        {"lower": "Feature Testing", "upper": "System Testing", "unit": "rows",
         "lowerValue": 13, "upperValue": 9, "verdict": "holds"},
    ],
    "traceability": {
        "available": True,
        "featuresWithSystemTestPct": 53.8, "systemItemsInFeaturePlanPct": 77.8,
        "plannedFeaturesWithoutSystemTest": [{"id": "FL-5761", "title": ""},
                                             {"id": "FL-5778", "title": ""}],
        "systemItemsNotInFeaturePlan": [{"id": "FL-5842", "title": ""}],
        "idMismatches": [{"id": "FL-5777", "names": "FL-5778", "text": ""}],
    },
    "layers": [
        {"id": "acceptance", "name": "Acceptance Testing", "files": [
            {"warnings": [{"code": "text_in_measure", "severity": "problem"},
                          {"code": "empty_columns", "severity": "notice"}]}]},
        {"id": "feature", "name": "Feature Testing", "files": [
            {"warnings": [{"code": "mismatched_id", "severity": "problem"}]}]},
    ],
}


def body(*texts: str) -> dict:
    return {"summary": texts[0], "findings": [{"text": t, "sources": []} for t in texts[1:]],
            "gaps": [], "risks": [], "recommendations": []}


def test_a_complete_report_leaves_nothing_out():
    assert rpt.not_covered(body(
        "System has 165 test cases against Acceptance's 1,286.",
        "53.8% of planned features have a system test; 77.8% of system items are planned.",
        "FL-5761 and FL-5778 have no system test; FL-5842 is not in the plan.",
        "FL-5777's text names FL-5778.",
        "Acceptance counts hold text, so its totals are short.",
    ), FACTS) == []


def test_everything_left_out_is_named():
    assert rpt.not_covered(body("A quiet release."), FACTS) == [
        "Pyramid violation: Acceptance Testing (1,286 test cases) is not smaller than "
        "System Testing (165)",
        "Traceability: 53.8% of planned features have a system test",
        "Traceability: 77.8% of system items are in the feature plan",
        "Planned features without a system test: FL-5761, FL-5778",
        "System items not in the feature plan: FL-5842",
        "ID mismatch: FL-5777’s text names FL-5778",
        "Acceptance Testing: text in a count column",
    ]


def test_ids_are_matched_whole():
    """FL-576 is not FL-5761, and the report has to name the ids themselves."""
    missing = rpt.not_covered(body("FL-576 and FL-577 need tests."), FACTS)
    assert "Planned features without a system test: FL-5761, FL-5778" in missing


def test_a_warning_counts_only_where_its_layer_is_named():
    """"Text" in a point about Feature does not cover Acceptance's warning."""
    missing = rpt.not_covered(body("Feature descriptions contain free text."), FACTS)
    assert "Acceptance Testing: text in a count column" in missing


def test_the_env_file_key_beats_a_stale_one_in_the_environment(tmp_path, monkeypatch):
    """A machine-wide OPENAI_API_KEY left over from another tool used to win,
    and every report failed while the key in .env was valid."""
    from app.config import OpenAISettings
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-from-env-file\nOPENAI_MODEL=gpt-4o-mini\n")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-stale-machine-wide")
    settings = OpenAISettings(_env_file=env_file)
    assert settings.openai_api_key == "sk-from-env-file"
    assert settings.openai_model == "gpt-4o-mini"


def test_a_blank_env_file_key_falls_back_to_the_environment(tmp_path, monkeypatch):
    from app.config import OpenAISettings
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=\n")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-set-for-the-server")
    assert OpenAISettings(_env_file=env_file).openai_api_key == "sk-set-for-the-server"



def test_filling_leaves_nothing_out_and_invents_nothing():
    filled, added = rpt.fill_gaps(body("A quiet release."), FACTS)
    assert len(added) == 7
    assert rpt.not_covered(filled, FACTS) == []
    assert rpt.unverified(filled, FACTS) == []
    lines = [p for section in rpt.SECTIONS for p in filled[section] if p.get("added")]
    assert len(lines) == 7
    assert ("Acceptance Testing has 1,286 test cases, not fewer than the 165 in System "
            "Testing, so the testing pyramid is inverted between these two layers."
            ) in [p["text"] for p in lines]
    assert {"text": "Acceptance Testing: a count column holds some text, so totals taken "
                    "from it are short.", "sources": ["layer:acceptance"], "added": True
            } in filled["gaps"]


def test_a_complete_report_gets_nothing_added():
    complete = body(
        "System has 165 test cases against Acceptance's 1,286.",
        "53.8% of planned features have a system test; 77.8% of system items are planned.",
        "FL-5761 and FL-5778 have no system test; FL-5842 is not in the plan.",
        "FL-5777's text names FL-5778.",
        "Acceptance counts hold text, so its totals are short.",
    )
    filled, added = rpt.fill_gaps(complete, FACTS)
    assert added == []
    assert not any(p.get("added") for section in rpt.SECTIONS for p in filled[section])
