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
    # folder holding this application's workbooks, relative to settings.excelRoot
    excel_path: str = ""
    layer_count: int = 0
    record_count: int = 0


class AppCreate(CamelModel):
    name: str = Field(min_length=1, max_length=80)
    tag: str = ""
    desc: str = ""
    icon: str = "scope"
    excel_path: str = Field(default="", max_length=400)


class AppUpdate(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    tag: str | None = None
    desc: str | None = None
    icon: str | None = None
    excel_path: str | None = Field(default=None, max_length=400)


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
    # Pyramid position, bottom-first: 0 puts the new layer at the base (most
    # test cases). Omitted/out-of-range values append it to the tip.
    order: int | None = Field(default=None, ge=0)


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


class SourceFileInfo(CamelModel):
    name: str
    relative_path: str
    size_bytes: int
    modified_at: datetime
    layer_id: str | None = None
    changed: bool = False
    error: str | None = None


class LayerSource(CamelModel):
    layer_id: str
    layer_name: str
    files: list[SourceFileInfo] = []
    # more than one workbook maps here: ask before merging or picking one
    conflict: bool = False
    changed: bool = False
    last_synced_at: datetime | None = None
    last_synced_file: str | None = None


class SourceStatus(CamelModel):
    root: str = ""
    relative_path: str = ""
    resolved_path: str = ""
    ok: bool
    error_code: str | None = None
    error: str | None = None
    layers: list[LayerSource] = []
    # workbooks whose name matches no layer — surfaced, never silently dropped
    unmatched_files: list[SourceFileInfo] = []
    changed_count: int = 0


class SyncLayerChoice(CamelModel):
    layer_id: str
    # relative paths chosen for this layer; several means "merge them"
    files: list[str] = []


class SyncRequest(CamelModel):
    mode: Literal["merge", "replace"] = "merge"
    # omitted -> every layer with exactly one matched workbook
    layers: list[SyncLayerChoice] | None = None


class SyncLayerResult(CamelModel):
    layer_id: str
    layer_name: str
    files: list[str] = []
    total_rows: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    duplicates_skipped: int = 0
    error: str | None = None


class SyncResult(CamelModel):
    synced_at: datetime
    mode: Literal["merge", "replace"]
    layers: list[SyncLayerResult] = []


class AppSettings(CamelModel):
    # parent folder holding one sub-folder per application
    excel_root: str = ""
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
