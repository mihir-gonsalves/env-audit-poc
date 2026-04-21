# tests/test_analyzers/test_path_shadow.py
"""
100 % coverage tests for env_audit.analyzers.path_shadow.

Strategy
--------
* ``ShadowedBinaryFinding`` is tested for field storage, ``to_dict()``
  (including tuple-to-list conversion), immutability, and subclassing.
* ``PathShadowAnalyzer.analyze()`` branches:
    - no packages / no binaries → empty
    - single binary per name → skipped (``len(entries) < 2`` branch)
    - two or more binaries → finding emitted
    - PATH rank determines the winner
    - binary whose parent dir is absent from PATH → lowest rank
    - multiple findings → sorted by binary_name
* ``_path_dirs()`` branches:
    - ``self._path is not None``  → use provided string
    - ``self._path is None``      → fall back to ``os.environ["PATH"]``
    - PATH not set in environ     → returns empty list
    - empty components (double-colon) → filtered out
* ``_rank()`` branches:
    - parent directory found in path_dirs  → returns its index
    - parent directory absent              → returns len(path_dirs)

All PATH-sensitive tests use the constructor's ``path=`` parameter so they
never depend on the developer's environment.
"""

import os
from unittest.mock import patch

import pytest

from env_audit.analyzers.base import Finding
from env_audit.analyzers.path_shadow import PathShadowAnalyzer, ShadowedBinaryFinding
from env_audit.models import BinaryRecord, Confidence, PackageRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _binary(name: str, path: str) -> BinaryRecord:
    return BinaryRecord(
        name=name,
        path=path,
        confidence=Confidence.MEDIUM,
        is_symlink=False,
        symlink_target=None,
    )


def _pkg(
    name: str,
    binaries: list[BinaryRecord],
    ecosystem: str = "manual",
) -> PackageRecord:
    return PackageRecord(
        name=name,
        ecosystem=ecosystem,
        source="/usr/local/bin",
        binaries=binaries,
    )


# ---------------------------------------------------------------------------
# ShadowedBinaryFinding
# ---------------------------------------------------------------------------


class TestShadowedBinaryFinding:
    def test_stores_all_fields(self) -> None:
        f = ShadowedBinaryFinding(
            severity="warning",
            message="test message",
            binary_name="git",
            winner_path="/usr/bin/git",
            winner_package="git-core",
            shadowed_paths=("/usr/local/bin/git",),
        )
        assert f.severity == "warning"
        assert f.binary_name == "git"
        assert f.winner_path == "/usr/bin/git"
        assert f.winner_package == "git-core"
        assert f.shadowed_paths == ("/usr/local/bin/git",)

    def test_to_dict_includes_kind_key(self) -> None:
        f = ShadowedBinaryFinding(
            severity="warning",
            message="test",
            binary_name="git",
            winner_path="/usr/bin/git",
            winner_package="git-core",
            shadowed_paths=("/usr/local/bin/git",),
        )
        assert f.to_dict()["kind"] == "path_shadow"

    def test_to_dict_preserves_shadowed_paths_as_tuple(self) -> None:
        """dataclasses.asdict() uses type(obj)(...) so tuples stay as tuples."""
        f = ShadowedBinaryFinding(
            severity="warning",
            message="test",
            binary_name="git",
            winner_path="/usr/bin/git",
            winner_package="git-core",
            shadowed_paths=("/usr/local/bin/git", "/home/user/bin/git"),
        )
        d = f.to_dict()
        assert isinstance(d["shadowed_paths"], tuple)
        assert d["shadowed_paths"] == ("/usr/local/bin/git", "/home/user/bin/git")

    def test_to_dict_includes_all_fields(self) -> None:
        f = ShadowedBinaryFinding(
            severity="warning",
            message="my message",
            binary_name="git",
            winner_path="/usr/bin/git",
            winner_package="git-core",
            shadowed_paths=("/usr/local/bin/git",),
        )
        d = f.to_dict()
        assert d["severity"] == "warning"
        assert d["message"] == "my message"
        assert d["binary_name"] == "git"
        assert d["winner_path"] == "/usr/bin/git"
        assert d["winner_package"] == "git-core"

    def test_frozen_raises_on_attribute_assignment(self) -> None:
        f = ShadowedBinaryFinding(
            severity="warning",
            message="test",
            binary_name="git",
            winner_path="/usr/bin/git",
            winner_package="git-core",
            shadowed_paths=("/usr/local/bin/git",),
        )
        with pytest.raises(AttributeError):
            f.binary_name = "other"  # type: ignore[misc]

    def test_is_finding_subclass(self) -> None:
        f = ShadowedBinaryFinding(
            severity="warning",
            message="test",
            binary_name="git",
            winner_path="/usr/bin/git",
            winner_package="git-core",
            shadowed_paths=("/usr/local/bin/git",),
        )
        assert isinstance(f, Finding)


# ---------------------------------------------------------------------------
# PathShadowAnalyzer — no-finding cases
# ---------------------------------------------------------------------------


