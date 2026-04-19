# tests/test_models/test_package.py
"""
Tests for core data models.

These tests validate that the models enforce their contracts correctly.
"""

import pytest
from pydantic import ValidationError

from env_audit.models import (
    BinaryRecord,
    Confidence,
    InstallReason,
    PackageMetadata,
    PackageRecord,
    SemVer,
)


class TestSemVer:
    """Tests for semantic version parsing and comparison."""

    def test_basic_version(self) -> None:
        """Test basic semantic version creation."""
        v = SemVer(major=1, minor=2, patch=3)
        assert str(v) == "1.2.3"

    def test_version_with_prerelease(self) -> None:
        """Test version with prerelease tag."""
        v = SemVer(major=2, minor=0, patch=0, prerelease="rc1")
        assert str(v) == "2.0.0-rc1"

    def test_version_with_build(self) -> None:
        """Test version with build metadata."""
        v = SemVer(major=1, minor=0, patch=0, build="abc123")
        assert str(v) == "1.0.0+abc123"

    def test_version_comparison(self) -> None:
        """Test semantic version ordering."""
        v1 = SemVer(major=1, minor=0, patch=0)
        v2 = SemVer(major=1, minor=1, patch=0)
        v3 = SemVer(major=2, minor=0, patch=0)

        assert v1 < v2
        assert v2 < v3
        assert v1 < v3

    def test_version_comparison_with_prerelease(self) -> None:
        """Test that prerelease versions rank below the release (SemVer spec)."""
        release = SemVer(major=1, minor=0, patch=0)
        prerelease = SemVer(major=1, minor=0, patch=0, prerelease="rc1")
        ubuntu = SemVer(major=3, minor=11, patch=4, prerelease="1ubuntu1")
        release_311 = SemVer(major=3, minor=11, patch=4)

        assert prerelease < release          # 1.0.0-rc1 < 1.0.0
        assert ubuntu < release_311          # 3.11.4-1ubuntu1 < 3.11.4
        assert not release < prerelease

    def test_version_sorting(self) -> None:
        """Test that a list of SemVer objects sorts correctly."""
        versions = [
            SemVer(major=2, minor=0, patch=0),
            SemVer(major=1, minor=0, patch=0, prerelease="alpha"),
            SemVer(major=1, minor=0, patch=0),
            SemVer(major=1, minor=1, patch=0),
        ]
        result = sorted(versions)
        assert [str(v) for v in result] == [
            "1.0.0-alpha",
            "1.0.0",
            "1.1.0",
            "2.0.0",
        ]

    def test_version_total_ordering(self) -> None:
        """Test that >=, <=, > all work (via total_ordering)."""
        v1 = SemVer(major=1, minor=0, patch=0)
        v2 = SemVer(major=2, minor=0, patch=0)

        assert v2 > v1
        assert v2 >= v1
        assert v1 <= v2
        assert v1 >= v1  # equal

    def test_version_immutable(self) -> None:
        """Test that versions are immutable."""
        v = SemVer(major=1, minor=0, patch=0)
        with pytest.raises(ValidationError):
            v.major = 2  # type: ignore

    def test_version_with_prerelease_and_build(self) -> None:
        """Test __str__ branch where both prerelease AND build are set.
        
        The existing tests cover each branch in isolation (prerelease-only,
        build-only). This test covers both if-branches executing in one call.
        """
        v = SemVer(major=1, minor=0, patch=0, prerelease="rc1", build="abc123")
        assert str(v) == "1.0.0-rc1+abc123"

    def test_version_eq_non_semver(self) -> None:
        """Test __eq__ NotImplemented guard for non-SemVer objects.
        
        Closes the `return NotImplemented` branch in __eq__. Calling the
        dunder directly is the only reliable way to exercise this line —
        Python's == operator may short-circuit before reaching it.
        """
        v = SemVer(major=1, minor=0, patch=0)
        assert v.__eq__("1.0.0") is NotImplemented
        assert v.__eq__(1) is NotImplemented

    def test_version_lt_non_semver(self) -> None:
        """Test __lt__ NotImplemented guard for non-SemVer objects.
        
        Closes the `return NotImplemented` branch in __lt__.
        """
        v = SemVer(major=1, minor=0, patch=0)
        assert v.__lt__("2.0.0") is NotImplemented  # type: ignore[arg-type]

    def test_prerelease_lexicographic_order(self) -> None:
        """Test __lt__ final branch: both sides have prerelease with equal cores.
        
        The existing prerelease tests only cover release vs. prerelease. This
        test exercises the final `return (self.prerelease or "") < (other.prerelease
        or "")` line, which is only reached when both versions share the same
        core (major.minor.patch) and both carry a prerelease label.
        """
        alpha = SemVer(major=1, minor=0, patch=0, prerelease="alpha")
        beta = SemVer(major=1, minor=0, patch=0, prerelease="beta")
        rc1 = SemVer(major=1, minor=0, patch=0, prerelease="rc1")

        assert alpha < beta
        assert beta < rc1
        assert not beta < alpha

    def test_version_hashable(self) -> None:
        """Test that __hash__ is exercised so its body appears in coverage.
        
        SemVer overrides __eq__, which in Python 3 suppresses the inherited
        __hash__ unless you also define it. This test verifies both that the
        method runs and that equal versions produce equal hashes (required by
        the hash/eq contract), and that SemVer instances can be stored in sets
        and used as dict keys.
        """
        v1 = SemVer(major=1, minor=0, patch=0)
        v2 = SemVer(major=1, minor=0, patch=0)
        v3 = SemVer(major=2, minor=0, patch=0)

        # Equal objects must have equal hashes
        assert hash(v1) == hash(v2)

        # Usable as dict keys and set members
        seen = {v1, v2, v3}
        assert len(seen) == 2  # v1 and v2 are the same logical version
        
        version_map = {v1: "first", v3: "third"}
        assert version_map[v2] == "first"  # v2 looks up the same bucket as v1


