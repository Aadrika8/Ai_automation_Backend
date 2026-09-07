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
    release_count: int = 0
    record_count: int = 0
    # the release the UI opens by default
    current_release: str = ""
    current_release_id: str = ""


class AppCreate(CamelModel):
    name: str = Field(min_length=1, max_length=80)
    tag: str = ""
    desc: str = ""
    icon: str = "scope"
    # an application is useless without somewhere to put data, so it opens
    # with one release carrying the default pyramid
    release_name: str = Field(default="Initial release", min_length=1, max_length=80)


class AppUpdate(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    tag: str | None = None
    desc: str | None = None
    icon: str | None = None


class ReleaseInfo(CamelModel):
    id: str
    name: str
    desc: str = ""
    # Override for this release's folder, relative to settings.excelRoot.
    # Blank — the normal case — means "<application name>/<release name>".
    excel_path: str = ""
    # the release the UI opens by default — exactly one per application
    current: bool = False
    order: int = 0
    layer_count: int = 0
    record_count: int = 0
    created_at: datetime | None = None
    last_upload_at: datetime | None = None


class ReleaseCreate(CamelModel):
    name: str = Field(min_length=1, max_length=80)
    desc: str = ""
    excel_path: str = Field(default="", max_length=400)
    # copy another release's layer structure — never its data or columns.
    # Omitted: start from the global default pyramid.
    copy_layers_from: str | None = None
    make_current: bool = True


class ReleaseUpdate(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    desc: str | None = None
    excel_path: str | None = Field(default=None, max_length=400)
    # true promotes this release to the current one (demoting the previous)
    current: bool | None = None


class SnapshotPeriod(CamelModel):
    """The month a snapshot describes. Chosen by the person loading it, not
    inferred from the clock, and correctable afterwards."""
    year: int = Field(ge=2000, le=2999)
    month: int = Field(ge=1, le=12)


class LayerInfo(CamelModel):
    id: str
    name: str
    short: str = ""
    desc: str = ""
    order: int = 0
    # Rows held right now, summed across this type's files. Summed only so the
    # testing pyramid has a figure to compare — no combined dataset exists.
    record_count: int = 0
    file_count: int = 0
    snapshot_count: int = 0
    latest_snapshot_at: datetime | None = None
    latest_period: SnapshotPeriod | None = None
    latest_sources: list[str] = []


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


class SnapshotSource(CamelModel):
    """One workbook that went into a snapshot, kept so the load can be traced
    back to the files it came from."""
    relative_path: str
    file_name: str
    file_hash: str = ""
    size_bytes: int = 0
    modified_at: datetime | None = None
    rows_read: int = 0
    duplicates_skipped: int = 0


class SnapshotDiff(CamelModel):
    """How a snapshot differs from the one before it.

    `comparable` is false when the two were keyed differently — a changed
    sheet format makes row identity incomparable, and reporting every row as
    removed and re-added would be worse than reporting nothing.
    """
    comparable: bool = True
    compared_to: int | None = None  # sequence of the snapshot compared against
    added: int = 0
    changed: int = 0
    removed: int = 0
    reason: str | None = None


class LayerFileInfo(CamelModel):
    """One workbook feeding a testing type, with the state of its current
    snapshot. Files are listed side by side and never added together."""
    file: str  # path relative to the release folder — the dataset's identity
    file_name: str
    snapshot_id: str
    sequence: int = 0
    period: SnapshotPeriod | None = None
    loaded_at: datetime | None = None  # exact date and time of this load
    row_count: int = 0
    column_count: int = 0
    snapshot_count: int = 0
    combined: bool = False  # a pre-split load, kept as history


class SnapshotInfo(CamelModel):
    id: str
    layer_id: str
    # the one workbook this snapshot was taken from
    file: str = ""
    sequence: int
    period: SnapshotPeriod
    row_count: int = 0
    total_rows: int = 0
    duplicates_skipped: int = 0
    columns: list[ColumnDef] = []
    sections: list[str] = []
    identity_keys: list[str] = []
    sources: list[SnapshotSource] = []
    diff: SnapshotDiff | None = None
    # true only for snapshots migrated from before files were kept separate
    combined: bool = False
    created_at: datetime
    created_by: str = ""
    # the newest snapshot of its testing type — what the app shows by default
    is_current: bool = False


class SnapshotUpdate(CamelModel):
    """Only the period is correctable; the data a snapshot holds is fixed."""
    period: SnapshotPeriod


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
    # the schema of the snapshot being read, which may differ from any other
    columns: list[ColumnDef]
    snapshot: SnapshotInfo | None = None
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
    # the one snapshot being read; null when the metrics are merged
    snapshot: SnapshotInfo | None = None
    # true when these figures add up several files' current snapshots. The
    # merge happens at read time — no combined records are stored.
    merged: bool = False
    merged_files: list[LayerFileInfo] = []
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
    # every workbook feeding this testing type; each becomes its own dataset,
    # so several of them is normal rather than something to resolve
    files: list[SourceFileInfo] = []
    changed: bool = False
    last_synced_at: datetime | None = None
    last_synced_file: str | None = None


class SourceStatus(CamelModel):
    root: str = ""
    # the one relative path this release reads, and whether it was configured
    # by hand or derived from the application and release names
    folder: str = ""
    custom_folder: bool = False
    resolved_path: str = ""
    release_id: str = ""
    release_name: str = ""
    ok: bool
    error_code: str | None = None
    error: str | None = None
    layers: list[LayerSource] = []
    # workbooks whose name matches no layer — surfaced, never silently dropped
    unmatched_files: list[SourceFileInfo] = []
    changed_count: int = 0


class SnapshotLayerChoice(CamelModel):
    layer_id: str
    # relative paths to load for this testing type; each becomes its own
    # snapshot, never one combined snapshot
    files: list[str] = []


class SnapshotRequest(CamelModel):
    # omitted -> the current month
    period: SnapshotPeriod | None = None
    # omitted -> every testing type with exactly one matched workbook
    layers: list[SnapshotLayerChoice] | None = None


class SnapshotFileResult(CamelModel):
    """The outcome for one workbook. A load reports one of these per file."""
    layer_id: str
    layer_name: str
    file: str
    file_name: str
    # false when the data was unchanged, or the load failed
    created: bool = False
    snapshot_id: str | None = None
    sequence: int | None = None
    row_count: int = 0
    total_rows: int = 0
    duplicates_skipped: int = 0
    diff: SnapshotDiff | None = None
    # why nothing was written, when nothing was
    reason: str | None = None
    error: str | None = None


class SnapshotRunResult(CamelModel):
    created_at: datetime
    release_id: str = ""
    period: SnapshotPeriod
    files: list[SnapshotFileResult] = []


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


# --- traceability: Feature <-> System coverage ---------------------------


class TraceKeyConfig(CamelModel):
    """Where one testing layer keeps the identifier the two sides share.

    Empty means the rule: the first column of whichever workbook the row came
    from. A column key is an admin's override for a sheet whose first column
    is unusable, and is ignored by any file that does not have it.
    """
    column: str = ""


class TraceFileRead(CamelModel):
    """Which column one workbook's identifier was actually read from."""
    file_name: str = ""
    column: str = ""
    column_label: str = ""


class TraceSideRead(CamelModel):
    """What reading the identifier column produced for one layer.

    The first column is the default, not a rule: real sheets open with a serial
    number and keep the identifier in the second. So the screen answers both
    questions — "did that column give us identifiers?", from real extracted
    values, and "which other column could it be?", from `columns`.
    """
    family: str = ""
    # every column these workbooks carry, for the identifier picker
    columns: list[ColumnDef] = []
    files: list[TraceFileRead] = []
    extracted: list[str] = []
    distinct_ids: int = 0
    matched_rows: int = 0
    total_rows: int = 0
    unresolved_rows: int = 0
    # share of ids with no alphabetic prefix — a first column of bare 1, 2, 3
    numeric_ratio: float = 0.0


class TraceConfig(CamelModel):
    """How Feature and System rows are matched, for one release.

    Per release, like the column definitions it depends on: a new release's
    workbooks may be shaped nothing like the last one's.
    """
    # false when nothing has been saved — the values are then what the
    # first-column rule produced, which is usable as it stands
    configured: bool = False
    feature_layer: str = "feature"
    system_layer: str = "system"
    # regex pulling ids out of a cell; empty means the built-in token
    pattern: str = ""
    layers: dict[str, TraceKeyConfig] = {}
    # what the rule in force actually read, per layer
    read: dict[str, TraceSideRead] = {}
    updated_at: datetime | None = None
    updated_by: str = ""


class TraceConfigUpdate(CamelModel):
    pattern: str = Field(default="", max_length=200)
    layers: dict[str, TraceKeyConfig] = {}


class CoverageSideRow(CamelModel):
    """One ingested row, as it appears on its side of the comparison."""
    section: str = ""
    data: dict[str, Cell] = {}
    file: str = ""
    file_name: str = ""
    snapshot_id: str = ""


class RelatedId(CamelModel):
    """A supporting identifier, and the cell it was read out of.

    The id alone does not say what it refers to: `CS-4312` means nothing until
    you can see it sitting in "CS-4312 - GPU: Support Blackwell Technology".
    The list stays scannable and the text is there when it is asked for.
    """
    id: str
    text: str = ""


class CoverageEntry(CamelModel):
    """One identifier, and what each side has to say about it."""
    # stable list key: the folded id, or a synthetic one for an unreadable row
    key: str
    # the id spelled as the sheet spells it
    id: str = ""
    status: Literal["covered", "missing_in_system", "missing_in_feature", "unresolved"]
    # the same id more than once on a side — a data-quality problem, reported
    # alongside the coverage verdict rather than replacing it
    duplicate: bool = False
    # occurrences on each side, counting a forward-filled group as one
    feature_count: int = 0
    system_count: int = 0
    feature: CoverageSideRow | None = None
    system: CoverageSideRow | None = None
    # ids of the supporting family the FEATURE row mentions — the CS ids.
    # Evidence shown beside the gap, never a key: an entry missing from
    # feature has no feature row to read them from, so this stays empty
    # rather than being guessed at from somewhere else.
    related_ids: list[RelatedId] = []
    # which side an unresolved row came from
    side: str = ""


class CoverageSummary(CamelModel):
    feature_total: int = 0
    system_total: int = 0
    covered: int = 0
    missing_in_system: int = 0
    missing_in_feature: int = 0
    duplicates: int = 0
    unresolved: int = 0
    # the prefix the key ids share ("FL"), discovered from the first column
    family: str = ""
    # the prefix the supporting ids share ("CS") — lets the screen name the
    # column honestly instead of hardcoding a value nothing here should know
    related_family: str = ""
    # Feature -> System: every feature planned has a system requirement
    forward_coverage_pct: float = 100.0
    # System -> Feature: every system scope item is represented at feature level
    backward_coverage_pct: float = 100.0


class CoverageLayerInfo(CamelModel):
    """Which data one side of the comparison was read from."""
    layer_id: str
    layer_name: str = ""
    row_count: int = 0
    files: list[str] = []
    snapshot_ids: list[str] = []
    latest_period: SnapshotPeriod | None = None
    missing: bool = False  # the layer exists but holds no data yet
    # the column each of this layer's workbooks was read for identifiers
    id_columns: list[TraceFileRead] = []
    family: str = ""


class CoverageWarning(CamelModel):
    """Something that would make these figures misleading at face value.

    Never stops the comparison. A report that refuses to run teaches nobody
    anything; one that runs and says why it looks odd can be acted on.
    """
    code: Literal["family_mismatch", "numeric_key_column"]
    side: str = ""
    message: str


class CoverageResponse(CamelModel):
    configured: bool = False
    # set when the comparison could not run — an unloaded layer, say, or a
    # saved column that this release's workbooks no longer have
    error_code: str | None = None
    error: str | None = None
    config: TraceConfig | None = None
    feature: CoverageLayerInfo | None = None
    system: CoverageLayerInfo | None = None
    summary: CoverageSummary = CoverageSummary()
    entries: list[CoverageEntry] = []
    warnings: list[CoverageWarning] = []


class TracePreview(CamelModel):
    """What a proposed pattern or column override would pull out of a layer.

    Shown before anything is saved. Overriding the rule blind, on sheets
    whose columns may only be called "Column 1", is how a coverage report
    ends up quietly measuring the wrong thing.
    """
    layer_id: str
    column: str = ""
    pattern: str = ""
    read: TraceSideRead = TraceSideRead()


# --- benchmark: automation coverage --------------------------------------


class BenchmarkSource(CamelModel):
    """One published study behind the reference. Provenance travels with the
    number: a figure whose publisher sells automation tooling reads differently
    from one that does not."""
    value: float
    label: str
    publisher: str = ""
    publisher_kind: str = ""
    sample: str = ""
    year: int = 0


class AutomationReference(CamelModel):
    """The industry side — curated, never computed, never merged with Evident's."""
    metric: str = ""
    scope: str = ""
    low: float = 0.0
    high: float = 0.0
    # the range is the spread of the sources below, not a published figure
    synthesised: bool = True
    self_reported: bool = True
    life_science_specific: bool = False
    sources: list[BenchmarkSource] = []
    caveats: list[str] = []


class AutomationLayer(CamelModel):
    """One testing layer's measured coverage, and how it was arrived at."""
    layer_id: str
    name: str = ""
    # false when the workbooks carry no automated count — not the same as zero
    measured: bool = False
    reason: str = ""
    automated: float = 0.0
    total: float = 0.0
    coverage_pct: float | None = None
    # "test_count" or "row_count" — which measure the share was taken over
    basis: str = ""
    basis_label: str = ""
    row_count: int = 0
    automated_column: str = ""
    automated_label: str = ""
    total_column: str = ""
    total_label: str = ""


class AutomationMeasurement(CamelModel):
    """Evident's side — counts added across layers, then divided."""
    measured: bool = False
    automated: float = 0.0
    total: float = 0.0
    coverage_pct: float | None = None
    basis: str = ""
    unmeasured_layers: list[str] = []
    layers: list[AutomationLayer] = []


class ReferenceDistance(CamelModel):
    """Where the measurement sits against the range — whole points only, since
    the reference is a spread of self-reported estimates."""
    position: Literal["below", "within", "above", "unmeasured"] = "unmeasured"
    points: int | None = None


class AutomationCoverageResponse(CamelModel):
    release_id: str = ""
    release_name: str = ""
    latest_period: SnapshotPeriod | None = None
    evident: AutomationMeasurement = AutomationMeasurement()
    reference: AutomationReference = AutomationReference()
    distance: ReferenceDistance = ReferenceDistance()


class AutomationTrendPoint(CamelModel):
    """One release's automation coverage, and its change from the last one."""
    release_id: str
    release_name: str = ""
    order: int = 0
    current: bool = False
    measured: bool = False
    automated: float = 0.0
    total: float = 0.0
    coverage_pct: float | None = None
    basis: str = ""
    unmeasured_layers: list[str] = []
    latest_period: SnapshotPeriod | None = None
    # change in percentage points from the previous *measured* release
    delta_pct: float | None = None
    compared_to: str = ""


class AutomationTrendResponse(CamelModel):
    """Release history beside the industry reference — the two stay apart, and
    the history is the comparison that actually holds: measured against
    measured, same definition and same counting rules."""
    app_id: str = ""
    # oldest release first, so the series reads left to right
    points: list[AutomationTrendPoint] = []
    reference: AutomationReference = AutomationReference()
    measured_releases: int = 0
