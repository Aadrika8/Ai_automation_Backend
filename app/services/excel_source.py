
import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePath

EXCEL_SUFFIXES = {".xlsx", ".xlsm"}
# Excel writes lock files alongside open workbooks; they are not data.
TEMP_PREFIXES = ("~$", ".~")
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 200
MAX_DEPTH = 2  # <release>/file.xlsx and <release>/<layer>/file.xlsx


class SourceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class SourceFile:
    """One workbook found under an application's folder."""
    name: str  # "feature.xlsx"
    relative_path: str  # "feature.xlsx" or "unit/part-a.xlsx", POSIX separators
    absolute_path: str
    size_bytes: int
    modified_at: datetime
    fingerprint: str  # sha256 of the bytes — drives change detection
    layer_id: str | None = None  # None when the name matches no layer
    error: str | None = None  # unreadable/oversized; the file is still listed


def _now_utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def _layer_aliases(layer: dict) -> set[str]:
    """Names a workbook might plausibly use for this layer.

    "regression.xlsx" matches the layer `regression`; so do "Regression.xlsx",
    "regression-testing.xlsx" and "regression tests.xlsx", because the trailing
    test/tests/testing is stripped from both sides before comparing.
    """
    def strip_suffix(slug: str) -> str:
        return re.sub(r"-(test|tests|testing)$", "", slug)

    out: set[str] = set()
    for raw in (layer.get("id"), layer.get("name"), layer.get("short")):
        if not raw:
            continue
        slug = slugify(raw)
        if slug:
            out.add(slug)
            out.add(strip_suffix(slug))
    return {s for s in out if s}


def match_layer(stem: str, folder: str | None, layers: list[dict]) -> str | None:
    """Map a workbook to a layer id by file name, then by its parent folder."""
    def strip_suffix(slug: str) -> str:
        return re.sub(r"-(test|tests|testing)$", "", slug)

    lookup: dict[str, str] = {}
    for layer in layers:
        for alias in _layer_aliases(layer):
            lookup.setdefault(alias, layer["id"])

    for candidate in (stem, folder):
        if not candidate:
            continue
        slug = slugify(candidate)
        hit = lookup.get(slug) or lookup.get(strip_suffix(slug))
        if hit:
            return hit
    return None


# --- path validation ----------------------------------------------------


def resolve_root(root: str) -> Path:
    """Validate the configured parent folder. Works for local paths and UNC
    shares (\\\\server\\share\\...) alike — both are just paths to the OS."""
    if not (root or "").strip():
        raise SourceError(
            "root_not_configured",
            "No Excel root folder is configured. An admin can set it in Settings.")
    path = Path(root.strip()).expanduser()
    try:
        resolved = path.resolve()
    except OSError:
        raise SourceError("root_invalid", f"The Excel root folder path is not valid: {root}")
    if not resolved.exists():
        raise SourceError(
            "root_missing",
            f"The Excel root folder does not exist or is not reachable: {resolved}")
    if not resolved.is_dir():
        raise SourceError("root_not_a_directory", f"The Excel root path is not a folder: {resolved}")
    if not os.access(resolved, os.R_OK | os.X_OK):
        raise SourceError(
            "root_permission_denied",
            f"The server does not have permission to read the Excel root folder: {resolved}")
    return resolved


# Characters Windows forbids in a file name, plus the separators we handle
# ourselves. Applied when deriving a folder name from an application or
# release name, so "4.5.1" stays "4.5.1" but "cellSens: R&D" cannot escape.
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def folder_name(text: str) -> str:
    """The folder a person would create for this application or release."""
    name = _ILLEGAL.sub("-", str(text)).strip().rstrip(".")
    return name or "unnamed"


def default_folder(app: dict, release: dict) -> str:
    """Where a release's workbooks live unless it says otherwise."""
    return f'{folder_name(app.get("name") or app.get("_id", ""))}/' \
           f'{folder_name(release.get("name") or release.get("releaseId", ""))}'


def release_folder_path(app: dict, release: dict) -> str:
    """The relative path for a release: its override, else the default."""
    return (release.get("excelPath") or "").strip() or default_folder(app, release)


def resolve_release_folder(root: str, relative_path: str) -> Path:
    """Resolve one release's folder beneath the configured root.

    The path is confined to the root: an absolute path, or one climbing out
    with `..`, is rejected rather than followed.
    """
    root_dir = resolve_root(root)
    rel = (relative_path or "").strip().replace("\\", "/").strip("/")
    if not rel:
        raise SourceError(
            "folder_not_configured",
            "This release has no Excel folder. An admin can set one on the "
            "application's layers page.")
    if PurePath(rel).is_absolute() or ".." in PurePath(rel).parts:
        raise SourceError(
            "folder_invalid",
            "The release's Excel folder must be a relative path inside the root folder.")

    target = (root_dir / rel).resolve()
    if target != root_dir and root_dir not in target.parents:
        raise SourceError(
            "folder_escapes_root",
            "The release's Excel folder resolves outside the configured root folder.")
    if not target.exists():
        raise SourceError(
            "folder_missing",
            f"No folder for this release at {target}. Create it, or point the "
            f"release at the folder that holds its workbooks.")
    if not target.is_dir():
        raise SourceError(
            "folder_not_a_directory",
            f"The release's Excel path is a file, not a folder: {target}")
    if not os.access(target, os.R_OK | os.X_OK):
        raise SourceError(
            "folder_permission_denied",
            f"The server does not have permission to read: {target}")
    return target


