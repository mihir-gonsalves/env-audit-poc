# src/env_audit/collectors/pip.py
"""
pip package collector for env-audit-poc.

Parses the output of ``pip list --format=json --verbose`` into
``PackageRecord`` objects and attaches the console scripts each package
owns.

Two-phase design
----------------
1. ``_parse()`` is a pure function: JSON string in, records out.  It never
   raises and never touches the filesystem, so it can be tested directly
   against captured fixture files.
2. ``_attach_binaries()`` reads each package's PEP 376 ``RECORD`` manifest
   from disk to discover the ``bin/`` entries the package installed.  It is
   separated from parsing precisely because it *does* need the filesystem;
   it is tested with ``tmp_path`` layouts and tolerates every ``OSError``.

Why ``RECORD`` and not ``pip show -f``?
---------------------------------------
``pip show -f`` reports the same information, but measured across 219
packages on the author's machine it took roughly 10 seconds and produced
2.6 MB of text that then has to be re-parsed.  Reading ``RECORD`` files
directly costs a few milliseconds: one ``iterdir()`` per site-packages
directory, then one small CSV read per package.

Why this matters
----------------
``pip install --user`` writes console scripts into ``~/.local/bin``, which
``ManualBinaryCollector`` also scans.  Without ownership information the
pipeline sees ``pytest`` as both a pip package and a manual binary and
reports a spurious cross-ecosystem duplicate.  Binaries attributed here
carry ``Confidence.HIGH`` because they come from a manifest, not a
heuristic, which is what lets the normalizer suppress the manual record.
"""

import csv
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from env_audit.models import (
    BinaryRecord,
    Confidence,
    PackageMetadata,
    PackageRecord,
    SemVer,
)

from .base import (
    Collector,
    CollectorParseError,
    CollectorTimeoutError,
    CollectorUnavailableError,
)
from .fsutil import symlink_target

__all__ = ["PipCollector"]

# ---------------------------------------------------------------------------
# Module-level compiled regex for version parsing
# ---------------------------------------------------------------------------

# Matches pip/PyPI version strings.  Examples:
#   23.3.2          -> major=23 minor=3 patch=2
#   2.5.3           -> major=2  minor=5 patch=3
#   1.0.0-dev1      -> major=1  minor=0 patch=0  pre=dev1
#   4.9.0           -> major=4  minor=9 patch=0
#   0.41.3          -> major=0  minor=41 patch=3
#   2.0a1           -> no match (letter suffix without separator)
_PIP_VERSION_RE = re.compile(
    r"^(?P<major>\d+)"
    r"(?:\.(?P<minor>\d+))?"
    r"(?:\.(?P<patch>\d+))?"
    r"(?:[-.](?P<pre>.+))?$"
)

# PEP 503 name normalization: runs of '-', '_' and '.' collapse to a single
# '-', then lowercase.  Needed because a dist-info directory escapes the
# project name per PEP 427 ('mypy_extensions-1.0.0.dist-info') and may keep
# its original case ('Jinja2-3.0.3.dist-info'), while ``pip list`` reports
# the name in yet another form ('mypy-extensions', 'Jinja2').
_NAME_SEPARATOR_RE = re.compile(r"[-_.]+")

#: Suffix marking a PEP 376 metadata directory.  Packages installed by apt
#: into ``/usr/lib/python3/dist-packages`` use ``.egg-info`` instead and
#: carry no ``RECORD``; they are skipped silently.
_DIST_INFO_SUFFIX = ".dist-info"

#: Directory name that console scripts live in, relative to the environment
#: root.  ``RECORD`` paths reach it as ``../../../bin/<script>``.
_SCRIPT_DIR_NAME = "bin"


def _normalize_name(name: str) -> str:
    """
    Return the PEP 503 normalized form of a distribution *name*.

    ``'Mypy.Extensions'``, ``'mypy_extensions'`` and ``'mypy-extensions'``
    all normalize to ``'mypy-extensions'``.
    """
    return _NAME_SEPARATOR_RE.sub("-", name).lower()


