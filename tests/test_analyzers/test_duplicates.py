# tests/test_analyzers/test_duplicates.py
"""
100 % coverage tests for env_audit.analyzers.duplicates.

Strategy
--------
* ``CrossEcosystemDuplicate`` is a frozen dataclass: constructor, ``to_dict()``,
  immutability, and subclass relationship are all tested directly.
* ``DuplicateAnalyzer.analyze()`` has two branches inside its inner loop:
  ``len(unique) < 2`` (no finding) and ``len(unique) >= 2`` (emit finding).
  Both are exercised in isolation and in combination.
* Sorting, deduplication of repeated ecosystems, and multi-finding scenarios
  are each covered by dedicated tests.
"""

import pytest

from env_audit.analyzers.base import Finding
from env_audit.analyzers.duplicates import CrossEcosystemDuplicate, DuplicateAnalyzer
from env_audit.models import PackageRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pkg(name: str, ecosystem: str = "apt", source: str = "test-src") -> PackageRecord:
    return PackageRecord(name=name, ecosystem=ecosystem, source=source)


# ---------------------------------------------------------------------------
# CrossEcosystemDuplicate
# ---------------------------------------------------------------------------


class TestCrossEcosystemDuplicate:
    def test_stores_all_fields(self) -> None:
        f = CrossEcosystemDuplicate(
            severity="warning",
            message="'vim' is installed in 2 ecosystems: apt, pip",
            name="vim",
            ecosystems=("apt", "pip"),
        )
        assert f.severity == "warning"
        assert f.name == "vim"
        assert f.ecosystems == ("apt", "pip")

    def test_to_dict_includes_kind_key(self) -> None:
        f = CrossEcosystemDuplicate(
            severity="warning",
            message="test",
            name="vim",
            ecosystems=("apt", "pip"),
        )
        assert f.to_dict()["kind"] == "cross_ecosystem_duplicate"

    def test_to_dict_preserves_ecosystems_as_tuple(self) -> None:
        """dataclasses.asdict() uses type(obj)(...) so tuples stay as tuples."""
        f = CrossEcosystemDuplicate(
            severity="warning",
            message="test",
            name="vim",
            ecosystems=("apt", "pip"),
        )
        d = f.to_dict()
        assert isinstance(d["ecosystems"], tuple)
        assert d["ecosystems"] == ("apt", "pip")

    def test_to_dict_includes_all_base_fields(self) -> None:
        f = CrossEcosystemDuplicate(
            severity="warning",
            message="my message",
            name="vim",
            ecosystems=("apt", "pip"),
        )
        d = f.to_dict()
        assert d["severity"] == "warning"
        assert d["message"] == "my message"
        assert d["name"] == "vim"

    def test_frozen_raises_on_attribute_assignment(self) -> None:
        f = CrossEcosystemDuplicate(
            severity="warning",
            message="test",
            name="vim",
            ecosystems=("apt",),
        )
        with pytest.raises(AttributeError):
            f.name = "git"  # type: ignore[misc]

    def test_is_finding_subclass(self) -> None:
        f = CrossEcosystemDuplicate(
            severity="warning",
            message="test",
            name="vim",
            ecosystems=("apt", "pip"),
        )
        assert isinstance(f, Finding)

    def test_equality_based_on_field_values(self) -> None:
        f1 = CrossEcosystemDuplicate(
            severity="warning", message="m", name="vim", ecosystems=("apt", "pip")
        )
        f2 = CrossEcosystemDuplicate(
            severity="warning", message="m", name="vim", ecosystems=("apt", "pip")
        )
        assert f1 == f2

    def test_hashable_usable_in_set(self) -> None:
        f1 = CrossEcosystemDuplicate(
            severity="warning", message="m", name="vim", ecosystems=("apt", "pip")
        )
        f2 = CrossEcosystemDuplicate(
            severity="warning", message="m", name="vim", ecosystems=("apt", "pip")
        )
        assert len({f1, f2}) == 1


# ---------------------------------------------------------------------------
# DuplicateAnalyzer — no-finding cases
# ---------------------------------------------------------------------------


class TestDuplicateAnalyzerNoFindings:
    def test_empty_packages_returns_empty_list(self) -> None:
        assert DuplicateAnalyzer().analyze([]) == []

    def test_single_package_returns_empty_list(self) -> None:
        assert DuplicateAnalyzer().analyze([_pkg("vim", "apt")]) == []

    def test_unique_names_across_ecosystems_returns_empty(self) -> None:
        packages = [
            _pkg("vim", "apt"),
            _pkg("click", "pip"),
            _pkg("typescript", "npm"),
        ]
        assert DuplicateAnalyzer().analyze(packages) == []

    def test_same_name_same_ecosystem_not_flagged(self) -> None:
        """Intra-ecosystem duplicates are the normalizer's concern, not ours."""
        packages = [_pkg("vim", "apt"), _pkg("vim", "apt")]
        assert DuplicateAnalyzer().analyze(packages) == []


