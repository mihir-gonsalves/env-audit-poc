# src/env_audit/collectors/apt.py
"""
APT package collector for env-audit-poc.

Parses the output of ``LANG=C apt list --installed`` into PackageRecord
objects. Tested against fixture files; never touches the live system in
tests.
"""

import os
import re
import shutil
import subprocess

from env_audit.models import (
    InstallReason,
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

__all__ = ["AptCollector"]

# ---------------------------------------------------------------------------
# Module-level compiled regexes
# ---------------------------------------------------------------------------

# Matches a single ``apt list --installed`` data line, e.g.:
#   python3.11/jammy-updates,jammy-security 3.11.6-1~22.04 amd64 [installed,automatic]
_LINE_RE = re.compile(
    r"^(?P<name>[^/\s]+)"           # package name (no slash, no space)
    r"/(?P<source>\S+)"             # /source(s) — may be comma-separated
    r"\s+(?P<version>\S+)"          # version string
    r"\s+(?P<arch>\S+)"             # architecture
    r"\s+\[(?P<status>[^\]]+)\]"    # [status] — e.g. installed, installed,automatic
)

# Best-effort SemVer extraction from Debian/Ubuntu version strings, e.g.:
#   3.11.6-1~22.04   →  3.11.6-pre=1~22.04
#   2:8.2.3995-1ubuntu2.17   →  (epoch stripped) 8.2.3995-pre=1ubuntu2.17
#   3.118ubuntu5     →  no match (no separator before "ubuntu")
_VERSION_RE = re.compile(
    r"^(?:\d+:)?"                       # optional epoch  (e.g. "2:")
    r"(?P<major>\d+)"                   # major  (required)
    r"(?:\.(?P<minor>\d+))?"            # optional  .minor
    r"(?:\.(?P<patch>\d+))?"            # optional  .patch
    r"(?:[-~](?P<pre>.+))?$"            # optional  -prerelease  or  ~prerelease
)


class AptCollector(Collector):
    """
    Collects installed packages from the APT package manager.

    Runs ``apt list --installed`` with ``LANG=C`` to guarantee stable,
    locale-independent output, then normalises each line into a
    ``PackageRecord``.

    The ``source`` field on each record is set to the first repository
    listed for the package (the part before any comma in the apt output).
    """

    @property
    def ecosystem(self) -> str:
        return "apt"

    def is_available(self) -> bool:
        """Return True if the ``apt`` binary is present in PATH."""
        return shutil.which("apt") is not None

    def collect(self) -> list[PackageRecord]:
        """
        Run ``apt list --installed`` and return normalised package records.

        Raises
        ------
        CollectorUnavailableError
            If ``apt`` is not found in PATH.
        CollectorTimeoutError
            If the subprocess exceeds ``DEFAULT_TIMEOUT`` seconds.
        CollectorParseError
            If ``apt`` exits with a non-zero status and produces no output.
        """
        if not self.is_available():
            raise CollectorUnavailableError(
                self.ecosystem, "apt binary not found in PATH"
            )

        env = {**os.environ, "LANG": "C"}
        try:
            result = subprocess.run(
                ["apt", "list", "--installed"],
                env=env,
                capture_output=True,
                text=True,
                timeout=self.DEFAULT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise CollectorTimeoutError(self.ecosystem, self.DEFAULT_TIMEOUT)

        if result.returncode != 0:
            raise CollectorParseError(
                self.ecosystem,
                f"apt exited with status {result.returncode}: "
                f"{result.stderr.strip()}",
            )

        return self._parse(result.stdout)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse(self, output: str) -> list[PackageRecord]:
        """
        Parse the raw stdout of ``apt list --installed``.

        Skips the ``Listing...`` header and any line that does not match
        the expected format; never raises.
        """
        records: list[PackageRecord] = []

        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith("Listing..."):
                continue

            m = _LINE_RE.match(line)
            if not m:
                continue

            name = m.group("name")
            # apt may list multiple sources separated by commas; take the first.
            source = m.group("source").split(",")[0]
            version_raw = m.group("version")
            arch = m.group("arch")
            status = m.group("status")

            # "automatic" means installed as a dependency; otherwise explicit.
            if "automatic" in status:
                install_reason = InstallReason.DEPENDENCY
            else:
                install_reason = InstallReason.EXPLICIT

            records.append(
                PackageRecord(
                    name=name,
                    version_raw=version_raw,
                    version_parsed=self._try_parse_semver(version_raw),
                    ecosystem=self.ecosystem,
                    source=source,
                    metadata=PackageMetadata(
                        install_reason=install_reason,
                        extensions={"apt:architecture": arch},
                    ),
                )
            )

        return records

    def _try_parse_semver(self, version: str) -> SemVer | None:
        """
        Attempt to parse a Debian/Ubuntu version string as SemVer.

        Returns ``None`` when the string cannot be mapped to a semantic
        version (e.g. ``3.118ubuntu5``, ``1.2.11.dfsg-2ubuntu9``).
        """
        m = _VERSION_RE.match(version)
        if not m:
            return None

        return SemVer(
            major=int(m.group("major")),
            minor=int(m.group("minor") or 0),
            patch=int(m.group("patch") or 0),
            prerelease=m.group("pre"),   # None when group didn't participate
        )