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

"Manual" is a claim about provenance, not a permanent label.  A binary in
``~/.local/bin`` may in fact have been placed there by ``pip install --user``.
This collector cannot know that - collectors are independent and never
consult one another - so it reports what it sees.  The ``Normalizer``
resolves the overlap afterwards by dropping manual records whose binary
paths another ecosystem claims from a manifest.  See
``MANUAL_ECOSYSTEM`` and ``Normalizer.normalize()``.

Tested against a temporary filesystem fixture; never reads the live system
during tests.
"""

from pathlib import Path

from env_audit.models import (
    BinaryRecord,
    Confidence,
    PackageMetadata,
    PackageRecord,
)

from .base import Collector, CollectorUnavailableError
from .fsutil import is_executable_file, is_symlink, symlink_target

__all__ = ["MANUAL_ECOSYSTEM", "ManualBinaryCollector"]

#: Ecosystem identifier for unmanaged binaries.  Defined here (rather than
#: as a bare string literal in each consumer) because the normalizer and the
#: orphan analyzer both need to special-case it.
MANUAL_ECOSYSTEM = "manual"

# Default directories to scan - callers may override via the constructor.
DEFAULT_SCAN_DIRS: tuple[str, ...] = (
    "/usr/local/bin",
    str(Path.home() / "bin"),
    str(Path.home() / ".local" / "bin"),
)


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
        return MANUAL_ECOSYSTEM

    def is_available(self) -> bool:
        """
        Return True if at least one scan directory exists on this system.

        This collector is considered available whenever it can find any
        directory to scan - it does not require a specific binary in PATH.
        """
        return any(Path(d).is_dir() for d in self._scan_dirs)

    def collect(self) -> list[PackageRecord]:
        """
        Scan all configured directories and return one record per binary.

        Non-existent directories are silently skipped.
        Raises ``CollectorUnavailableError`` only when *none* of the
        configured directories exist.

        Never raises for individual file errors - unreadable files are
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
            if not is_executable_file(entry):
                continue

            is_sym = is_symlink(entry)
            target = symlink_target(entry) if is_sym else None

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