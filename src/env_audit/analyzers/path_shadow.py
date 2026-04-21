# src/env_audit/analyzers/path_shadow.py
"""
PATH shadowing analyzer for env-audit-poc.

Identifies binary name collisions across packages and determines which
binary "wins" based on the directories' positions in ``PATH`` at audit
time.

Note: Only ``PackageRecord.binaries`` entries are inspected.  In the
current implementation, only ``ManualBinaryCollector`` populates binary
records; apt/pip/npm records carry an empty ``binaries`` list.
"""

from __future__ import annotations

import dataclasses
import os
from collections import defaultdict
from typing import Any

from env_audit.models import PackageRecord

from .base import Analyzer, Finding

__all__ = ["PathShadowAnalyzer", "ShadowedBinaryFinding"]


@dataclasses.dataclass(frozen=True)
class ShadowedBinaryFinding(Finding):
    """
    A binary name that resolves to different executables depending on
    which PATH directory takes priority.

    ``winner_path`` is the path that a shell would execute.
    ``shadowed_paths`` are the paths hidden by the winner.
    """

    binary_name: str
    winner_path: str
    winner_package: str
    shadowed_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)   # preserves tuple types (does not convert to list)
        d["kind"] = "path_shadow"
        return d


class PathShadowAnalyzer(Analyzer):
    """
    Detects binary name collisions, ranked by PATH order.

    PATH is read from the environment at construction time, or from the
    ``path`` argument when provided (which allows deterministic testing
    without touching ``os.environ``).

    A shadow is reported whenever the same binary name appears in two or
    more distinct paths across all packages.  When a path's parent
    directory is not in PATH, that binary receives the lowest possible
    priority rank (``len(path_dirs)``).

    Findings are returned sorted by binary name.
    """

    def __init__(self, path: str | None = None) -> None:
        """
        Parameters
        ----------
        path:
            Override ``os.environ["PATH"]`` for testing.  When ``None``
            (the default), the live environment is used at analysis time.
        """
        self._path = path

    def analyze(self, packages: list[PackageRecord]) -> list[Finding]:
        """Return one finding per binary name that has more than one path."""
        path_dirs = self._path_dirs()

        # Collect all binary records: name -> [(binary_path, package_name)]
        binary_map: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for pkg in packages:
            for binary in pkg.binaries:
                binary_map[binary.name].append((binary.path, pkg.name))

        findings: list[Finding] = []
        for binary_name, entries in sorted(binary_map.items()):
            if len(entries) < 2:
                continue

            sorted_entries = sorted(
                entries,
                key=lambda e: self._rank(e[0], path_dirs),
            )
            winner_path, winner_package = sorted_entries[0]
            shadowed = tuple(path for path, _ in sorted_entries[1:])

            findings.append(
                ShadowedBinaryFinding(
                    severity="warning",
                    message=(
                        f"Binary '{binary_name}' at '{winner_path}' shadows "
                        f"{len(shadowed)} other installation(s): "
                        f"{', '.join(shadowed)}"
                    ),
                    binary_name=binary_name,
                    winner_path=winner_path,
                    winner_package=winner_package,
                    shadowed_paths=shadowed,
                )
            )

        return findings

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _path_dirs(self) -> list[str]:
        """Return ordered, non-empty PATH components."""
        raw = self._path if self._path is not None else os.environ.get("PATH", "")
        return [d for d in raw.split(os.pathsep) if d]

    @staticmethod
    def _rank(binary_path: str, path_dirs: list[str]) -> int:
        """
        Return the PATH rank for *binary_path*.

        Lower rank = higher priority (earlier in PATH).
        Returns ``len(path_dirs)`` (lowest priority) when the binary's
        parent directory is not present in PATH.
        """
        parent = os.path.dirname(binary_path)
        try:
            return path_dirs.index(parent)
        except ValueError:
            return len(path_dirs)