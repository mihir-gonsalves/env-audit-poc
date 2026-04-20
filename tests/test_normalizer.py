# tests/test_normalizer.py
"""
100 % coverage tests for env_audit.normalizer.

Strategy
--------
* Exercise every branch in ``Normalizer.normalize()`` and ``_pick_best()``.
* Verify ``NormalizerResult`` defaults.
* Confirm sorting, deduplication, and cross/intra-ecosystem duplicate
  detection all behave correctly in isolation and in combination.
"""

import pytest

from env_audit.models import PackageRecord, SemVer
from env_audit.normalizer import NormalizerResult, Normalizer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pkg(
    name: str,
    ecosystem: str = "apt",
    source: str = "test-src",
    version_parsed: SemVer | None = None,
    version_raw: str | None = None,
) -> PackageRecord:
    return PackageRecord(
        name=name,
        ecosystem=ecosystem,
        source=source,
        version_parsed=version_parsed,
        version_raw=version_raw,
    )


def _versioned(name: str, major: int, minor: int = 0, patch: int = 0, ecosystem: str = "apt") -> PackageRecord:
    sv = SemVer(major=major, minor=minor, patch=patch)
    return _pkg(name, ecosystem=ecosystem, version_parsed=sv, version_raw=f"{major}.{minor}.{patch}")


# ---------------------------------------------------------------------------
# NormalizerResult defaults
# ---------------------------------------------------------------------------


class TestNormalizerResult:
    def test_default_packages_is_empty_list(self) -> None:
        r = NormalizerResult()
        assert r.packages == []

    def test_default_cross_ecosystem_duplicates_is_empty_dict(self) -> None:
        r = NormalizerResult()
        assert r.cross_ecosystem_duplicates == {}

    def test_default_intra_ecosystem_duplicates_is_empty_dict(self) -> None:
        r = NormalizerResult()
        assert r.intra_ecosystem_duplicates == {}

    def test_initialised_with_values(self) -> None:
        pkg = _pkg("vim")
        r = NormalizerResult(
            packages=[pkg],
            cross_ecosystem_duplicates={"vim": ["apt", "pip"]},
            intra_ecosystem_duplicates={("apt", "vim"): 2},
        )
        assert r.packages == [pkg]
        assert r.cross_ecosystem_duplicates == {"vim": ["apt", "pip"]}
        assert r.intra_ecosystem_duplicates == {("apt", "vim"): 2}


# ---------------------------------------------------------------------------
# Normalizer.normalize() — empty input
# ---------------------------------------------------------------------------


class TestNormalizeEmpty:
    def test_empty_list_returns_empty_result(self) -> None:
        result = Normalizer().normalize([])
        assert result.packages == []
        assert result.cross_ecosystem_duplicates == {}
        assert result.intra_ecosystem_duplicates == {}


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


class TestSorting:
    def test_sorted_by_ecosystem_then_name(self) -> None:
        packages = [
            _pkg("vim", ecosystem="apt"),
            _pkg("wget", ecosystem="apt"),
            _pkg("click", ecosystem="pip"),
            _pkg("axios", ecosystem="npm"),
        ]
        result = Normalizer().normalize(packages)
        keys = [(p.ecosystem, p.name) for p in result.packages]
        assert keys == sorted(keys)

    def test_stable_within_same_ecosystem(self) -> None:
        packages = [
            _pkg("zzz", ecosystem="apt"),
            _pkg("aaa", ecosystem="apt"),
            _pkg("mmm", ecosystem="apt"),
        ]
        result = Normalizer().normalize(packages)
        names = [p.name for p in result.packages]
        assert names == ["aaa", "mmm", "zzz"]

    def test_single_package_is_returned_unchanged(self) -> None:
        pkg = _pkg("vim")
        result = Normalizer().normalize([pkg])
        assert result.packages == [pkg]


# ---------------------------------------------------------------------------
# Intra-ecosystem deduplication
# ---------------------------------------------------------------------------


