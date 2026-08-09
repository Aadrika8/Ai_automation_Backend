"""AI report generation: compress dashboard data into a small payload, one OpenAI call.

Cost control lives here — the model never sees raw test rows or full stack
traces, only aggregates and one truncated trace per failure group.
"""
import json
import logging

from openai import AsyncOpenAI

from app.config import get_settings

log = logging.getLogger("qi.reports")

SYSTEM_PROMPT = (
    "You are a senior QA analyst writing a quality report for the Evident engineering team. "
    "You receive aggregated test-suite data as JSON. Respond ONLY with a JSON object with keys: "
    '"summary" (2-3 sentence executive summary for non-technical readers), '
    '"healthAssessment" (one paragraph judging suite health from pass rate, trend and failure counts), '
    '"topIssues" (array, one entry per failure group, each with "title" (short), "affectedTests" (number), '
    '"likelyCauses" (2-4 short strings) and "suggestedFixes" (2-4 concrete, actionable strings)), '
    '"recommendations" (3-5 short strings for the wider team). '
    "Base causes and fixes on the exception types, failing steps and stack traces provided. "
    "Be specific to the data, never generic."
)


def _exception_type(error_message: str) -> str:
    head = error_message.split(":", 1)[0].strip()
    return head or "UnknownError"


def _truncate_trace(trace: str, head: int = 4, tail: int = 3) -> str:
    lines = trace.splitlines()
    if len(lines) <= head + tail:
        return trace
    omitted = len(lines) - head - tail
    return "\n".join([*lines[:head], f"... ({omitted} lines omitted) ...", *lines[-tail:]])


def build_payload(app_id: str, layer_id: str, dashboard: dict, failed_tests: list[dict]) -> dict:
    """Aggregate the layer dashboard + failed tests into a compact, stable dict.

    Stable content for identical data — the router hashes the JSON to decide
    whether a cached report can be reused instead of spending credits.
    """
    history = dashboard["history"]
    month = history[-30:]
    trend = {
        "currentPassRate": history[-1]["passRate"],
        "deltaVsLastWeek": dashboard["snapshot"]["deltaVsLastWeek"],
        "min30d": min(p["passRate"] for p in month),
        "max30d": max(p["passRate"] for p in month),
    }

    groups: dict[str, dict] = {}
    flaky = 0
    for t in failed_tests:
        hist = t.get("history") or []
        if any(h["status"] == "Passed" for h in hist) and any(h["status"] == "Failed" for h in hist):
            flaky += 1
        failure = t.get("failure") or {}
        exc = _exception_type(failure.get("errorMessage", ""))
        g = groups.setdefault(exc, {
            "exception": exc,
            "count": 0,
            "sampleTests": [],
            "suites": set(),
            "failingStep": failure.get("failingStep", ""),
            "sampleStackTrace": "",
        })
        g["count"] += 1
        if len(g["sampleTests"]) < 3:
            g["sampleTests"].append(t["name"])
        if t.get("suite"):
            g["suites"].add(t["suite"])
        if not g["sampleStackTrace"] and failure.get("stackTrace"):
            g["sampleStackTrace"] = _truncate_trace(failure["stackTrace"])

    failure_groups = sorted(groups.values(), key=lambda g: -g["count"])
    for g in failure_groups:
        g["suites"] = sorted(g["suites"])

    return {
        "application": app_id,
        "layer": layer_id,
        "version": dashboard["version"],
        "snapshot": dashboard["snapshot"],
        "trend": trend,
        "flakyFailedTests": flaky,
        "failureGroups": failure_groups,
    }


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


async def generate_report(payload: dict) -> dict:
    """One chat completion; returns the report dict in wire (camelCase) shape."""
    s = get_settings()
    client = AsyncOpenAI(api_key=s.openai_api_key)
    res = await client.chat.completions.create(
        model=s.openai_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ],
        response_format={"type": "json_object"},
        max_tokens=1200,
        temperature=0.2,
    )
    if res.usage:
        log.info(
            "OpenAI usage for %s/%s: prompt=%d completion=%d tokens",
            payload["application"], payload["layer"],
            res.usage.prompt_tokens, res.usage.completion_tokens,
        )
    data = json.loads(res.choices[0].message.content or "{}")
    # Normalize defensively — a malformed model reply becomes empty fields, not a 500.
    return {
        "summary": str(data.get("summary", "")),
        "healthAssessment": str(data.get("healthAssessment", "")),
        "topIssues": [
            {
                "title": str(i.get("title", "")),
                "affectedTests": _int(i.get("affectedTests")),
                "likelyCauses": [str(c) for c in i.get("likelyCauses") or []],
                "suggestedFixes": [str(f) for f in i.get("suggestedFixes") or []],
            }
            for i in data.get("topIssues") or [] if isinstance(i, dict)
        ],
        "recommendations": [str(r) for r in data.get("recommendations") or []],
    }
