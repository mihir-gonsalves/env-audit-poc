# src/env_audit/orchestrator.py
"""
Orchestrator for env-audit.

Runs all registered collectors independently.  One collector failing
never prevents others from running; errors are collected per-ecosystem
and returned alongside successful results.
"""

from dataclasses import dataclass, field

from env_audit.collectors.base import Collector, CollectorError
from env_audit.models import PackageRecord

__all__ = ["AuditResult", "Orchestrator"]


@dataclass
class AuditResult:
    """
    Outcome of a full audit run.

    ``packages`` contains the union of all packages gathered by every
    collector that succeeded.  ``errors`` maps ecosystem name -> the
    ``CollectorError`` raised by that collector.
    """

    packages: list[PackageRecord] = field(default_factory=list)
    errors: dict[str, CollectorError] = field(default_factory=dict)


class Orchestrator:
    """
    Drives a set of collectors and assembles their output.

    Each collector is run in sequence.  ``CollectorError`` exceptions
    are caught per-collector and stored in ``AuditResult.errors`` so
    that one broken collector never aborts the entire audit.
    """

    def __init__(self, collectors: list[Collector]) -> None:
        self._collectors = collectors

    def run(self) -> AuditResult:
        """
        Execute every collector and return the combined result.

        Returns
        -------
        AuditResult
            Contains all packages gathered by successful collectors and
            a mapping of ecosystem -> error for those that failed.
        """
        result = AuditResult()
        for collector in self._collectors:
            try:
                result.packages.extend(collector.collect())
            except CollectorError as exc:
                result.errors[collector.ecosystem] = exc
        return result