class PipCollector(Collector):
    """
    Collects globally-installed Python packages via
    ``pip list --format=json --verbose``.

    The ``source`` field on every record is set to ``'pypi'`` - pip does not
    expose per-package index information in its list output.

    Version strings are normalized to ``SemVer`` on a best-effort basis.
    Pre-release suffixes (``1.0.0-dev1``, ``2.0.0.post1``) are captured as
    the ``prerelease`` field so they survive round-tripping.

    ``--verbose`` adds a ``location`` key (the site-packages directory) at
    no measurable cost - both forms run in about 0.55 s across 219 packages.
    That location is stored as ``PackageRecord.install_path`` and is the
    anchor used to find each package's ``RECORD`` manifest and, from it, the
    console scripts the package owns.
    """

    # Command used to locate pip; prefer pip3 over pip for clarity.
    _PIP_BINARY = "pip3"

    @property
    def ecosystem(self) -> str:
        return "pip"

    def is_available(self) -> bool:
        """Return True if a pip binary is present in PATH."""
        return shutil.which(self._PIP_BINARY) is not None or shutil.which("pip") is not None

    def _pip_binary(self) -> str:
        """Return the first available pip binary name."""
        return self._PIP_BINARY if shutil.which(self._PIP_BINARY) else "pip"

    def collect(self) -> list[PackageRecord]:
        """
        Run ``pip list`` and return normalised package records.

        The parsed records are enriched with the console scripts each
        package owns before being returned.  Manifest lookup never raises:
        a package whose ``RECORD`` is missing or unreadable simply carries
        no binaries.

        Raises
        ------
        CollectorUnavailableError
            If neither ``pip3`` nor ``pip`` is found in PATH.
        CollectorTimeoutError
            If the subprocess exceeds ``DEFAULT_TIMEOUT`` seconds.
        CollectorParseError
            If ``pip`` exits with a non-zero status or produces invalid JSON.
        """
        if not self.is_available():
            raise CollectorUnavailableError(
                self.ecosystem, "pip binary not found in PATH"
            )

        binary = self._pip_binary()
        try:
            result = subprocess.run(
                [binary, "list", "--format=json", "--verbose"],
                capture_output=True,
                text=True,
                timeout=self.DEFAULT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise CollectorTimeoutError(self.ecosystem, self.DEFAULT_TIMEOUT)

        if result.returncode != 0:
            raise CollectorParseError(
                self.ecosystem,
                f"pip exited with status {result.returncode}: "
                f"{result.stderr.strip()}",
            )

        return self._attach_binaries(self._parse(result.stdout))

    # ------------------------------------------------------------------
    # Internal helpers - parsing (pure, no filesystem)
    # ------------------------------------------------------------------

    def _parse(self, output: str) -> list[PackageRecord]:
        """
        Parse the JSON output of ``pip list --format=json --verbose``.

        Each element must have a ``"name"`` and ``"version"`` key.  The
        ``"location"`` and ``"editable_project_location"`` keys added by
        ``--verbose`` are optional: older pip versions omit them, and the
        non-verbose fixture has neither.

        Malformed entries (missing keys, wrong types) are silently skipped.
        Never raises and never touches the filesystem.
        """
        try:
            raw = json.loads(output)
        except json.JSONDecodeError:
            # Callers expecting a list get an empty list on parse failure;
            # the exception detail is only useful when called from collect()
            # which has already checked returncode.  Return empty to keep
            # _parse() pure and never-raising.
            return []

        if not isinstance(raw, list):
            return []

        records: list[PackageRecord] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            version_raw = entry.get("version")
            if not isinstance(name, str) or not name:
                continue
            if not isinstance(version_raw, str):
                version_raw = None

            location = entry.get("location")
            if not isinstance(location, str) or not location:
                location = None

            editable = entry.get("editable_project_location")
            extensions: dict[str, str] = {}
            if isinstance(editable, str) and editable:
                extensions["pip:editable_project_location"] = editable

            records.append(
                PackageRecord(
                    name=name,
                    version_raw=version_raw,
                    version_parsed=self._try_parse_semver(version_raw) if version_raw else None,
                    ecosystem=self.ecosystem,
                    source="pypi",
                    install_path=location,
                    metadata=PackageMetadata(extensions=extensions),
                )
            )

        return records

    def _try_parse_semver(self, version: str) -> SemVer | None:
        """
        Attempt to parse a pip/PyPI version string as SemVer.

        Returns ``None`` when the string cannot be mapped cleanly.
        """
        m = _PIP_VERSION_RE.match(version)
        if not m:
            return None

        pre = m.group("pre")

        return SemVer(
            major=int(m.group("major")),
            minor=int(m.group("minor") or 0),
            patch=int(m.group("patch") or 0),
            prerelease=pre,
        )

    # ------------------------------------------------------------------
    # Internal helpers - manifest lookup (reads the filesystem)
    # ------------------------------------------------------------------

    def _attach_binaries(self, records: list[PackageRecord]) -> list[PackageRecord]:
        """
        Return *records* with console scripts attached from each ``RECORD``.

        A record is returned unchanged (the same object) when it has no
        ``install_path``, when no matching ``.dist-info`` directory exists,
        or when the manifest lists no ``bin/`` entries.  Records that do
        gain binaries are replaced by a copy, since ``PackageRecord`` is
        frozen.

        Each site-packages directory is listed once and cached: a typical
        environment has 150+ packages sharing two or three locations.

        Never raises.
        """
        index_cache: dict[str, dict[str, list[Path]]] = {}
        enriched: list[PackageRecord] = []

        for pkg in records:
            if pkg.install_path is None:
                enriched.append(pkg)
                continue

            location = Path(pkg.install_path)
            if pkg.install_path not in index_cache:
                index_cache[pkg.install_path] = self._index_dist_info(location)
            index = index_cache[pkg.install_path]

            candidates = index.get(_normalize_name(pkg.name))
            if not candidates:
                enriched.append(pkg)
                continue

            dist_info = self._pick_dist_info(candidates, pkg.version_raw)
            binaries = self._binaries_from_record(dist_info, location)
            if not binaries:
                enriched.append(pkg)
                continue

            enriched.append(pkg.model_copy(update={"binaries": binaries}))

        return enriched

    def _index_dist_info(self, location: Path) -> dict[str, list[Path]]:
        """
        Map normalized distribution name -> ``.dist-info`` directories in
        *location*.

        A name maps to a *list* because an interrupted upgrade can leave two
        ``.dist-info`` directories for the same project; ``_pick_dist_info``
        chooses between them.

        Entries that are not ``.dist-info`` directories are ignored, which
        excludes the ``.egg-info`` directories apt-installed packages use
        (they carry no ``RECORD``).

        Returns an empty mapping when *location* is missing or unreadable.
        Never raises.
        """
        index: dict[str, list[Path]] = {}
        try:
            entries = sorted(location.iterdir())
        except OSError:
            return {}

        for entry in entries:
            if not entry.name.endswith(_DIST_INFO_SUFFIX):
                continue
            # The escaped name never contains '-', so the first '-' splits
            # name from version: 'mypy_extensions-1.0.0.dist-info'.
            raw_name = entry.name.split("-", 1)[0]
            index.setdefault(_normalize_name(raw_name), []).append(entry)

        return index

    @staticmethod
    def _pick_dist_info(candidates: list[Path], version_raw: str | None) -> Path:
        """
        Choose one ``.dist-info`` directory from *candidates*.

        Prefers the directory whose version segment matches *version_raw*
        (what pip actually reported as installed); falls back to the first
        candidate in sorted order for determinism.
        """
        if version_raw is not None:
            wanted = f"-{version_raw}{_DIST_INFO_SUFFIX}"
            for candidate in candidates:
                if candidate.name.endswith(wanted):
                    return candidate
        return candidates[0]

    @staticmethod
    def _binaries_from_record(dist_info: Path, location: Path) -> list[BinaryRecord]:
        """
        Return one ``BinaryRecord`` per ``bin/`` entry in *dist_info*'s
        ``RECORD``.

        ``RECORD`` is a CSV of ``path,hash,size`` whose paths are relative
        to *location*; console scripts appear as ``../../../bin/<name>``.
        An entry is kept only when its immediate parent directory is named
        ``bin``, which excludes ``bin/__pycache__/*.pyc`` as well as the
        ``share/man`` and ``include/`` entries some packages install.

        Existence is deliberately not checked.  The manifest is the
        authority on ownership, and the normalizer matches on path alone.
        Confidence is ``HIGH`` because this is manifest data, not a
        heuristic.

        Returns an empty list when ``RECORD`` is missing, unreadable, or
        malformed.  Never raises.
        """
        record_path = dist_info / "RECORD"
        try:
            with record_path.open(newline="", encoding="utf-8", errors="replace") as handle:
                rows = list(csv.reader(handle))
        except (OSError, csv.Error):
            return []

        binaries: list[BinaryRecord] = []
        for row in rows:
            if not row or not row[0]:
                continue

            absolute = Path(os.path.normpath(location / row[0]))
            if absolute.parent.name != _SCRIPT_DIR_NAME:
                continue
            # BinaryRecord requires a non-root absolute path.  A relative
            # ``location`` (never produced by pip, but possible if a caller
            # constructs records by hand) would fail validation, so skip.
            if not absolute.is_absolute():
                continue

            # Derive is_symlink from the resolved target rather than asking
            # twice: BinaryRecord rejects is_symlink=True with no target,
            # and the two calls could disagree if the link is removed
            # between them.
            target = symlink_target(absolute)
            binaries.append(
                BinaryRecord(
                    name=absolute.name,
                    path=str(absolute),
                    confidence=Confidence.HIGH,
                    is_symlink=target is not None,
                    symlink_target=target,
                )
            )

        return binaries