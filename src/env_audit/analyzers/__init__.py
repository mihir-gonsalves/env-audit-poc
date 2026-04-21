# src/env_audit/analyzers/__init__.py
"""
Analysis layer for env-audit-poc.

Each analyzer takes a normalized ``list[PackageRecord]`` and returns a
list of typed ``Finding`` objects.
"""

from .base import Analyzer, Finding
from .duplicates import CrossEcosystemDuplicate, DuplicateAnalyzer
from .orphans import OrphanedBinaryAnalyzer, OrphanedBinaryFinding
from .path_shadow import PathShadowAnalyzer, ShadowedBinaryFinding

__all__ = [
    "Analyzer",
    "CrossEcosystemDuplicate",
    "DuplicateAnalyzer",
    "Finding",
    "OrphanedBinaryAnalyzer",
    "OrphanedBinaryFinding",
    "PathShadowAnalyzer",
    "ShadowedBinaryFinding",
]