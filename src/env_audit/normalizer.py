# src/env_audit/normalizer.py
"""
Normalizer for env-audit-poc.

Takes the raw union of packages emitted by all collectors and produces a
clean, deterministic, analysis-ready list.

Responsibilities
----------------
0. **Suppress claimed manual records** - a binary in ``~/.local/bin`` may
   have been placed there by ``pip install --user`` rather than by hand.
   Collectors are independent and cannot know this, so the manual scanner
   reports it and the normalizer resolves the overlap: a ``manual`` record
   is dropped when every one of its binaries is claimed, at the same
   absolute path, by a ``Confidence.HIGH`` binary from another ecosystem.
   This is the right layer for the rule - doing it in the collectors would
   force one to run after another, and doing it in the analyzers would put
   three copies of the same rule in three places.
1. **Sort** - packages are sorted by (ecosystem, name) so output is
   stable across runs regardless of collector execution order.
2. **Deduplicate within ecosystem** - when the same package name appears
   multiple times from the *same* collector (which should not normally
   happen but may for manual/filesystem scans), keep only the record with
   the highest parsed version; when versions cannot be compared, keep the
   first occurrence.
3. **Cross-ecosystem duplicate detection** - identify package names that
   appear in more than one ecosystem so downstream analyzers can surface
   potential conflicts without the normalizer needing to pick a winner.

The normalizer does **not**:
- Modify system state.
- Merge records from different ecosystems into a single record.
- Make decisions about which version or ecosystem is "correct".
- Run any subprocesses.

Design note: All input records are immutable (Pydantic frozen models) so
the normalizer never mutates them - it only selects, sorts, and groups.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field

from env_audit.collectors.manual import MANUAL_ECOSYSTEM
from env_audit.models import Confidence, PackageRecord

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
    suppressed_manual_binaries : dict[str, str]
        Mapping of binary path -> owning ecosystem, for every ``manual``
        record dropped because another ecosystem's manifest claimed it.
        Recorded so the decision is explainable rather than silent.
    """

    packages: list[PackageRecord] = field(default_factory=list)
    cross_ecosystem_duplicates: dict[str, list[str]] = field(default_factory=dict)
    intra_ecosystem_duplicates: dict[tuple[str, str], int] = field(default_factory=dict)
    suppressed_manual_binaries: dict[str, str] = field(default_factory=dict)


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
        # Step 0: drop manual records whose binaries another ecosystem owns
        # ----------------------------------------------------------------
        packages, suppressed = self._suppress_claimed_manual(packages)

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
            suppressed_manual_binaries=suppressed,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _suppress_claimed_manual(
        packages: list[PackageRecord],
    ) -> tuple[list[PackageRecord], dict[str, str]]:
        """
        Return (*kept packages*, *suppressed path -> owning ecosystem*).

        A ``manual`` record is dropped only when it has at least one binary
        and *every* one of its binaries is claimed by a ``Confidence.HIGH``
        binary at the same absolute path in a non-manual ecosystem.

        Only HIGH confidence counts.  A MEDIUM or LOW attribution is itself
        a heuristic, and one heuristic must not silently delete another.

        Matching is on the normalized absolute path only - no ``realpath``,
        no filesystem access, so the normalizer stays a pure function of its
        input.  The cost is that a hand-made symlink *pointing at* a pip
        script, but living at a different path, is not suppressed.  That is
        the correct outcome anyway: it is a real, separately-placed file.
        """
        claimed: dict[str, str] = {}
        for pkg in packages:
            if pkg.ecosystem == MANUAL_ECOSYSTEM:
                continue
            for binary in pkg.binaries:
                if binary.confidence == Confidence.HIGH:
                    claimed.setdefault(os.path.normpath(binary.path), pkg.ecosystem)

        kept: list[PackageRecord] = []
        suppressed: dict[str, str] = {}

        for pkg in packages:
            paths = [os.path.normpath(b.path) for b in pkg.binaries]
            if (
                pkg.ecosystem == MANUAL_ECOSYSTEM
                and paths
                and all(path in claimed for path in paths)
            ):
                for path in paths:
                    suppressed[path] = claimed[path]
                continue
            kept.append(pkg)

        return kept, suppressed

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
            # No parseable versions - keep first occurrence.
            return group[0]

        # Return the record with the maximum parsed version.
        return max(versioned, key=lambda p: p.version_parsed)  # type: ignore[return-value]