def read_workbook_bytes(path: Path) -> bytes:
    """Read one workbook, turning every filesystem failure into a clear message."""
    if path.suffix.lower() not in EXCEL_SUFFIXES:
        raise SourceError(
            "unsupported_format",
            f"{path.name} is not a supported Excel file (.xlsx or .xlsm).")
    try:
        size = path.stat().st_size
    except OSError:
        raise SourceError("file_missing", f"{path.name} is no longer available at {path}.")
    if size > MAX_FILE_BYTES:
        raise SourceError(
            "file_too_large",
            f"{path.name} is larger than the {MAX_FILE_BYTES // (1024 * 1024)} MB limit.")
    try:
        return path.read_bytes()
    except PermissionError:
        raise SourceError(
            "file_permission_denied",
            f"The server does not have permission to read {path.name}.")
    except OSError as e:
        raise SourceError(
            "file_unreadable",
            f"{path.name} could not be read ({e.strerror or 'I/O error'}).")


# --- discovery ----------------------------------------------------------


def fingerprint_bytes(data: bytes) -> str:
    """Fingerprint of content already in hand — what ingestion records."""
    return hashlib.sha256(data).hexdigest()


def detect_rename(known: dict[str, str], present: set[str], content_hash: str) -> str:
    """The workbook this one used to be called, or "" when it is genuinely new.

    A renamed workbook would otherwise fork: the new name starts its own series
    at #1 while the old one lives on, and because a layer's record count sums
    across its files, a rename doubles it — which then feeds the pyramid check.

    Two signals separate a rename from a copy, and both are already in hand
    when a load decides what to do with a file:

        the old name is gone from the folder   and   the content is identical

    A copy leaves the original in place, so it fails the first test and stays
    two datasets. Anything whose content differs at all fails the second.

    `known` maps each of the layer's files to the content hash of its current
    snapshot; `present` is every path the folder scan just found.

    Two deliberate refusals. More than one absent file with this hash is
    ambiguous — nothing here can say which one was renamed, so it declines and
    the load proceeds as a new file. And a workbook renamed *and* edited in one
    step has a different hash, so it reads as new; recovering that would mean
    guessing at similarity, which is worse than starting a fresh series.
    """
    if not content_hash:
        return ""
    candidates = [file for file, digest in known.items()
                  if file not in present and digest == content_hash]
    return candidates[0] if len(candidates) == 1 else ""


def fingerprint_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_release_folder(root: str, relative_path: str,
                        layers: list[dict]) -> list[SourceFile]:
    """List the workbooks belonging to one release, matched to its layers.

    Only that release's folder is read, so releases never see each other's
    workbooks. Files that cannot be read are still returned, carrying `error`,
    so the UI can show what is wrong with them instead of pretending they are
    absent.
    """
    folder = resolve_release_folder(root, relative_path)
    found: list[SourceFile] = []

    for path in sorted(folder.rglob("*")):
        if len(path.relative_to(folder).parts) > MAX_DEPTH or not path.is_file():
            continue
        if path.name.startswith(TEMP_PREFIXES) or path.name.startswith("."):
            continue
        if path.suffix.lower() not in EXCEL_SUFFIXES:
            continue

        rel = path.relative_to(folder).as_posix()
        parent = path.parent.name if path.parent != folder else None
        try:
            stat = path.stat()
            fingerprint = fingerprint_file(path)
            error = None
            if stat.st_size > MAX_FILE_BYTES:
                error = f"Larger than the {MAX_FILE_BYTES // (1024 * 1024)} MB limit."
        except PermissionError:
            stat, fingerprint = None, ""
            error = "The server does not have permission to read this file."
        except OSError:
            stat, fingerprint = None, ""
            error = "This file could not be read."

        found.append(SourceFile(
            name=path.name,
            relative_path=rel,
            absolute_path=str(path),
            size_bytes=stat.st_size if stat else 0,
            modified_at=_now_utc(stat.st_mtime) if stat else _now_utc(0),
            fingerprint=fingerprint,
            layer_id=match_layer(path.stem, parent, layers),
            error=error,
        ))
        if len(found) >= MAX_FILES:
            break

    if not found:
        raise SourceError(
            "no_excel_files",
            f"No Excel workbooks (.xlsx or .xlsm) were found in {folder}. "
            "Each release reads only its own folder.")
    return found
