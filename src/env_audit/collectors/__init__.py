# src/env_audit/collectors/__init__.py
"""
Package collectors for env-audit-poc.

Each collector is responsible for a single ecosystem.  Import the
concrete collectors directly; this module re-exports the public API
and the exception hierarchy so callers need only one import path.
"""

from .apt import AptCollector
from .base import (
    Collector,
    CollectorError,
    CollectorParseError,
    CollectorTimeoutError,
    CollectorUnavailableError,
)

__all__ = [
    # concrete collectors
    "AptCollector",
    # base / ABC
    "Collector",
    # exceptions
    "CollectorError",
    "CollectorParseError",
    "CollectorTimeoutError",
    "CollectorUnavailableError",
]