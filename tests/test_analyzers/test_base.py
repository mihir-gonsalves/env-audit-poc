# tests/test_analyzers/test_base.py
"""
100 % coverage tests for env_audit.analyzers.base.

Strategy
--------
* A minimal ``ConcreteAnalyzer`` subclass satisfies the abstract
  contract and lets us verify the ABC machinery.
* ``Finding`` is a frozen dataclass; both its data contract and its
  immutability are tested directly.
* Every line and branch in base.py is exercised.
"""

import pytest

from env_audit.analyzers.base import Analyzer, Finding
from env_audit.models import PackageRecord


# ---------------------------------------------------------------------------
# Minimal concrete subclass
# ---------------------------------------------------------------------------


class ConcreteAnalyzer(Analyzer):
    """Trivial implementation: returns one fixed finding per call."""

    def analyze(self, packages: list[PackageRecord]) -> list[Finding]:
        return [Finding(severity="info", message="test-finding")]


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------


class TestFinding:
    def test_stores_severity_and_message(self) -> None:
        f = Finding(severity="warning", message="something wrong")
        assert f.severity == "warning"
        assert f.message == "something wrong"

    def test_to_dict_returns_all_fields(self) -> None:
        f = Finding(severity="info", message="ok")
        assert f.to_dict() == {"severity": "info", "message": "ok"}

    def test_to_dict_returns_new_dict_each_call(self) -> None:
        f = Finding(severity="info", message="ok")
        d1 = f.to_dict()
        d2 = f.to_dict()
        assert d1 == d2
        assert d1 is not d2  # separate dict objects

    def test_equality_based_on_values(self) -> None:
        f1 = Finding(severity="info", message="x")
        f2 = Finding(severity="info", message="x")
        assert f1 == f2

    def test_inequality_when_fields_differ(self) -> None:
        f1 = Finding(severity="info", message="a")
        f2 = Finding(severity="warning", message="a")
        assert f1 != f2

    def test_frozen_raises_on_attribute_assignment(self) -> None:
        f = Finding(severity="info", message="test")
        with pytest.raises(AttributeError):
            f.severity = "error"  # type: ignore[misc]

    def test_hashable(self) -> None:
        """Frozen dataclasses are hashable; usable in sets and as dict keys."""
        f1 = Finding(severity="info", message="x")
        f2 = Finding(severity="info", message="x")
        assert hash(f1) == hash(f2)
        assert len({f1, f2}) == 1


# ---------------------------------------------------------------------------
# Analyzer ABC
# ---------------------------------------------------------------------------


class TestAnalyzerABC:
    def test_cannot_instantiate_abstract_class(self) -> None:
        with pytest.raises(TypeError):
            Analyzer()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self) -> None:
        a = ConcreteAnalyzer()
        assert isinstance(a, Analyzer)

    def test_analyze_with_packages(self) -> None:
        pkg = PackageRecord(name="vim", ecosystem="apt", source="universe")
        findings = ConcreteAnalyzer().analyze([pkg])
        assert len(findings) == 1
        assert findings[0].severity == "info"
        assert findings[0].message == "test-finding"

    def test_analyze_with_empty_list(self) -> None:
        """analyze() must accept an empty package list without raising."""
        findings = ConcreteAnalyzer().analyze([])
        assert len(findings) == 1  # ConcreteAnalyzer always returns one finding

    def test_analyze_returns_list_of_findings(self) -> None:
        findings = ConcreteAnalyzer().analyze([])
        assert isinstance(findings, list)
        assert all(isinstance(f, Finding) for f in findings)