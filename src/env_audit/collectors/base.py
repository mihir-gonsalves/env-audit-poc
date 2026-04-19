# src/env_audit/collectors/base.py
"""
Collector base class and exception hierarchy for env-audit-poc.
"""

from abc import ABC, abstractmethod

from env_audit.models import PackageRecord

__all__ = [
    "Collector",
    "CollectorError",
    "CollectorParseError",
    "CollectorTimeoutError",
    "CollectorUnavailableError",
]


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class CollectorError(Exception):
    """Base class for all collector errors."""


class CollectorUnavailableError(CollectorError):
    """Raised when a collector cannot run on this system."""

    def __init__(self, ecosystem: str, reason: str) -> None:
        self.ecosystem = ecosystem
        self.reason = reason
        super().__init__(f"Collector '{ecosystem}' is unavailable: {reason}")


class CollectorTimeoutError(CollectorError):
    """Raised when a collector exceeds its time budget."""

    def __init__(self, ecosystem: str, timeout: float) -> None:
        self.ecosystem = ecosystem
        self.timeout = timeout
        super().__init__(
            f"Collector '{ecosystem}' timed out after {timeout:.1f}s"
        )


class CollectorParseError(CollectorError):
    """Raised when a collector cannot parse command output."""

    def __init__(self, ecosystem: str, detail: str) -> None:
        self.ecosystem = ecosystem
        self.detail = detail
        super().__init__(
            f"Collector '{ecosystem}' failed to parse output: {detail}"
        )


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class Collector(ABC):
    """
    Abstract base class for all package collectors.

    Each collector is responsible for one ecosystem (apt, pip, npm, etc.).
    Collectors are strictly read-only — they must never modify system state.

    Error contract
    --------------
    - Raise ``CollectorUnavailableError`` from ``is_available()`` *or*
      ``collect()`` if the required tools are missing.
    - Raise ``CollectorTimeoutError`` if a subprocess exceeds its timeout.
    - Raise ``CollectorParseError`` if command output cannot be parsed.
    - Never let raw ``subprocess`` or ``OSError`` exceptions escape — wrap
      them in the appropriate ``CollectorError`` subclass.

    The orchestrator catches ``CollectorError`` and records it per-collector,
    so one broken collector never aborts the full audit.
    """

    #: Default subprocess timeout in seconds. Override in subclass if needed.
    DEFAULT_TIMEOUT: float = 30.0

    @property
    @abstractmethod
    def ecosystem(self) -> str:
        """
        Unique identifier for this collector's ecosystem.

        Must be lowercase (e.g., ``'apt'``, ``'pip'``, ``'npm'``).
        Used as the ``ecosystem`` field on every ``PackageRecord`` emitted.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """
        Return ``True`` if this collector can run on the current system.

        Should be fast and side-effect-free (e.g. check with
        ``shutil.which``).  Must not raise — return ``False`` instead.
        """

    @abstractmethod
    def collect(self) -> list[PackageRecord]:
        """
        Gather and normalise package data for this ecosystem.

        Returns a (possibly empty) list of ``PackageRecord`` objects.
        Raises a ``CollectorError`` subclass on failure.
        Never modifies system state.
        """