class TestPackageRecord:
    """Tests for package record validation."""

    def test_minimal_package(self) -> None:
        """Test creating a minimal package record."""
        pkg = PackageRecord(
            name="python3",
            ecosystem="apt",
            source="universe",
        )
        assert pkg.name == "python3"
        assert pkg.ecosystem == "apt"
        assert pkg.version_raw is None
        assert pkg.binaries == []

    def test_full_package(self) -> None:
        """Test creating a complete package record."""
        pkg = PackageRecord(
            name="python3.11",
            version_raw="3.11.4-1ubuntu1",
            version_parsed=SemVer(major=3, minor=11, patch=4),
            ecosystem="apt",
            source="universe",
            install_path="/usr/lib/python3.11",
            binaries=[
                BinaryRecord(
                    name="python3.11",
                    path="/usr/bin/python3.11",
                    confidence=Confidence.HIGH,
                    is_symlink=False,
                )
            ],
            metadata=PackageMetadata(
                install_reason=InstallReason.EXPLICIT,
                size_bytes=5_242_880,
            ),
        )
        assert pkg.name == "python3.11"
        assert pkg.version_parsed.major == 3
        assert len(pkg.binaries) == 1

    def test_ecosystem_normalized_to_lowercase(self) -> None:
        """Test that ecosystem names are normalized to lowercase."""
        pkg = PackageRecord(name="test", ecosystem="APT", source="universe")
        assert pkg.ecosystem == "apt"

    def test_display_version_prefers_parsed(self) -> None:
        """Test that display_version prefers parsed over raw."""
        pkg = PackageRecord(
            name="test",
            version_raw="1.2.3-ubuntu1",
            version_parsed=SemVer(major=1, minor=2, patch=3),
            ecosystem="apt",
            source="universe",
        )
        assert pkg.display_version() == "1.2.3"

    def test_display_version_falls_back_to_raw(self) -> None:
        """Test that display_version uses raw when parsed is unavailable."""
        pkg = PackageRecord(
            name="test",
            version_raw="weird-version-string",
            ecosystem="apt",
            source="universe",
        )
        assert pkg.display_version() == "weird-version-string"

    def test_display_version_unknown_when_no_version(self) -> None:
        """Test that display_version shows 'unknown' when no version available."""
        pkg = PackageRecord(name="test", ecosystem="manual", source="~/bin")
        assert pkg.display_version() == "unknown"

    def test_package_immutable(self) -> None:
        """Test that package records are immutable."""
        pkg = PackageRecord(name="test", ecosystem="apt", source="universe")
        with pytest.raises(ValidationError):
            pkg.name = "changed"  # type: ignore

    def test_source_cannot_be_blank(self) -> None:
        """Test that a blank source string is rejected."""
        with pytest.raises(ValidationError, match="source must not be blank"):
            PackageRecord(name="test", ecosystem="apt", source="   ")

    def test_json_serialization(self) -> None:
        """Test that packages can be serialized to JSON."""
        pkg = PackageRecord(
            name="test",
            version_raw="1.0.0",
            ecosystem="apt",
            source="universe",
        )
        json_str = pkg.model_dump_json()
        assert "test" in json_str
        assert "apt" in json_str

    def test_json_deserialization(self) -> None:
        """Test that packages can be deserialized from JSON."""
        json_data = {
            "name": "test",
            "version_raw": "1.0.0",
            "version_parsed": None,
            "ecosystem": "apt",
            "source": "universe",
            "install_path": None,
            "binaries": [],
            "metadata": {
                "install_date": None,
                "install_reason": None,
                "size_bytes": None,
                "extensions": {},
            },
        }
        pkg = PackageRecord.model_validate(json_data)
        assert pkg.name == "test"
        assert pkg.ecosystem == "apt"