# tests/test_analyzers/test_orphans.py
"""
100 % coverage tests for env_audit.analyzers.orphans.

Strategy
--------
* ``OrphanedBinaryFinding`` is a frozen dataclass: its constructor,
  ``to_dict()``, immutability, and inheritance are all verified directly.
* ``OrphanedBinaryAnalyzer.analyze()`` has four meaningful branches:

    Step 1 (build managed_names)
      a) non-manual package → add pkg.name to managed set
      b) non-manual package with binary records → add binary.name too
      c) manual package in step-1 loop → ``continue``

    Step 2+3 (emit findings)
      d) non-manual package in step-2 loop → ``continue``
      e) manual binary whose name IS in managed set → skip
      f) manual binary whose name IS NOT in managed set → emit finding

* The ``sorted()`` return path is exercised by the multi-orphan test.
"""

import pytest

from env_audit.analyzers.base import Finding
from env_audit.analyzers.orphans import OrphanedBinaryAnalyzer, OrphanedBinaryFinding
from env_audit.models import BinaryRecord, Confidence, PackageRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _binary(
    name: str,
    path: str,
    is_symlink: bool = False,
) -> BinaryRecord:
    return BinaryRecord(
        name=name,
        path=path,
        confidence=Confidence.MEDIUM,
        is_symlink=is_symlink,
        symlink_target=path + ".real" if is_symlink else None,
    )


def _pkg(
    name: str,
    ecosystem: str = "apt",
    source: str = "test-src",
    binaries: list[BinaryRecord] | None = None,
) -> PackageRecord:
    return PackageRecord(
        name=name,
        ecosystem=ecosystem,
        source=source,
        binaries=binaries or [],
    )


def _manual(name: str, path: str) -> PackageRecord:
    """Convenience: a manual-ecosystem package with one binary."""
    return PackageRecord(
        name=name,
        ecosystem="manual",
        source="/usr/local/bin",
        binaries=[_binary(name, path)],
    )


# ---------------------------------------------------------------------------
# OrphanedBinaryFinding
# ---------------------------------------------------------------------------


class TestOrphanedBinaryFinding:
    def test_stores_all_fields(self) -> None:
        f = OrphanedBinaryFinding(
            severity="info",
            message="Binary 'mytool' has no known owner",
            binary_name="mytool",
            path="/usr/local/bin/mytool",
        )
        assert f.severity == "info"
        assert f.binary_name == "mytool"
        assert f.path == "/usr/local/bin/mytool"

    def test_to_dict_includes_kind_key(self) -> None:
        f = OrphanedBinaryFinding(
            severity="info",
            message="test",
            binary_name="mytool",
            path="/usr/local/bin/mytool",
        )
        assert f.to_dict()["kind"] == "orphaned_binary"

    def test_to_dict_includes_all_fields(self) -> None:
        f = OrphanedBinaryFinding(
            severity="info",
            message="the message",
            binary_name="mytool",
            path="/usr/local/bin/mytool",
        )
        d = f.to_dict()
        assert d["severity"] == "info"
        assert d["message"] == "the message"
        assert d["binary_name"] == "mytool"
        assert d["path"] == "/usr/local/bin/mytool"

    def test_frozen_raises_on_attribute_assignment(self) -> None:
        f = OrphanedBinaryFinding(
            severity="info", message="test", binary_name="x", path="/bin/x"
        )
        with pytest.raises(AttributeError):
            f.binary_name = "y"  # type: ignore[misc]

    def test_is_finding_subclass(self) -> None:
        f = OrphanedBinaryFinding(
            severity="info", message="test", binary_name="x", path="/bin/x"
        )
        assert isinstance(f, Finding)


# ---------------------------------------------------------------------------
# OrphanedBinaryAnalyzer — no-finding cases
# ---------------------------------------------------------------------------


class TestOrphanedBinaryAnalyzerNoFindings:
    def test_empty_packages_returns_empty_list(self) -> None:
        assert OrphanedBinaryAnalyzer().analyze([]) == []

    def test_no_manual_packages_returns_empty(self) -> None:
        packages = [_pkg("vim", "apt"), _pkg("click", "pip")]
        assert OrphanedBinaryAnalyzer().analyze(packages) == []

    def test_manual_binary_matching_non_manual_package_name_not_flagged(self) -> None:
        """
        Step 1 branch: ``managed_names.add(pkg.name)`` protects by name.
        A manual binary named 'git' is suppressed because an apt package
        is also named 'git' — even if that apt package has no binary records.
        """
        packages = [
            _pkg("git", "apt"),                          # no binary records
            _manual("git", "/usr/local/bin/git"),
        ]
        assert OrphanedBinaryAnalyzer().analyze(packages) == []

    def test_manual_binary_matching_explicit_binary_record_not_flagged(self) -> None:
        """
        Step 1 branch: ``managed_names.add(binary.name)`` for non-manual
        packages that carry explicit binary records.
        """
        packages = [
            _pkg(
                "git-core",
                "apt",
                binaries=[_binary("git", "/usr/bin/git")],
            ),
            _manual("git", "/usr/local/bin/git"),
        ]
        assert OrphanedBinaryAnalyzer().analyze(packages) == []

    def test_manual_package_with_empty_binaries_produces_no_findings(self) -> None:
        """A manual record with no binary entries produces no findings."""
        pkg = PackageRecord(
            name="ghost-tool",
            ecosystem="manual",
            source="/usr/local/bin",
            binaries=[],
        )
        assert OrphanedBinaryAnalyzer().analyze([pkg]) == []