class TestPathShadowAnalyzerNoFindings:
    def test_empty_packages_returns_empty_list(self) -> None:
        assert PathShadowAnalyzer(path="/usr/bin").analyze([]) == []

    def test_packages_with_no_binaries_returns_empty(self) -> None:
        pkg = PackageRecord(name="vim", ecosystem="apt", source="universe")
        assert PathShadowAnalyzer(path="/usr/bin").analyze([pkg]) == []

    def test_unique_binary_names_returns_empty(self) -> None:
        """Each name appears exactly once → ``len(entries) < 2`` skips all."""
        packages = [
            _pkg("vim-pkg", [_binary("vim", "/usr/bin/vim")]),
            _pkg("git-pkg", [_binary("git", "/usr/bin/git")]),
        ]
        assert PathShadowAnalyzer(path="/usr/bin").analyze(packages) == []


# ---------------------------------------------------------------------------
# PathShadowAnalyzer — finding cases
# ---------------------------------------------------------------------------


class TestPathShadowAnalyzerFindings:
    def test_two_binaries_same_name_produces_one_finding(self) -> None:
        packages = [
            _pkg("pkg-a", [_binary("git", "/usr/bin/git")]),
            _pkg("pkg-b", [_binary("git", "/usr/local/bin/git")]),
        ]
        findings = PathShadowAnalyzer(path="/usr/bin:/usr/local/bin").analyze(packages)
        assert len(findings) == 1

    def test_finding_type_is_shadowed_binary_finding(self) -> None:
        packages = [
            _pkg("a", [_binary("git", "/usr/bin/git")]),
            _pkg("b", [_binary("git", "/usr/local/bin/git")]),
        ]
        finding = PathShadowAnalyzer(path="/usr/bin:/usr/local/bin").analyze(packages)[0]
        assert isinstance(finding, ShadowedBinaryFinding)

    def test_finding_has_warning_severity(self) -> None:
        packages = [
            _pkg("a", [_binary("git", "/usr/bin/git")]),
            _pkg("b", [_binary("git", "/usr/local/bin/git")]),
        ]
        finding = PathShadowAnalyzer(path="/usr/bin:/usr/local/bin").analyze(packages)[0]
        assert finding.severity == "warning"

    def test_winner_is_earliest_in_path(self) -> None:
        """/usr/bin comes before /usr/local/bin in PATH → /usr/bin/git wins."""
        packages = [
            _pkg("pkg-a", [_binary("git", "/usr/local/bin/git")]),  # later in PATH
            _pkg("pkg-b", [_binary("git", "/usr/bin/git")]),         # earlier in PATH
        ]
        finding = PathShadowAnalyzer(path="/usr/bin:/usr/local/bin").analyze(packages)[0]
        assert isinstance(finding, ShadowedBinaryFinding)
        assert finding.winner_path == "/usr/bin/git"

    def test_shadowed_path_is_the_lower_priority_binary(self) -> None:
        packages = [
            _pkg("a", [_binary("git", "/usr/bin/git")]),
            _pkg("b", [_binary("git", "/usr/local/bin/git")]),
        ]
        finding = PathShadowAnalyzer(path="/usr/bin:/usr/local/bin").analyze(packages)[0]
        assert isinstance(finding, ShadowedBinaryFinding)
        assert finding.shadowed_paths == ("/usr/local/bin/git",)

    def test_winner_package_name_is_correct(self) -> None:
        packages = [
            _pkg("the-winner", [_binary("git", "/usr/bin/git")]),
            _pkg("the-loser",  [_binary("git", "/usr/local/bin/git")]),
        ]
        finding = PathShadowAnalyzer(path="/usr/bin:/usr/local/bin").analyze(packages)[0]
        assert isinstance(finding, ShadowedBinaryFinding)
        assert finding.winner_package == "the-winner"

    def test_message_contains_binary_name_and_winner_path(self) -> None:
        packages = [
            _pkg("a", [_binary("git", "/usr/bin/git")]),
            _pkg("b", [_binary("git", "/usr/local/bin/git")]),
        ]
        finding = PathShadowAnalyzer(path="/usr/bin:/usr/local/bin").analyze(packages)[0]
        assert "git" in finding.message
        assert "/usr/bin/git" in finding.message

    def test_binary_not_in_path_receives_lowest_rank(self) -> None:
        """/unknown/bin is not a PATH component → loses to /usr/bin."""
        packages = [
            _pkg("a", [_binary("git", "/unknown/bin/git")]),
            _pkg("b", [_binary("git", "/usr/bin/git")]),
        ]
        finding = PathShadowAnalyzer(path="/usr/bin").analyze(packages)[0]
        assert isinstance(finding, ShadowedBinaryFinding)
        assert finding.winner_path == "/usr/bin/git"
        assert "/unknown/bin/git" in finding.shadowed_paths

    def test_both_binaries_absent_from_path_still_produces_finding(self) -> None:
        """
        When neither binary's parent is in PATH both receive rank == len(path_dirs).
        sorted() is stable, so a finding is still emitted regardless of which
        path "wins" (the exact winner depends on stable sort order).
        """
        packages = [
            _pkg("a", [_binary("git", "/unknown1/git")]),
            _pkg("b", [_binary("git", "/unknown2/git")]),
        ]
        findings = PathShadowAnalyzer(path="/usr/bin").analyze(packages)
        assert len(findings) == 1
        assert isinstance(findings[0], ShadowedBinaryFinding)

    def test_three_candidates_one_winner_two_shadowed(self) -> None:
        packages = [
            _pkg("a", [_binary("git", "/usr/bin/git")]),
            _pkg("b", [_binary("git", "/usr/local/bin/git")]),
            _pkg("c", [_binary("git", "/home/user/bin/git")]),
        ]
        finding = PathShadowAnalyzer(
            path="/usr/bin:/usr/local/bin:/home/user/bin"
        ).analyze(packages)[0]
        assert isinstance(finding, ShadowedBinaryFinding)
        assert finding.winner_path == "/usr/bin/git"
        assert len(finding.shadowed_paths) == 2

    def test_multiple_findings_sorted_by_binary_name(self) -> None:
        packages = [
            _pkg("x", [_binary("zzz", "/usr/bin/zzz"), _binary("aaa", "/usr/bin/aaa")]),
            _pkg("y", [_binary("zzz", "/usr/local/bin/zzz"), _binary("aaa", "/usr/local/bin/aaa")]),
        ]
        findings = PathShadowAnalyzer(path="/usr/bin:/usr/local/bin").analyze(packages)
        assert len(findings) == 2
        names = [f.binary_name for f in findings if isinstance(f, ShadowedBinaryFinding)]
        assert names == sorted(names)
        assert names[0] == "aaa"
        assert names[1] == "zzz"

    def test_package_with_multiple_binaries_only_one_shadowed(self) -> None:
        """Only the binary name with two+ candidates triggers a finding."""
        packages = [
            _pkg("a", [_binary("git", "/usr/bin/git"), _binary("vim", "/usr/bin/vim")]),
            _pkg("b", [_binary("git", "/usr/local/bin/git")]),  # vim has no shadow
        ]
        findings = PathShadowAnalyzer(path="/usr/bin:/usr/local/bin").analyze(packages)
        assert len(findings) == 1
        assert isinstance(findings[0], ShadowedBinaryFinding)
        assert findings[0].binary_name == "git"


