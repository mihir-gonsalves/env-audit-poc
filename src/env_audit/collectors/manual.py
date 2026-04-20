# src/env_audit/collectors/manual.py
"""
Manual binary collector for env-audit-poc.

Scans well-known directories for executable files that are not managed by
any package manager. Each binary becomes a ``PackageRecord`` with a
``BinaryRecord`` attached and a ``Confidence.MEDIUM`` attribution (heuristic,
not from a manifest).

Directories scanned by default:
  - ``/usr/local/bin``
  - ``~/bin``   (i.e. ``$HOME/bin``)
  - ``~/.local/bin``

Version detection is intentionally not attempted. Executing binaries to
extract version information (e.g., via ``--version``) is avoided for safety,
so ``version_raw`` and ``version_parsed`` are always ``None``.

Tested against a temporary filesystem fixture; never reads the live system
during tests.
"""

import stat
from pathlib import Path

from env_audit.models import (
    BinaryRecord,
    Confidence,
    PackageMetadata,
    PackageRecord,
)

from .base import Collector, CollectorUnavailableError

__all__ = ["ManualBinaryCollector"]

# Default directories to scan — callers may override via the constructor.
DEFAULT_SCAN_DIRS: tuple[str, ...] = (
    "/usr/local/bin",
    str(Path.home() / "bin"),
    str(Path.home() / ".local" / "bin"),
)


def _is_executable_file(path: Path) -> bool:
    """Return True if *path* is a regular file with any execute bit set."""
    try:
        st = path.stat()
        return stat.S_ISREG(st.st_mode) and bool(st.st_mode & 0o111)
    except OSError:
        return False


def _is_symlink(path: Path) -> bool:
    """Return True if *path* is a symbolic link (lstat does not follow)."""
    try:
        return path.is_symlink()
    except OSError:
        return False


def _symlink_target(path: Path) -> str | None:
    """Return the symlink target as a string, or None if not a symlink."""
    try:
        if path.is_symlink():
            return str(path.resolve())
    except OSError:
        pass
    return None


class ManualBinaryCollector(Collector):
    """
    Scans directories for unmanaged executable files.

    Each discovered executable becomes a ``PackageRecord`` with:
    - ``ecosystem``  = ``"manual"``
    - ``source``     = the directory that contained the binary (absolute path)
    - ``binaries``   = one ``BinaryRecord`` at ``Confidence.MEDIUM``
    - ``version_*``  = ``None`` (version detection is not attempted)

    Symlinks are recorded faithfully but still produce a ``PackageRecord``
    so that PATH shadowing analysis can see them.
    """

    def __init__(self, scan_dirs: tuple[str, ...] = DEFAULT_SCAN_DIRS) -> None:
        self._scan_dirs = scan_dirs

    @property
    def ecosystem(self) -> str:
        return "manual"

    def is_available(self) -> bool:
        """
        Return True if at least one scan directory exists on this system.

        This collector is considered available whenever it can find any
        directory to scan — it does not require a specific binary in PATH.
        """
        return any(Path(d).is_dir() for d in self._scan_dirs)

    def collect(self) -> list[PackageRecord]:
        """
        Scan all configured directories and return one record per binary.

        Non-existent directories are silently skipped.
        Raises ``CollectorUnavailableError`` only when *none* of the
        configured directories exist.

        Never raises for individual file errors — unreadable files are
        skipped gracefully.
        """
        if not self.is_available():
            raise CollectorUnavailableError(
                self.ecosystem,
                f"none of the scan directories exist: {', '.join(self._scan_dirs)}",
            )

        records: list[PackageRecord] = []
        for directory in self._scan_dirs:
            dir_path = Path(directory)
            if not dir_path.is_dir():
                continue
            records.extend(self._scan_directory(dir_path))

        return records

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scan_directory(self, directory: Path) -> list[PackageRecord]:
        """
        Return a ``PackageRecord`` for each executable in *directory*.

        Subdirectories and non-executable files are ignored.
        """
        records: list[PackageRecord] = []
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return []

        for entry in entries:
            if not _is_executable_file(entry):
                continue

            is_sym = _is_symlink(entry)
            target = _symlink_target(entry) if is_sym else None

            binary = BinaryRecord(
                name=entry.name,
                path=str(entry),
                confidence=Confidence.MEDIUM,
                is_symlink=is_sym,
                symlink_target=target,
            )

            records.append(
                PackageRecord(
                    name=entry.name,
                    version_raw=None,
                    version_parsed=None,
                    ecosystem=self.ecosystem,
                    source=str(directory),
                    binaries=[binary],
                    metadata=PackageMetadata(),
                )
            )

        return records