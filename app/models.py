"""Response/request models — field-for-field mirror of Frontend/src/api/types.ts.
Snake_case fields with camelCase aliases; FastAPI serializes by alias, so the
wire format matches the frontend exactly."""
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

AppId = Literal["hrms", "cellsens", "preciv"]
LayerId = Literal["unit", "integration", "system", "e2e"]
Role = Literal["manager", "qa", "admin"]
RunStatus = Literal["Completed", "Running", "Failed"]
TestStatus = Literal["Passed", "Failed", "Running", "Skipped"]
AppIcon = Literal["people", "scope", "ruler"]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class User(CamelModel):
    username: str
    name: str
    role: Role


class LoginRequest(CamelModel):
    username: str
    password: str


class LoginResponse(CamelModel):
    token: str
    user: User


class AppSummary(CamelModel):
    id: AppId
    name: str
    tag: str
    desc: str
    icon: AppIcon
    total_tests: int
    pass_rate: float


class LayerInfo(CamelModel):
    id: LayerId
    name: str
    short: str
    share: float
    desc: str
    color_var: str
    test_count: int
    pass_rate: float


class LayerSnapshot(CamelModel):
    total: int
    passed: int
    failed: int
    known_bugs: int
    running: int
    skipped: int
    pass_rate: float
    delta_vs_last_week: float


class HistoryPoint(CamelModel):
    days_ago: int
    pass_rate: float
    runs: int


class HourPoint(CamelModel):
    hours_ago: int
    pass_rate: float
    runs: int


class SuiteVersion(CamelModel):
    current: str
    last_updated: str


class RunSummary(CamelModel):
    id: str
    app_id: AppId
    layer_id: LayerId
    name: str
    when: str
    total: int
    passed: int
    duration_min: int
    status: RunStatus


class LayerDashboard(CamelModel):
    snapshot: LayerSnapshot
    history: list[HistoryPoint]
    hourly: list[HourPoint]
    version: SuiteVersion
    recent_runs: list[RunSummary]


class TestCaseRow(CamelModel):
    id: str
    name: str
    suite: str
    release: str
    status: TestStatus
    duration_s: float
    last_run: str


class TestRunRecord(CamelModel):
    when: str
    status: TestStatus
    duration_s: float


class TestFailureDetail(CamelModel):
    error_message: str
    failing_step: str
    stack_trace: str


class TestDetail(TestCaseRow):
    app_id: AppId
    layer_id: LayerId
    path: str
    history: list[TestRunRecord]
    failure: TestFailureDetail | None = None


class AppSettings(CamelModel):
    repo_url: str
    branch: str
    cache_dir: str
    timeout_seconds: int
    root_folder: str
    levels: list[str]
    extensions: list[str]


class ManagedUser(CamelModel):
    username: str
    name: str
    role: Role
    last_active: str
