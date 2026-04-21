# src/env_audit/analyzers/base.py
"""
Base classes for the env-audit analysis layer.

Every analyzer receives a normalized ``list[PackageRecord]`` and returns
a list of typed ``Finding`` objects.  Analyzers must never raise and
must never modify system state.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import Any

from env_audit.models import PackageRecord

__all__ = ["Analyzer", "Finding"]


@dataclasses.dataclass(frozen=True)
class Finding:
    """
    A single, explainable analysis finding.

    ``severity`` is one of ``"info"``, ``"warning"``, or ``"error"``.
    ``message`` is a human-readable description of the finding.

    Subclasses add domain-specific fields and override ``to_dict()`` to
    include a discriminating ``kind`` key for JSON consumers.
    """

    severity: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict of all fields."""
        return dataclasses.asdict(self)


class Analyzer(ABC):
    """
    Abstract base class for all env-audit analyzers.

    Each concrete analyzer implements a single concern (duplicates,
    PATH shadowing, orphaned binaries, …) and operates exclusively on
    the normalized ``list[PackageRecord]`` it receives.  No subprocess
    calls, no filesystem writes.
    """

    @abstractmethod
    def analyze(self, packages: list[PackageRecord]) -> list[Finding]:
        """
        Analyze *packages* and return a list of findings.

        Returns an empty list when no issues are detected.
        Never raises.
        """