# ---------------------------------------------------------------------------
# _path_dirs()
# ---------------------------------------------------------------------------


class TestPathDirs:
    def test_provided_path_is_split_on_pathsep(self) -> None:
        a = PathShadowAnalyzer(path="/usr/bin:/usr/local/bin")
        assert a._path_dirs() == ["/usr/bin", "/usr/local/bin"]

    def test_empty_components_filtered_out(self) -> None:
        """Double-colon and trailing colon produce empty string components."""
        a = PathShadowAnalyzer(path="/usr/bin::/usr/local/bin:")
        dirs = a._path_dirs()
        assert "" not in dirs
        assert "/usr/bin" in dirs
        assert "/usr/local/bin" in dirs

    def test_empty_path_string_returns_empty_list(self) -> None:
        a = PathShadowAnalyzer(path="")
        assert a._path_dirs() == []

    def test_none_path_reads_from_os_environ(self) -> None:
        a = PathShadowAnalyzer(path=None)
        with patch.dict(os.environ, {"PATH": "/mock/bin:/mock/local/bin"}):
            dirs = a._path_dirs()
        assert dirs == ["/mock/bin", "/mock/local/bin"]

    def test_none_path_returns_empty_when_path_absent_from_environ(self) -> None:
        """os.environ.get("PATH", "") falls back to "" when PATH is unset."""
        a = PathShadowAnalyzer(path=None)
        env_without_path = {k: v for k, v in os.environ.items() if k != "PATH"}
        with patch.dict(os.environ, env_without_path, clear=True):
            dirs = a._path_dirs()
        assert dirs == []


# ---------------------------------------------------------------------------
# _rank()
# ---------------------------------------------------------------------------


class TestRank:
    def test_returns_index_when_parent_is_in_path(self) -> None:
        path_dirs = ["/usr/bin", "/usr/local/bin", "/home/user/bin"]
        assert PathShadowAnalyzer._rank("/usr/bin/git", path_dirs) == 0
        assert PathShadowAnalyzer._rank("/usr/local/bin/git", path_dirs) == 1
        assert PathShadowAnalyzer._rank("/home/user/bin/git", path_dirs) == 2

    def test_returns_len_when_parent_absent_from_path(self) -> None:
        path_dirs = ["/usr/bin", "/usr/local/bin"]
        assert PathShadowAnalyzer._rank("/unknown/bin/git", path_dirs) == 2

    def test_returns_zero_when_path_dirs_is_empty(self) -> None:
        """With no PATH components len([]) == 0, so the fallback rank is 0."""
        assert PathShadowAnalyzer._rank("/usr/bin/git", []) == 0