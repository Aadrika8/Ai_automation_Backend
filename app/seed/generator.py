"""Deterministic demo-data generation — Python port of the frontend's mock
fixtures (Frontend/src/api/mock/fixtures.ts invariants). Pure functions:
no I/O, so tests can reuse them. Seeded with random.Random per app:layer,
so every run produces identical data."""
import random
from datetime import datetime, timedelta

APPS = [
    {
        "id": "hrms", "name": "HRMS", "tag": "HR Management Suite", "icon": "people", "total": 1840,
        "desc": "Payroll, attendance and employee lifecycle platform used across all business units.",
    },
    {
        "id": "cellsens", "name": "cellSens", "tag": "Imaging & Microscopy", "icon": "scope", "total": 2620,
        "desc": "Life-science imaging software for microscope control, acquisition and analysis.",
    },
    {
        "id": "preciv", "name": "PRECiV", "tag": "Industrial Measurement", "icon": "ruler", "total": 1210,
        "desc": "Precision measurement and inspection software for industrial quality control.",
    },
]

LAYERS = [
    {"id": "unit", "name": "Unit Testing", "short": "Unit", "share": 0.55, "colorVar": "--tier-unit",
     "desc": "Fast, isolated checks of individual components"},
    {"id": "integration", "name": "Integration Testing", "short": "Integration", "share": 0.25,
     "colorVar": "--tier-integration", "desc": "Modules verified working together"},
    {"id": "system", "name": "System Testing", "short": "System", "share": 0.13, "colorVar": "--tier-system",
     "desc": "Complete builds validated end-to-end internally"},
    {"id": "e2e", "name": "UI / End-to-End Testing", "short": "UI / E2E", "share": 0.07,
     "colorVar": "--tier-e2e", "desc": "Real user journeys through the interface"},
]

RUN_NAMES = {
    "unit": ["CI · push build", "CI · merge queue", "Nightly unit sweep", "Pre-release gate"],
    "integration": ["Service contract run", "Nightly integration", "API compatibility run", "Data-flow verification"],
    "system": ["Full build validation", "Release candidate run", "Environment matrix run", "Upgrade-path validation"],
    "e2e": ["User journey suite", "Smoke on staging", "Release sign-off run", "Cross-platform UI run"],
}

SUITES = ["Regression", "Smoke", "Sanity", "Integration"]
RELEASES = {"hrms": ["4.5", "4.3"], "cellsens": ["4.5", "4.3"], "preciv": ["2.1", "2.0"]}

VERBS = ["login", "export", "capture", "measure", "sync", "upload", "render", "search", "filter", "validate",
         "import", "calibrate", "stitch", "annotate", "report", "archive", "zoom", "crop", "align", "batch"]
NOUNS = ["flow", "dialog", "session", "profile", "image", "dataset", "project", "settings", "permissions",
         "report", "layout", "metadata", "preview", "workspace", "template", "queue", "history", "snapshot",
         "overlay", "wizard"]

ERROR_TEMPLATES = [
    {"msg": "AssertionError: expected status 'saved' but got 'pending' in {t}",
     "step": "Step 4 — Verify persisted state after submit"},
    {"msg": "TimeoutError: element '#confirm-dialog .ok-button' not visible after 30s in {t}",
     "step": "Step 3 — Confirm dialog interaction"},
    {"msg": "ElementNotFoundError: locator 'toolbar > Export' matched 0 nodes in {t}",
     "step": "Step 2 — Open export toolbar action"},
    {"msg": "ValueError: measured 41.7 µm, expected 42.0 µm ± 0.1 in {t}",
     "step": "Step 5 — Compare measurement to reference"},
]

RUN_WHEN = ["38 min ago", "3 h ago", "Yesterday", "2 days ago", "4 days ago", "5 days ago"]
LAST_RUN_WHEN = ["38 min ago", "3 h ago", "6 h ago", "Yesterday", "2 days ago"]
HISTORY_WHEN = ["38 min ago", "Yesterday", "2 days ago", "3 days ago", "5 days ago", "6 days ago", "8 days ago"]

