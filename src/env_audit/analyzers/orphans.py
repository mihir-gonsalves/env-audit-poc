# src/env_audit/analyzers/orphans.py
"""
Orphaned binary analyzer for env-audit-poc.

An "orphaned" binary is one found in the ``manual`` ecosystem for which
no other package manager (apt, pip, npm, …) has a record claiming that
name — either as a package name or as an explicit binary record.

This is a heuristic: a binary named ``git`` in ``~/bin`` would not be
flagged as an orphan if ``git`` is also present as an apt package, even
if the two are completely different programs.  The goal is to surface
executables that are entirely untracked, not to guarantee exact ownership.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from env_audit.models import PackageRecord

from .base import Analyzer, Finding

__all__ = ["OrphanedBinaryAnalyzer", "OrphanedBinaryFinding"]


@dataclasses.dataclass(frozen=True)
class OrphanedBinaryFinding(Finding):
    """
    A manually-installed binary that no package manager claims.

    ``binary_name`` is the executable file name (e.g. ``my-custom-tool``).
    ``path`` is its absolute path on disk.
    """

    binary_name: str
    path: str

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["kind"] = "orphaned_binary"
        return d


class OrphanedBinaryAnalyzer(Analyzer):
    """
    Identifies manually-installed binaries with no known package owner.

    Strategy
    --------
    1. Collect all package names *and* explicit binary names from every
       non-``manual`` ecosystem into a ``managed_names`` set.
    2. Iterate over every binary attached to a ``manual``-ecosystem record.
    3. If a binary's name is absent from ``managed_names``, emit a finding.

    Findings are returned sorted by ``(binary_name, path)`` for stable,
    deterministic output.
    """

    def analyze(self, packages: list[PackageRecord]) -> list[Finding]:
        """Return one finding per unmanaged manual binary."""
        # Step 1: collect all names claimed by non-manual ecosystems.
        managed_names: set[str] = set()
        for pkg in packages:
            if pkg.ecosystem == "manual":
                continue
            managed_names.add(pkg.name)
            for binary in pkg.binaries:
                managed_names.add(binary.name)

        # Step 2 & 3: check each manual binary against managed names.
        orphan_findings: list[OrphanedBinaryFinding] = []
        for pkg in packages:
            if pkg.ecosystem != "manual":
                continue
            for binary in pkg.binaries:
                if binary.name not in managed_names:
                    orphan_findings.append(
                        OrphanedBinaryFinding(
                            severity="info",
                            message=(
                                f"Binary '{binary.name}' at '{binary.path}' "
                                f"has no known package manager owner"
                            ),
                            binary_name=binary.name,
                            path=binary.path,
                        )
                    )

        return sorted(  # type: ignore[return-value]
            orphan_findings,
            key=lambda f: (f.binary_name, f.path),
        )