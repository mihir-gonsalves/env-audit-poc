# src/env_audit/collectors/npm.py
"""
npm global package collector for env-audit-poc.

Parses the output of ``npm list -g --json`` into PackageRecord objects.
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

__all__ = ["NpmCollector"]

# ---------------------------------------------------------------------------
# Module-level compiled regex for version parsing
# ---------------------------------------------------------------------------

# npm uses strict SemVer for most packages.  Examples:
#   10.2.4          -> major=10 minor=2 patch=4
#   5.3.3           -> major=5  minor=3 patch=3
#   1.22.21         -> major=1  minor=22 patch=21
#   17.0.6          -> major=17 minor=0 patch=6
#   5.0.1           -> major=5  minor=0 patch=1
#   1.0.0-beta.1    -> major=1  minor=0 patch=0  pre=beta.1
_NPM_VERSION_RE = re.compile(
    r"^(?P<major>\d+)"
    r"(?:\.(?P<minor>\d+))?"
    r"(?:\.(?P<patch>\d+))?"
    r"(?:-(?P<pre>[A-Za-z0-9._-]+))?$"
)


class NpmCollector(Collector):
    """
    Collects globally-installed npm packages via ``npm list -g --json``.

    The ``source`` field on every record is set to ``'npmjs'``.

    npm uses strict SemVer for most packages, so version parsing success
    rate is higher than for apt or pip.
    """

    @property
    def ecosystem(self) -> str:
        return "npm"

    def is_available(self) -> bool:
        """Return True if the ``npm`` binary is present in PATH."""
        return shutil.which("npm") is not None

    def collect(self) -> list[PackageRecord]:
        """
        Run ``npm list -g --json`` and return normalised package records.

        Raises
        ------
        CollectorUnavailableError
            If ``npm`` is not found in PATH.
        CollectorTimeoutError
            If the subprocess exceeds ``DEFAULT_TIMEOUT`` seconds.
        CollectorParseError
            If ``npm`` exits with an unexpected status or produces invalid JSON.
        """
        if not self.is_available():
            raise CollectorUnavailableError(
                self.ecosystem, "npm binary not found in PATH"
            )

        try:
            result = subprocess.run(
                ["npm", "list", "-g", "--json"],
                capture_output=True,
                text=True,
                timeout=self.DEFAULT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise CollectorTimeoutError(self.ecosystem, self.DEFAULT_TIMEOUT)

        # npm exits 1 when a package has unmet peer deps but still outputs
        # valid JSON — treat exit codes > 1 as hard failures.
        if result.returncode > 1:
            raise CollectorParseError(
                self.ecosystem,
                f"npm exited with status {result.returncode}: "
                f"{result.stderr.strip()}",
            )

        return self._parse(result.stdout)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse(self, output: str) -> list[PackageRecord]:
        """
        Parse the JSON output of ``npm list -g --json``.

        The top-level object has a ``"dependencies"`` key whose values are
        objects with at least a ``"version"`` key.  Malformed entries are
        silently skipped.  Never raises.
        """
        try:
            raw = json.loads(output)
        except json.JSONDecodeError:
            return []

        if not isinstance(raw, dict):
            return []

        dependencies = raw.get("dependencies", {})
        if not isinstance(dependencies, dict):
            return []

        records: list[PackageRecord] = []
        for name, info in dependencies.items():
            if not isinstance(name, str) or not name:
                continue
            if not isinstance(info, dict):
                continue

            version_raw = info.get("version")
            if not isinstance(version_raw, str):
                version_raw = None

            records.append(
                PackageRecord(
                    name=name,
                    version_raw=version_raw,
                    version_parsed=self._try_parse_semver(version_raw) if version_raw else None,
                    ecosystem=self.ecosystem,
                    source="npmjs",
                    metadata=PackageMetadata(),
                )
            )

        return records

    def _try_parse_semver(self, version: str) -> SemVer | None:
        """
        Attempt to parse an npm version string as SemVer.

        Returns ``None`` when the string cannot be mapped cleanly.
        """
        m = _NPM_VERSION_RE.match(version)
        if not m:
            return None

        return SemVer(
            major=int(m.group("major")),
            minor=int(m.group("minor") or 0),
            patch=int(m.group("patch") or 0),
            prerelease=m.group("pre"),
        )