# ---------------------------------------------------------------------------
# OrphanedBinaryAnalyzer — finding cases
# ---------------------------------------------------------------------------


class TestOrphanedBinaryAnalyzerFindings:
    def test_unmanaged_manual_binary_produces_finding(self) -> None:
        packages = [_manual("my-custom-tool", "/usr/local/bin/my-custom-tool")]
        findings = OrphanedBinaryAnalyzer().analyze(packages)
        assert len(findings) == 1

    def test_finding_type_is_orphaned_binary_finding(self) -> None:
        packages = [_manual("mytool", "/usr/local/bin/mytool")]
        finding = OrphanedBinaryAnalyzer().analyze(packages)[0]
        assert isinstance(finding, OrphanedBinaryFinding)

    def test_finding_has_info_severity(self) -> None:
        packages = [_manual("mytool", "/usr/local/bin/mytool")]
        finding = OrphanedBinaryAnalyzer().analyze(packages)[0]
        assert finding.severity == "info"

    def test_finding_binary_name_correct(self) -> None:
        packages = [_manual("mytool", "/usr/local/bin/mytool")]
        finding = OrphanedBinaryAnalyzer().analyze(packages)[0]
        assert isinstance(finding, OrphanedBinaryFinding)
        assert finding.binary_name == "mytool"

    def test_finding_path_correct(self) -> None:
        packages = [_manual("mytool", "/usr/local/bin/mytool")]
        finding = OrphanedBinaryAnalyzer().analyze(packages)[0]
        assert isinstance(finding, OrphanedBinaryFinding)
        assert finding.path == "/usr/local/bin/mytool"

    def test_finding_message_contains_binary_name_and_path(self) -> None:
        packages = [_manual("mytool", "/usr/local/bin/mytool")]
        finding = OrphanedBinaryAnalyzer().analyze(packages)[0]
        assert "mytool" in finding.message
        assert "/usr/local/bin/mytool" in finding.message

    def test_multiple_orphans_sorted_by_binary_name_then_path(self) -> None:
        packages = [
            _manual("zzz-tool", "/usr/local/bin/zzz-tool"),
            _manual("aaa-tool", "/usr/local/bin/aaa-tool"),
            _manual("mmm-tool", "/usr/local/bin/mmm-tool"),
        ]
        findings = OrphanedBinaryAnalyzer().analyze(packages)
        assert len(findings) == 3
        names = [f.binary_name for f in findings if isinstance(f, OrphanedBinaryFinding)]
        assert names == ["aaa-tool", "mmm-tool", "zzz-tool"]

    def test_sorted_by_path_when_binary_name_equal(self) -> None:
        """Two manual records with the same binary name but different paths."""
        packages = [
            PackageRecord(
                name="mytool",
                ecosystem="manual",
                source="/home/user/bin",
                binaries=[_binary("mytool", "/home/user/bin/mytool")],
            ),
            PackageRecord(
                name="mytool",
                ecosystem="manual",
                source="/usr/local/bin",
                binaries=[_binary("mytool", "/usr/local/bin/mytool")],
            ),
        ]
        findings = OrphanedBinaryAnalyzer().analyze(packages)
        assert len(findings) == 2
        paths = [f.path for f in findings if isinstance(f, OrphanedBinaryFinding)]
        assert paths == sorted(paths)

    def test_mix_of_managed_and_orphaned_binaries(self) -> None:
        """Only the unmanaged binary produces a finding."""
        packages = [
            _pkg("git", "apt"),                              # manages 'git' by name
            _manual("git", "/usr/local/bin/git"),            # → managed, no finding
            _manual("my-custom", "/usr/local/bin/my-custom"),  # → orphan, finding
        ]
        findings = OrphanedBinaryAnalyzer().analyze(packages)
        assert len(findings) == 1
        assert isinstance(findings[0], OrphanedBinaryFinding)
        assert findings[0].binary_name == "my-custom"

    def test_non_manual_package_in_step2_loop_is_skipped(self) -> None:
        """
        Step 2+3: the ``if pkg.ecosystem != "manual": continue`` branch.
        Even if a non-manual package has binaries, it must not generate
        orphan findings.
        """
        packages = [
            _pkg(
                "git-core",
                "apt",
                binaries=[_binary("git", "/usr/bin/git")],
            ),
        ]
        # No manual packages at all → no findings
        assert OrphanedBinaryAnalyzer().analyze(packages) == []