USERS = [
    {"username": "admin", "password": "admin123", "name": "Aadrika Sharma", "role": "admin", "lastActive": "Now"},
    {"username": "qa", "password": "qa123", "name": "Rahul Verma", "role": "qa", "lastActive": "2 h ago"},
    {"username": "manager", "password": "manager123", "name": "Priya Nair", "role": "manager", "lastActive": "Yesterday"},
    {"username": "qa2", "password": None, "name": "Meera Iyer", "role": "qa", "lastActive": "3 days ago"},
    {"username": "manager2", "password": None, "name": "Vikram Rao", "role": "manager", "lastActive": "5 days ago"},
]

DEFAULT_SETTINGS = {
    "repoUrl": "https://github.com/Aadrika8/test_suites.git",
    "branch": "main",
    "cacheDir": "~/TestRunner/cache",
    "timeoutSeconds": 60,
    "rootFolder": "test_suites",
    "levels": ["application", "suite", "release"],
    "extensions": [".py"],
}


def _date_label(days_ago: int) -> str:
    d = datetime.now() - timedelta(days=days_ago)
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def build_layer_model(app: dict, layer: dict) -> dict:
    """One app:layer — snapshot, 90-day history, 24h hourly, version, 6 runs."""
    r = random.Random(f"{app['id']}:{layer['id']}")
    total = round(app["total"] * layer["share"])

    history = []
    pr = 88 + r.random() * 8
    for days_ago in range(89, -1, -1):
        pr = min(99.6, max(78.0, pr + (r.random() - 0.48) * 1.6))
        history.append({"daysAgo": days_ago, "passRate": round(pr, 1), "runs": 2 + int(r.random() * 7)})

    cur = history[-1]["passRate"]
    week_ago = history[-8]["passRate"]
    running = max(1, round(total * 0.012 * r.random() * 2))
    skipped = round(total * 0.02 * r.random())
    passed = round((total - running - skipped) * cur / 100)
    failed = total - passed - running - skipped
    known_bugs = min(failed, round(failed * (0.25 + r.random() * 0.35)))

    hourly: list[dict] = []
    hpr = cur
    for hours_ago in range(24):
        hourly.insert(0, {"hoursAgo": hours_ago, "passRate": round(hpr, 1), "runs": int(r.random() * 3)})
        hpr = min(99.6, max(78.0, hpr + (r.random() - 0.5) * 0.8))

    version = {
        "current": f"{RELEASES[app['id']][0]}.{1 + int(r.random() * 8)}",
        "lastUpdated": _date_label(1 + int(r.random() * 13)),
    }

    runs = []
    for i, when in enumerate(RUN_WHEN):
        st = r.random()
        status = "Completed" if st < 0.68 else "Running" if st < 0.85 else "Failed"
        run_total = round(total * (0.25 + r.random() * 0.75))
        run_passed = (
            round(run_total * (0.62 + r.random() * 0.25))
            if status == "Failed"
            else round(run_total * (0.9 + r.random() * 0.099))
        )
        runs.append({
            "id": f"{app['id']}:{layer['id']}:run{i}",
            "appId": app["id"],
            "layerId": layer["id"],
            "name": f"{RUN_NAMES[layer['id']][i % 4]} #{310 - i * 7 - int(r.random() * 5)}",
            "when": when,
            "whenOrder": i,
            "total": run_total,
            "passed": min(run_passed, run_total),
            "durationMin": round(8 + r.random() * 70),
            "status": status,
        })

    snapshot = {
        "total": total, "passed": passed, "failed": failed, "knownBugs": known_bugs,
        "running": running, "skipped": skipped, "passRate": cur,
        "deltaVsLastWeek": round(cur - week_ago, 1),
    }
    return {"snapshot": snapshot, "history": history, "hourly": hourly, "version": version, "runs": runs}


