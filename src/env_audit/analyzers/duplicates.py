# src/env_audit/analyzers/duplicates.py
"""
Cross-ecosystem duplicate package detector for env-audit-poc.

By the time this analyzer runs the normalizer has already collapsed
intra-ecosystem duplicates (same name, same ecosystem) down to a single
record.  This analyzer therefore only needs to look for the same package
*name* appearing in more than one *distinct* ecosystem.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from typing import Any

from env_audit.models import PackageRecord

from .base import Analyzer, Finding

__all__ = ["CrossEcosystemDuplicate", "DuplicateAnalyzer"]


@dataclasses.dataclass(frozen=True)
class CrossEcosystemDuplicate(Finding):
    """
    A package name found in more than one ecosystem.

    Example: ``python3`` is installed via both ``apt`` and ``pip``.

    ``ecosystems`` is sorted for deterministic output.
    """

    name: str
    ecosystems: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)   # preserves tuple types (does not convert to list)
        d["kind"] = "cross_ecosystem_duplicate"
        return d


class DuplicateAnalyzer(Analyzer):
    """
    Detects package names that appear in more than one distinct ecosystem.

    One ``CrossEcosystemDuplicate`` finding is produced per affected
    name.  Findings are returned sorted by package name for stable,
    deterministic output.
    """

    def analyze(self, packages: list[PackageRecord]) -> list[Finding]:
        """Return one finding per package name that spans multiple ecosystems."""
        name_to_ecosystems: dict[str, list[str]] = defaultdict(list)
        for pkg in packages:
            name_to_ecosystems[pkg.name].append(pkg.ecosystem)

        findings: list[Finding] = []
        for name, ecosystems in sorted(name_to_ecosystems.items()):
            unique = sorted(set(ecosystems))
            if len(unique) < 2:
                continue
            findings.append(
                CrossEcosystemDuplicate(
                    severity="warning",
                    message=(
                        f"'{name}' is installed in {len(unique)} ecosystems: "
                        f"{', '.join(unique)}"
                    ),
                    name=name,
                    ecosystems=tuple(unique),
                )
            )

        return findings