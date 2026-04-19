# tests/test_orchestrator.py
"""
100 % coverage tests for env_audit.orchestrator.

Strategy
--------
* A minimal ``MockCollector`` subclass makes it easy to configure
  success, errors, or any combination without touching the real system.
* Every code path in ``Orchestrator.run()`` is exercised:
  - Empty collector list (no loop body executed).
  - Single successful collector (``packages.extend`` branch).
  - Single failing collector (``CollectorError`` catch branch).
  - Mix of both (proves independence — one failure does not stop others).
* ``AuditResult`` defaults are verified directly.
"""

import pytest

from env_audit.collectors.base import (
    Collector,
    CollectorError,
    CollectorParseError,
    CollectorTimeoutError,
    CollectorUnavailableError,
)
from env_audit.models import PackageRecord
from env_audit.orchestrator import AuditResult, Orchestrator


# ---------------------------------------------------------------------------
# Minimal concrete collector used throughout
# ---------------------------------------------------------------------------


class MockCollector(Collector):
    """Configurable stub that either returns packages or raises an error."""

    def __init__(
        self,
        ecosystem_name: str = "mock",
        packages: list[PackageRecord] | None = None,
        error: CollectorError | None = None,
    ) -> None:
        self._ecosystem = ecosystem_name
        self._packages = packages or []
        self._error = error

    @property
    def ecosystem(self) -> str:
        return self._ecosystem

    def is_available(self) -> bool:
        return True

    def collect(self) -> list[PackageRecord]:
        if self._error is not None:
            raise self._error
        return self._packages


# ---------------------------------------------------------------------------
# AuditResult dataclass
# ---------------------------------------------------------------------------


class TestAuditResult:
    def test_default_packages_is_empty_list(self) -> None:
        result = AuditResult()
        assert result.packages == []

    def test_default_errors_is_empty_dict(self) -> None:
        result = AuditResult()
        assert result.errors == {}

    def test_initialised_with_values(self) -> None:
        pkg = PackageRecord(name="vim", ecosystem="test", source="test-src")
        err = CollectorUnavailableError("test", "not found")
        result = AuditResult(packages=[pkg], errors={"test": err})
        assert result.packages == [pkg]
        assert result.errors["test"] is err


# ---------------------------------------------------------------------------
# Orchestrator.run()
# ---------------------------------------------------------------------------


class TestOrchestratorRun:
    def test_empty_collectors_returns_empty_result(self) -> None:
        result = Orchestrator([]).run()
        assert result.packages == []
        assert result.errors == {}

    def test_single_successful_collector_returns_packages(self) -> None:
        pkg = PackageRecord(name="vim", ecosystem="mock", source="test-src")
        result = Orchestrator([MockCollector(packages=[pkg])]).run()
        assert result.packages == [pkg]
        assert result.errors == {}

    def test_multiple_packages_from_one_collector(self) -> None:
        pkgs = [
            PackageRecord(name="vim", ecosystem="mock", source="s"),
            PackageRecord(name="git", ecosystem="mock", source="s"),
        ]
        result = Orchestrator([MockCollector(packages=pkgs)]).run()
        assert len(result.packages) == 2

    def test_unavailable_error_recorded_by_ecosystem(self) -> None:
        error = CollectorUnavailableError("mock", "binary not found")
        result = Orchestrator([MockCollector(error=error)]).run()
        assert result.packages == []
        assert "mock" in result.errors
        assert result.errors["mock"] is error

    def test_parse_error_recorded(self) -> None:
        error = CollectorParseError("mock", "unexpected token")
        result = Orchestrator([MockCollector(error=error)]).run()
        assert "mock" in result.errors

    def test_timeout_error_recorded(self) -> None:
        error = CollectorTimeoutError("mock", 30.0)
        result = Orchestrator([MockCollector(error=error)]).run()
        assert "mock" in result.errors
        assert isinstance(result.errors["mock"], CollectorTimeoutError)

    def test_failing_collector_does_not_stop_others(self) -> None:
        pkg = PackageRecord(name="git", ecosystem="good", source="test-src")
        error = CollectorParseError("bad", "bad output")

        result = Orchestrator([
            MockCollector("good", packages=[pkg]),
            MockCollector("bad", error=error),
        ]).run()

        assert result.packages == [pkg]
        assert "bad" in result.errors
        assert "good" not in result.errors

    def test_packages_from_multiple_successful_collectors_combined(self) -> None:
        pkg1 = PackageRecord(name="vim", ecosystem="c1", source="s1")
        pkg2 = PackageRecord(name="git", ecosystem="c2", source="s2")

        result = Orchestrator([
            MockCollector("c1", packages=[pkg1]),
            MockCollector("c2", packages=[pkg2]),
        ]).run()

        assert pkg1 in result.packages
        assert pkg2 in result.packages
        assert len(result.packages) == 2

    def test_multiple_errors_all_recorded(self) -> None:
        e1 = CollectorUnavailableError("c1", "missing")
        e2 = CollectorParseError("c2", "bad output")

        result = Orchestrator([
            MockCollector("c1", error=e1),
            MockCollector("c2", error=e2),
        ]).run()

        assert result.packages == []
        assert result.errors["c1"] is e1
        assert result.errors["c2"] is e2

    def test_packages_order_preserved_across_collectors(self) -> None:
        pkg1 = PackageRecord(name="aaa", ecosystem="c1", source="s")
        pkg2 = PackageRecord(name="bbb", ecosystem="c2", source="s")

        result = Orchestrator([
            MockCollector("c1", packages=[pkg1]),
            MockCollector("c2", packages=[pkg2]),
        ]).run()

        assert result.packages[0].name == "aaa"
        assert result.packages[1].name == "bbb"