class TestIntraEcosystemDeduplication:
    def test_unique_packages_not_flagged(self) -> None:
        packages = [_versioned("vim", 9), _versioned("git", 2)]
        result = Normalizer().normalize(packages)
        assert result.intra_ecosystem_duplicates == {}

    def test_two_versions_same_ecosystem_collapsed_to_one(self) -> None:
        packages = [
            _versioned("vim", 8),
            _versioned("vim", 9),
        ]
        result = Normalizer().normalize(packages)
        assert len(result.packages) == 1

    def test_higher_version_wins(self) -> None:
        packages = [
            _versioned("vim", 8, 2, 0),
            _versioned("vim", 9, 0, 0),
            _versioned("vim", 8, 5, 0),
        ]
        result = Normalizer().normalize(packages)
        assert result.packages[0].version_parsed == SemVer(major=9, minor=0, patch=0)

    def test_intra_dupe_count_recorded(self) -> None:
        packages = [_versioned("vim", 8), _versioned("vim", 9), _versioned("vim", 7)]
        result = Normalizer().normalize(packages)
        assert result.intra_ecosystem_duplicates[("apt", "vim")] == 3

    def test_no_parseable_version_keeps_first(self) -> None:
        p1 = _pkg("oddpkg", version_raw="weird-1")
        p2 = _pkg("oddpkg", version_raw="weird-2")
        result = Normalizer().normalize([p1, p2])
        assert len(result.packages) == 1
        assert result.packages[0].version_raw == "weird-1"

    def test_mixed_parseable_and_none_prefers_parseable(self) -> None:
        """Records with a parsed version should beat those without one."""
        p_none = _pkg("vim", version_raw="unparseable")
        p_ver  = _versioned("vim", 9)
        result = Normalizer().normalize([p_none, p_ver])
        assert result.packages[0].version_parsed is not None
        assert result.packages[0].version_parsed.major == 9

    def test_different_ecosystems_not_collapsed(self) -> None:
        packages = [
            _versioned("vim", 9, ecosystem="apt"),
            _versioned("vim", 9, ecosystem="pip"),
        ]
        result = Normalizer().normalize(packages)
        assert len(result.packages) == 2

    def test_intra_dupe_count_two(self) -> None:
        packages = [_versioned("vim", 8), _versioned("vim", 9)]
        result = Normalizer().normalize(packages)
        assert result.intra_ecosystem_duplicates[("apt", "vim")] == 2

    def test_only_duped_groups_appear_in_intra_dict(self) -> None:
        packages = [
            _versioned("vim", 8),
            _versioned("vim", 9),
            _versioned("git", 2),   # only one record → not a dupe
        ]
        result = Normalizer().normalize(packages)
        assert ("apt", "vim") in result.intra_ecosystem_duplicates
        assert ("apt", "git") not in result.intra_ecosystem_duplicates


# ---------------------------------------------------------------------------
# Cross-ecosystem duplicate detection
# ---------------------------------------------------------------------------


