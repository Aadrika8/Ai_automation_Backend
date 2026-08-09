"""AI report endpoint: RBAC, caching by data hash, unconfigured-key 503,
and payload compression. OpenAI is always mocked — no credits spent in tests."""
import pytest

from app import report_ai
from app.config import get_settings
from app.report_ai import _truncate_trace, build_payload

FAKE_REPORT = {
    "summary": "Suite is mostly healthy.",
    "healthAssessment": "Pass rate is stable.",
    "topIssues": [{
        "title": "Assertion failures on save",
        "affectedTests": 2,
        "likelyCauses": ["race in persistence"],
        "suggestedFixes": ["await the save confirmation"],
    }],
    "recommendations": ["quarantine flaky tests"],
}


@pytest.fixture
def openai_mocked(monkeypatch):
    """Mock the OpenAI call and pretend a key is configured; count invocations."""
    calls = {"n": 0}

    async def fake_generate(payload):
        calls["n"] += 1
        return dict(FAKE_REPORT)

    monkeypatch.setattr(report_ai, "generate_report", fake_generate)
    monkeypatch.setattr(get_settings(), "openai_api_key", "test-key")
    return calls


async def test_manager_forbidden(client, manager_headers, openai_mocked):
    res = await client.post("/api/apps/hrms/layers/unit/report", headers=manager_headers)
    assert res.status_code == 403
    assert openai_mocked["n"] == 0


async def test_qa_generates_report(client, qa_headers, openai_mocked):
    res = await client.post("/api/apps/cellsens/layers/unit/report", headers=qa_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"] == FAKE_REPORT["summary"]
    assert body["topIssues"][0]["suggestedFixes"] == ["await the save confirmation"]
    assert body["cached"] is False
    assert body["generatedAt"] and body["model"]
    assert openai_mocked["n"] == 1


async def test_cache_hit_and_force(client, admin_headers, openai_mocked):
    first = await client.post("/api/apps/hrms/layers/integration/report", headers=admin_headers)
    assert first.status_code == 200 and first.json()["cached"] is False
    assert openai_mocked["n"] == 1

    # same data -> served from Mongo, OpenAI NOT called again
    second = await client.post("/api/apps/hrms/layers/integration/report", headers=admin_headers)
    assert second.status_code == 200 and second.json()["cached"] is True
    assert openai_mocked["n"] == 1

    forced = await client.post("/api/apps/hrms/layers/integration/report?force=true", headers=admin_headers)
    assert forced.status_code == 200 and forced.json()["cached"] is False
    assert openai_mocked["n"] == 2


async def test_503_without_key(client, qa_headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "openai_api_key", "")
    res = await client.post("/api/apps/preciv/layers/unit/report", headers=qa_headers)
    assert res.status_code == 503
    assert "not configured" in res.json()["detail"]


async def test_404_unknown_layer(client, qa_headers, openai_mocked):
    res = await client.post("/api/apps/hrms/layers/nope/report", headers=qa_headers)
    assert res.status_code in (404, 422)  # 422: Literal path param rejects it earlier
    assert openai_mocked["n"] == 0


def test_build_payload_groups_and_truncates():
    dashboard = {
        "history": [{"daysAgo": d, "passRate": 90.0 + (d % 5), "runs": 3} for d in range(89, -1, -1)],
        "snapshot": {"total": 10, "passed": 7, "failed": 3, "knownBugs": 1,
                     "running": 0, "skipped": 0, "passRate": 92.1, "deltaVsLastWeek": 0.5},
        "version": {"current": "4.5.2", "lastUpdated": "Aug 1, 2026"},
    }
    long_trace = "\n".join(f"line {i}" for i in range(20))
    failed = [
        {"name": f"test_a_{i}", "suite": "Smoke",
         "history": [{"status": "Passed"}, {"status": "Failed"}],
         "failure": {"errorMessage": "TimeoutError: too slow", "failingStep": "Step 3",
                     "stackTrace": long_trace}}
        for i in range(5)
    ] + [
        {"name": "test_b", "suite": "Sanity", "history": [{"status": "Failed"}],
         "failure": {"errorMessage": "ValueError: off by 0.3", "failingStep": "Step 5",
                     "stackTrace": "short"}},
    ]

    payload = build_payload("hrms", "unit", dashboard, failed)

    groups = payload["failureGroups"]
    assert [g["exception"] for g in groups] == ["TimeoutError", "ValueError"]  # sorted by count
    assert groups[0]["count"] == 5
    assert len(groups[0]["sampleTests"]) == 3  # capped
    assert "lines omitted" in groups[0]["sampleStackTrace"]
    assert groups[1]["sampleStackTrace"] == "short"
    assert payload["flakyFailedTests"] == 5  # the 5 with mixed Passed/Failed history
    assert payload["trend"]["currentPassRate"] == dashboard["history"][-1]["passRate"]


def test_truncate_trace_short_untouched():
    assert _truncate_trace("a\nb\nc") == "a\nb\nc"
