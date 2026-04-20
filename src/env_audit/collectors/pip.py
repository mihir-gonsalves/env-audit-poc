# src/env_audit/collectors/pip.py
"""
pip package collector for env-audit-poc.

Parses the output of ``pip list --format=json`` into PackageRecord objects.
Tested against fixture files; never touches the live system in tests.
"""

import json
import re
import shutil
import subprocess

from env_audit.models import PackageMetadata, PackageRecord, SemVer

from .base import (
    Collector,
    CollectorParseError,
    CollectorTimeoutError,
    CollectorUnavailableError,
)

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


class PipCollector(Collector):
    """
    Collects globally-installed Python packages via ``pip list --format=json``.

    The ``source`` field on every record is set to ``'pypi'`` — pip does not
    expose per-package index information in its list output.

    Version strings are normalized to ``SemVer`` on a best-effort basis.
    Pre-release suffixes (``1.0.0-dev1``, ``2.0.0.post1``) are captured as
    the ``prerelease`` field so they survive round-tripping.
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
        Run ``pip list --format=json`` and return normalised package records.

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
                [binary, "list", "--format=json"],
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

        return self._parse(result.stdout)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse(self, output: str) -> list[PackageRecord]:
        """
        Parse the JSON output of ``pip list --format=json``.

        Each element must have a ``"name"`` and ``"version"`` key.
        Malformed entries (missing keys, wrong types) are silently skipped.
        Never raises.
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

            records.append(
                PackageRecord(
                    name=name,
                    version_raw=version_raw,
                    version_parsed=self._try_parse_semver(version_raw) if version_raw else None,
                    ecosystem=self.ecosystem,
                    source="pypi",
                    metadata=PackageMetadata(),
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