class TestCrossEcosystemDuplicates:
    def test_no_cross_dupes_when_all_names_unique(self) -> None:
        packages = [
            _versioned("vim", 9, ecosystem="apt"),
            _versioned("click", 8, ecosystem="pip"),
            _versioned("axios", 1, ecosystem="npm"),
        ]
        result = Normalizer().normalize(packages)
        assert result.cross_ecosystem_duplicates == {}

    def test_cross_dupe_detected(self) -> None:
        packages = [
            _versioned("requests", 2, ecosystem="apt"),
            _versioned("requests", 2, ecosystem="pip"),
        ]
        result = Normalizer().normalize(packages)
        assert "requests" in result.cross_ecosystem_duplicates
        assert set(result.cross_ecosystem_duplicates["requests"]) == {"apt", "pip"}

    def test_three_ecosystem_cross_dupe(self) -> None:
        packages = [
            _versioned("vim", 9, ecosystem="apt"),
            _versioned("vim", 9, ecosystem="pip"),
            _versioned("vim", 9, ecosystem="manual"),
        ]
        result = Normalizer().normalize(packages)
        assert len(result.cross_ecosystem_duplicates["vim"]) == 3

    def test_only_multi_ecosystem_names_in_cross_dict(self) -> None:
        packages = [
            _versioned("vim", 9, ecosystem="apt"),
            _versioned("vim", 9, ecosystem="pip"),
            _versioned("git", 2, ecosystem="apt"),  # only in one ecosystem
        ]
        result = Normalizer().normalize(packages)
        assert "vim" in result.cross_ecosystem_duplicates
        assert "git" not in result.cross_ecosystem_duplicates

    def test_cross_dupe_operates_on_deduped_list(self) -> None:
        """Intra-ecosystem dupes are collapsed first; cross-dupe detection
        should see only one record per ecosystem per name."""
        packages = [
            _versioned("vim", 8, ecosystem="apt"),
            _versioned("vim", 9, ecosystem="apt"),   # collapses with above
            _versioned("vim", 2, ecosystem="pip"),
        ]
        result = Normalizer().normalize(packages)
        ecosystems = result.cross_ecosystem_duplicates["vim"]
        assert sorted(ecosystems) == ["apt", "pip"]


# ---------------------------------------------------------------------------
# _pick_best() — direct unit tests
# ---------------------------------------------------------------------------


class TestPickBest:
    def _pick(self, group):
        return Normalizer._pick_best(group)

    def test_single_element_returned_directly(self) -> None:
        pkg = _versioned("vim", 9)
        assert self._pick([pkg]) is pkg

    def test_highest_version_wins(self) -> None:
        low = _versioned("vim", 8)
        high = _versioned("vim", 9)
        assert self._pick([low, high]).version_parsed == SemVer(major=9, minor=0, patch=0)

    def test_none_versions_fall_back_to_first(self) -> None:
        p1 = _pkg("vim", version_raw="a")
        p2 = _pkg("vim", version_raw="b")
        assert self._pick([p1, p2]) is p1

    def test_mixed_prefers_versioned_over_none(self) -> None:
        p_none = _pkg("vim", version_raw="x")
        p_ver  = _versioned("vim", 1)
        assert self._pick([p_none, p_ver]).version_parsed is not None


# ---------------------------------------------------------------------------
# Combined scenarios
# ---------------------------------------------------------------------------


class TestCombinedScenarios:
    def test_full_realistic_audit(self) -> None:
        """
        Simulate a realistic audit result with packages from three collectors,
        a same-name package appearing in two ecosystems (cross-dupe), and one
        collector emitting a package twice (intra-dupe).
        """
        packages = [
            # apt
            _versioned("vim", 9, ecosystem="apt"),
            _versioned("git", 2, ecosystem="apt"),
            _versioned("python3", 3, 11, ecosystem="apt"),
            # pip — python3 also appears here (cross-dupe)
            _versioned("python3", 3, 12, ecosystem="pip"),
            _versioned("click", 8, ecosystem="pip"),
            # pip intra-dupe: click appears twice, higher version wins
            _versioned("click", 7, ecosystem="pip"),
            # npm
            _versioned("typescript", 5, ecosystem="npm"),
        ]
        result = Normalizer().normalize(packages)

        # Total unique (ecosystem, name) pairs: 7 - 1 collapsed click = 6
        assert len(result.packages) == 6

        # click should be the version 8 record
        click_pkg = next(p for p in result.packages if p.name == "click")
        assert click_pkg.version_parsed.major == 8

        # python3 cross-dupe detected
        assert "python3" in result.cross_ecosystem_duplicates
        assert set(result.cross_ecosystem_duplicates["python3"]) == {"apt", "pip"}

        # click intra-dupe recorded
        assert result.intra_ecosystem_duplicates[("pip", "click")] == 2

        # Output is sorted
        keys = [(p.ecosystem, p.name) for p in result.packages]
        assert keys == sorted(keys)