def build_tests(app: dict, layer: dict, snapshot: dict) -> list[dict]:
    """Full test-case docs; statuses exactly match the snapshot counts."""
    r = random.Random(f"{app['id']}:{layer['id']}:tests")
    statuses = (
        ["Failed"] * snapshot["failed"]
        + ["Running"] * snapshot["running"]
        + ["Skipped"] * snapshot["skipped"]
        + ["Passed"] * snapshot["passed"]
    )
    r.shuffle(statuses)
    duration_cap = {"unit": 3, "e2e": 90}.get(layer["id"], 25)

    docs = []
    for i, status in enumerate(statuses):
        name = f"test_{r.choice(VERBS)}_{r.choice(NOUNS)}_{i + 1:03d}"
        suite = r.choice(SUITES)
        release = r.choice(RELEASES[app["id"]])
        duration = round(0.2 + r.random() * duration_cap, 1)
        path = f"test_suites/{app['name']}/{suite}/{release}/{name}.py"

        history = []
        for j, when in enumerate(HISTORY_WHEN):
            failed_now = status == "Failed" and j == 0
            flaky = r.random() < 0.12
            history.append({
                "when": when,
                "status": "Failed" if (failed_now or flaky) else "Passed",
                "durationS": round(duration * (0.85 + r.random() * 0.3), 1),
            })

        doc = {
            "_id": f"{app['id']}~{layer['id']}~{i}",
            "appId": app["id"], "layerId": layer["id"],
            "name": name, "suite": suite, "release": release, "status": status,
            "durationS": duration, "lastRun": r.choice(LAST_RUN_WHEN),
            "path": path, "history": history,
        }
        if status == "Failed":
            template = ERROR_TEMPLATES[r.randrange(len(ERROR_TEMPLATES))]
            message = template["msg"].format(t=name)
            doc["failure"] = {
                "errorMessage": message,
                "failingStep": template["step"],
                "stackTrace": "\n".join([
                    "Traceback (most recent call last):",
                    f'  File "{path}", line {40 + (i * 37) % 180}, in {name.rsplit("_", 1)[0]}',
                    "    result = page.submit_and_wait(payload)",
                    f'  File "framework/{layer["id"]}/driver.py", line {120 + (i * 53) % 300}, in submit_and_wait',
                    "    return self._await_state(target, timeout=self.timeout)",
                    '  File "framework/core/waiter.py", line 88, in _await_state',
                    "    raise self._error_for(state)",
                    message,
                ]),
            }
        docs.append(doc)
    return docs


def generate() -> dict:
    """All collections' documents. Self-checks the count invariants."""
    apps_docs, layers_docs, tests_docs, runs_docs = [], [], [], []
    for app in APPS:
        total_sum = passed_sum = 0
        for order, layer in enumerate(LAYERS):
            model = build_layer_model(app, layer)
            snap = model["snapshot"]
            total_sum += snap["total"]
            passed_sum += snap["passed"]

            recent_runs = [{k: v for k, v in run.items() if k != "whenOrder"} for run in model["runs"]]
            layers_docs.append({
                "_id": f"{app['id']}:{layer['id']}",
                "appId": app["id"], "layerId": layer["id"], "order": order,
                "name": layer["name"], "short": layer["short"], "share": layer["share"],
                "desc": layer["desc"], "colorVar": layer["colorVar"],
                "testCount": snap["total"], "passRate": snap["passRate"],
                "snapshot": snap, "history": model["history"], "hourly": model["hourly"],
                "version": model["version"], "recentRuns": recent_runs,
            })
            runs_docs.extend(model["runs"])

            tests = build_tests(app, layer, snap)
            assert len(tests) == snap["total"], f"test count mismatch for {app['id']}:{layer['id']}"
            tests_docs.extend(tests)

        apps_docs.append({
            "_id": app["id"], "name": app["name"], "tag": app["tag"], "desc": app["desc"],
            "icon": app["icon"], "totalTests": total_sum,
            "passRate": round(100 * passed_sum / total_sum, 1),
        })
    return {"apps": apps_docs, "layers": layers_docs, "tests": tests_docs, "runs": runs_docs}
