"""Response/request models — field-for-field mirror of Frontend/src/api/types.ts.
Snake_case fields with camelCase aliases; FastAPI serializes by alias, so the
wire format matches the frontend exactly."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

Role = Literal["manager", "qa", "admin"]

Cell = str | int | float | None


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


class ColumnDef(CamelModel):
    key: str  # sanitized: "test_count"
    label: str  # original: "Test Count"
    type: Literal["string", "number"]


class AppSummary(CamelModel):
    id: str
    name: str
    tag: str = ""
    desc: str = ""
    icon: str = "scope"
    layer_count: int = 0
    record_count: int = 0


class AppCreate(CamelModel):
    name: str = Field(min_length=1, max_length=80)
    tag: str = ""
    desc: str = ""
    icon: str = "scope"


class AppUpdate(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    tag: str | None = None
    desc: str | None = None
    icon: str | None = None


class LayerInfo(CamelModel):
    id: str
    name: str
    short: str = ""
    desc: str = ""
    order: int = 0
    record_count: int = 0
    last_upload_at: datetime | None = None
    last_upload_file: str | None = None


class LayerCreate(CamelModel):
    name: str = Field(min_length=1, max_length=80)
    short: str = ""
    desc: str = ""


class LayerUpdate(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    short: str | None = None
    desc: str | None = None
    order: int | None = None


class UploadResult(CamelModel):
    upload_id: str
    file_name: str
    columns: list[ColumnDef]
    sections: list[str]
    total_rows: int
    inserted: int
    updated: int
    unchanged: int
    duplicates_skipped: int
    uploaded_by: str
    uploaded_at: datetime


class RecordRow(CamelModel):
    section: str
    data: dict[str, Cell]


class SectionGroup(CamelModel):
    name: str
    row_count: int
    rows: list[RecordRow]


class LayerRecordsResponse(CamelModel):
    columns: list[ColumnDef]
    last_upload: UploadResult | None = None
    total: int
    page: int
    page_size: int
    sections: list[SectionGroup]


class SectionAggregate(CamelModel):
    section: str
    row_count: int
    sums: dict[str, float]


class TopRow(CamelModel):
    section: str
    label: str
    value: float


class LayerDashboardResponse(CamelModel):
    last_upload: UploadResult | None = None
    total_rows: int
    section_count: int
    numeric_columns: list[ColumnDef]
    totals: dict[str, float]
    by_section: list[SectionAggregate]
    top_rows: list[TopRow]


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
    last_active: datetime | None = None


class UserCreate(CamelModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=6, max_length=128)
    role: Role
