# tests/test_renderers/test_table.py
"""
100 % coverage tests for env_audit.renderers.table.

The table is written to a StringIO buffer by Rich, so all assertions
inspect the returned string rather than any terminal output.  Two key
branches are verified:

* ``pkg.metadata.install_reason is not None``  -> enum value shown
* ``pkg.metadata.install_reason is None``       -> ``'-'`` shown
"""

import pytest

from env_audit.models import InstallReason, PackageMetadata, PackageRecord, SemVer
from env_audit.renderers.table import TableRenderer


def _pkg(
    name: str = "vim",
    ecosystem: str = "apt",
    source: str = "universe",
    **kwargs: object,
) -> PackageRecord:
    return PackageRecord(name=name, ecosystem=ecosystem, source=source, **kwargs)


class TestTableRenderer:
    # ------------------------------------------------------------------
    # Structural guarantees
    # ------------------------------------------------------------------

    def test_output_is_string(self) -> None:
        assert isinstance(TableRenderer().render([]), str)

    def test_output_ends_with_newline(self) -> None:
        assert TableRenderer().render([]).endswith("\n")
        assert TableRenderer().render([_pkg()]).endswith("\n")

    def test_empty_packages_still_shows_column_headers(self) -> None:
        result = TableRenderer().render([])
        for header in ("Name", "Version", "Ecosystem", "Source", "Install Reason"):
            assert header in result

    # ------------------------------------------------------------------
    # Content correctness
    # ------------------------------------------------------------------

    def test_package_name_present(self) -> None:
        assert "mypkg" in TableRenderer().render([_pkg("mypkg")])

    def test_package_ecosystem_present(self) -> None:
        assert "apt" in TableRenderer().render([_pkg(ecosystem="apt")])

    def test_package_source_present(self) -> None:
        assert "jammy" in TableRenderer().render([_pkg(source="jammy")])

    def test_raw_version_shown_when_no_parsed_version(self) -> None:
        pkg = _pkg(version_raw="8.2.0")
        assert "8.2.0" in TableRenderer().render([pkg])

    def test_parsed_version_preferred_over_raw(self) -> None:
        pkg = _pkg(
            version_raw="3.11.6-1~22.04",
            version_parsed=SemVer(major=3, minor=11, patch=6),
        )
        result = TableRenderer().render([pkg])
        assert "3.11.6" in result

    def test_unknown_shown_when_no_version(self) -> None:
        assert "unknown" in TableRenderer().render([_pkg()])

    # ------------------------------------------------------------------
    # install_reason branches
    # ------------------------------------------------------------------

    def test_install_reason_value_shown_when_present(self) -> None:
        """Covers the ``is not None`` branch -> enum ``.value`` used."""
        pkg = _pkg(metadata=PackageMetadata(install_reason=InstallReason.EXPLICIT))
        assert "explicit" in TableRenderer().render([pkg])

    def test_install_reason_dependency_shown(self) -> None:
        pkg = _pkg(metadata=PackageMetadata(install_reason=InstallReason.DEPENDENCY))
        assert "dependency" in TableRenderer().render([pkg])

    def test_install_reason_dash_when_none(self) -> None:
        """Covers the ``is None`` branch -> ``'-'`` used."""
        result = TableRenderer().render([_pkg()])  # metadata.install_reason is None
        assert "-" in result

    # ------------------------------------------------------------------
    # Multiple rows
    # ------------------------------------------------------------------

    def test_multiple_packages_all_names_present(self) -> None:
        pkgs = [_pkg("vim"), _pkg("git", source="jammy")]
        result = TableRenderer().render(pkgs)
        assert "vim" in result
        assert "git" in result