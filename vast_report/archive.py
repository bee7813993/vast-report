from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import re
import tarfile
import tempfile
from pathlib import Path
from typing import Iterator


ARCHIVE_RE = re.compile(r"^vast-daily-(\d{4}-\d{2}-\d{2})\.txz$")
EXPECTED_MARKERS = {
    "machine-status-last24h.tsv",
    "gpu-market-summary.tsv",
    "earnings-last24h-summary.tsv",
}


def find_latest_archive(archive_dir: Path) -> Path:
    archives = sorted(archive_dir.glob("vast-daily-*.txz"))
    if not archives:
        raise FileNotFoundError(f"No archive found under {archive_dir}")
    return archives[-1]


def archive_date_from_name(path: Path) -> str:
    match = ARCHIVE_RE.match(path.name)
    if match:
        return match.group(1)
    return dt.date.today().isoformat()


def _is_within_directory(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_member(tmp_path: Path, member: tarfile.TarInfo) -> None:
    target = (tmp_path / member.name).resolve()
    if not _is_within_directory(tmp_path.resolve(), target):
        raise ValueError(f"Unsafe archive member path: {member.name}")
    if member.issym() or member.islnk():
        raise ValueError(f"Archive links are not allowed: {member.name}")
    if not (member.isfile() or member.isdir()):
        raise ValueError(f"Unsupported archive member type: {member.name}")


def _detect_data_dir(tmp_path: Path) -> tuple[Path, list[str]]:
    warnings: list[str] = []
    if any((tmp_path / name).exists() for name in EXPECTED_MARKERS):
        return tmp_path, warnings

    candidates: list[Path] = []
    for child in sorted(tmp_path.iterdir()):
        if not child.is_dir():
            continue
        if any((child / name).exists() for name in EXPECTED_MARKERS):
            candidates.append(child)

    if candidates:
        if len(candidates) > 1:
            warnings.append(
                "Multiple data directories found in archive; using "
                f"{candidates[0].name}"
            )
        return candidates[0], warnings

    dirs = [p for p in sorted(tmp_path.iterdir()) if p.is_dir()]
    if len(dirs) == 1:
        warnings.append(
            "Archive did not contain expected marker files; using the only "
            f"directory {dirs[0].name}"
        )
        return dirs[0], warnings

    warnings.append("Archive root did not contain expected marker files")
    return tmp_path, warnings


@contextmanager
def extracted_archive(archive_path: Path) -> Iterator[tuple[Path, list[str]]]:
    with tempfile.TemporaryDirectory(prefix="vast-report-") as tmp_name:
        tmp_path = Path(tmp_name)
        with tarfile.open(archive_path, "r:xz") as tar:
            members = tar.getmembers()
            for member in members:
                _validate_member(tmp_path, member)
            tar.extractall(tmp_path, members=members)

        data_dir, warnings = _detect_data_dir(tmp_path)
        yield data_dir, warnings
