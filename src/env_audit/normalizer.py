# src/env_audit/normalizer.py
"""
Normalizer for env-audit-poc.

Takes the raw union of packages emitted by all collectors and produces a
clean, deterministic, analysis-ready list.

Responsibilities
----------------
1. **Sort** — packages are sorted by (ecosystem, name) so output is
   stable across runs regardless of collector execution order.
2. **Deduplicate within ecosystem** — when the same package name appears
   multiple times from the *same* collector (which should not normally
   happen but may for manual/filesystem scans), keep only the record with
   the highest parsed version; when versions cannot be compared, keep the
   first occurrence.
3. **Cross-ecosystem duplicate detection** — identify package names that
   appear in more than one ecosystem so downstream analyzers can surface
   potential conflicts without the normalizer needing to pick a winner.

The normalizer does **not**:
- Modify system state.
- Merge records from different ecosystems into a single record.
- Make decisions about which version or ecosystem is "correct".
- Run any subprocesses.

Design note: All input records are immutable (Pydantic frozen models) so
the normalizer never mutates them — it only selects, sorts, and groups.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from env_audit.models import PackageRecord

__all__ = ["NormalizerResult", "Normalizer"]


@dataclass
class NormalizerResult:
    """
    Output of a normalizer run.

    Attributes
    ----------
    packages : list[PackageRecord]
        Deduplicated, sorted package list ready for analysis or rendering.
    cross_ecosystem_duplicates : dict[str, list[str]]
        Mapping of package name -> list of ecosystems in which it appears.
        Only names present in *more than one* ecosystem are included.
    intra_ecosystem_duplicates : dict[tuple[str, str], int]
        Mapping of ``(ecosystem, name)`` -> count of raw input records that
        were collapsed to one.  Only pairs with count > 1 are included.
    """

    packages: list[PackageRecord] = field(default_factory=list)
    cross_ecosystem_duplicates: dict[str, list[str]] = field(default_factory=dict)
    intra_ecosystem_duplicates: dict[tuple[str, str], int] = field(default_factory=dict)


class Normalizer:
    """
    Cleans and organizes a raw package list produced by the orchestrator.

    Usage::

        result = Normalizer().normalize(audit_result.packages)
        for pkg in result.packages:
            ...
    """

    def normalize(self, packages: list[PackageRecord]) -> NormalizerResult:
        """
        Normalize *packages* and return a ``NormalizerResult``.

        The algorithm is O(n log n) and allocates no unnecessary copies.

        Parameters
        ----------
        packages:
            Raw package list from the orchestrator.  May be empty.

        Returns
        -------
        NormalizerResult
            Sorted, deduplicated packages plus duplicate metadata.
        """
        if not packages:
            return NormalizerResult()

        # ----------------------------------------------------------------
        # Step 1: group by (ecosystem, name) to find intra-ecosystem dupes
        # ----------------------------------------------------------------
        groups: dict[tuple[str, str], list[PackageRecord]] = defaultdict(list)
        for pkg in packages:
            groups[(pkg.ecosystem, pkg.name)].append(pkg)

        # ----------------------------------------------------------------
        # Step 2: collapse each group to a single representative record
        # ----------------------------------------------------------------
        intra_dupes: dict[tuple[str, str], int] = {}
        deduped: list[PackageRecord] = []

        for key, group in groups.items():
            if len(group) > 1:
                intra_dupes[key] = len(group)
            deduped.append(self._pick_best(group))

        # ----------------------------------------------------------------
        # Step 3: sort for deterministic output
        # ----------------------------------------------------------------
        deduped.sort(key=lambda p: (p.ecosystem, p.name))

        # ----------------------------------------------------------------
        # Step 4: cross-ecosystem duplicate detection
        # ----------------------------------------------------------------
        name_to_ecosystems: dict[str, list[str]] = defaultdict(list)
        for pkg in deduped:
            name_to_ecosystems[pkg.name].append(pkg.ecosystem)

        cross_dupes = {
            name: ecosystems
            for name, ecosystems in name_to_ecosystems.items()
            if len(ecosystems) > 1
        }

        return NormalizerResult(
            packages=deduped,
            cross_ecosystem_duplicates=cross_dupes,
            intra_ecosystem_duplicates=intra_dupes,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pick_best(group: list[PackageRecord]) -> PackageRecord:
        """
        Return the record with the highest parsed version from *group*.

        If no record in the group has a parsed version, the first record
        is returned (preserving collector order).
        """
        # Fast path: only one record in the group.
        if len(group) == 1:
            return group[0]

        versioned = [p for p in group if p.version_parsed is not None]
        if not versioned:
            # No parseable versions — keep first occurrence.
            return group[0]

        # Return the record with the maximum parsed version.
        return max(versioned, key=lambda p: p.version_parsed)  # type: ignore[return-value]