# ---------------------------------------------------------------------------
# DuplicateAnalyzer — finding cases
# ---------------------------------------------------------------------------


class TestDuplicateAnalyzerFindings:
    def test_same_name_two_ecosystems_produces_one_finding(self) -> None:
        packages = [_pkg("requests", "apt"), _pkg("requests", "pip")]
        findings = DuplicateAnalyzer().analyze(packages)
        assert len(findings) == 1

    def test_finding_type_is_cross_ecosystem_duplicate(self) -> None:
        packages = [_pkg("requests", "apt"), _pkg("requests", "pip")]
        finding = DuplicateAnalyzer().analyze(packages)[0]
        assert isinstance(finding, CrossEcosystemDuplicate)

    def test_finding_has_warning_severity(self) -> None:
        packages = [_pkg("requests", "apt"), _pkg("requests", "pip")]
        finding = DuplicateAnalyzer().analyze(packages)[0]
        assert finding.severity == "warning"

    def test_finding_name_matches_package_name(self) -> None:
        packages = [_pkg("requests", "apt"), _pkg("requests", "pip")]
        finding = DuplicateAnalyzer().analyze(packages)[0]
        assert isinstance(finding, CrossEcosystemDuplicate)
        assert finding.name == "requests"

    def test_finding_ecosystems_are_sorted(self) -> None:
        """Ecosystems are always returned in sorted order."""
        packages = [_pkg("vim", "pip"), _pkg("vim", "apt")]
        finding = DuplicateAnalyzer().analyze(packages)[0]
        assert isinstance(finding, CrossEcosystemDuplicate)
        assert finding.ecosystems == ("apt", "pip")

    def test_finding_ecosystems_deduplicated(self) -> None:
        """If the same ecosystem appears twice, only one entry is kept."""
        packages = [
            _pkg("vim", "apt"),
            _pkg("vim", "apt"),  # duplicate, same ecosystem
            _pkg("vim", "pip"),
        ]
        finding = DuplicateAnalyzer().analyze(packages)[0]
        assert isinstance(finding, CrossEcosystemDuplicate)
        assert finding.ecosystems == ("apt", "pip")

    def test_message_contains_name_count_and_ecosystems(self) -> None:
        packages = [_pkg("requests", "apt"), _pkg("requests", "pip")]
        finding = DuplicateAnalyzer().analyze(packages)[0]
        assert "requests" in finding.message
        assert "2" in finding.message
        assert "apt" in finding.message
        assert "pip" in finding.message

    def test_three_ecosystems_all_appear_in_finding(self) -> None:
        packages = [
            _pkg("vim", "apt"),
            _pkg("vim", "pip"),
            _pkg("vim", "npm"),
        ]
        finding = DuplicateAnalyzer().analyze(packages)[0]
        assert isinstance(finding, CrossEcosystemDuplicate)
        assert set(finding.ecosystems) == {"apt", "npm", "pip"}
        assert "3" in finding.message

    def test_multiple_duplicates_returns_one_finding_per_name(self) -> None:
        packages = [
            _pkg("vim", "apt"),
            _pkg("vim", "pip"),
            _pkg("git", "apt"),
            _pkg("git", "npm"),
        ]
        findings = DuplicateAnalyzer().analyze(packages)
        assert len(findings) == 2
        assert all(isinstance(f, CrossEcosystemDuplicate) for f in findings)

    def test_findings_sorted_by_package_name(self) -> None:
        packages = [
            _pkg("zlib", "apt"),
            _pkg("zlib", "pip"),
            _pkg("aaa", "apt"),
            _pkg("aaa", "npm"),
            _pkg("mmm", "apt"),
            _pkg("mmm", "pip"),
        ]
        findings = DuplicateAnalyzer().analyze(packages)
        names = [f.name for f in findings if isinstance(f, CrossEcosystemDuplicate)]
        assert names == sorted(names)
        assert names == ["aaa", "mmm", "zlib"]

    def test_non_duplicate_not_mixed_with_duplicates(self) -> None:
        packages = [
            _pkg("vim", "apt"),
            _pkg("vim", "pip"),   # duplicate
            _pkg("git", "apt"),   # unique
        ]
        findings = DuplicateAnalyzer().analyze(packages)
        assert len(findings) == 1
        assert isinstance(findings[0], CrossEcosystemDuplicate)
        assert findings[0].name == "vim"