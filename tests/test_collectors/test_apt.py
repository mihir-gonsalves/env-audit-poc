# tests/test_collectors/test_apt.py
"""
100 % coverage tests for env_audit.collectors.apt.

Design principles
-----------------
* All subprocess interaction is mocked — tests never touch the live system.
* The fixture file (tests/fixtures/apt/ubuntu-22.04.txt) represents real
  ``apt list --installed`` output and exercises the full parsing path.
* Each conditional branch in collect(), _parse(), and _try_parse_semver()
  gets at least one dedicated test.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from env_audit.collectors.apt import AptCollector, _LINE_RE, _VERSION_RE
from env_audit.collectors.base import (
    CollectorParseError,
    CollectorTimeoutError,
    CollectorUnavailableError,
)
from env_audit.models import InstallReason, SemVer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "apt" / "ubuntu-22.04.txt"


def _make_result(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    """Return a mock CompletedProcess-like object."""
    r = MagicMock()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


# ---------------------------------------------------------------------------
# ecosystem property
# ---------------------------------------------------------------------------


class TestEcosystem:
    def test_returns_apt(self) -> None:
        assert AptCollector().ecosystem == "apt"


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_true_when_apt_found(self) -> None:
        with patch("env_audit.collectors.apt.shutil.which", return_value="/usr/bin/apt"):
            assert AptCollector().is_available() is True

    def test_false_when_apt_missing(self) -> None:
        with patch("env_audit.collectors.apt.shutil.which", return_value=None):
            assert AptCollector().is_available() is False


# ---------------------------------------------------------------------------
# collect() — subprocess layer
# ---------------------------------------------------------------------------


class TestCollect:
    """Tests for the subprocess-level behaviour of collect()."""

    def test_raises_unavailable_when_apt_missing(self) -> None:
        with patch("env_audit.collectors.apt.shutil.which", return_value=None):
            with pytest.raises(CollectorUnavailableError) as exc_info:
                AptCollector().collect()
            assert exc_info.value.ecosystem == "apt"
            assert "not found" in exc_info.value.reason

    def test_raises_timeout_on_subprocess_timeout(self) -> None:
        with patch("env_audit.collectors.apt.shutil.which", return_value="/usr/bin/apt"):
            with patch(
                "env_audit.collectors.apt.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="apt", timeout=30.0),
            ):
                with pytest.raises(CollectorTimeoutError) as exc_info:
                    AptCollector().collect()
                assert exc_info.value.ecosystem == "apt"
                assert exc_info.value.timeout == 30.0

    def test_raises_parse_error_on_non_zero_exit(self) -> None:
        with patch("env_audit.collectors.apt.shutil.which", return_value="/usr/bin/apt"):
            with patch(
                "env_audit.collectors.apt.subprocess.run",
                return_value=_make_result(stdout="", stderr="E: some error", returncode=1),
            ):
                with pytest.raises(CollectorParseError) as exc_info:
                    AptCollector().collect()
                assert exc_info.value.ecosystem == "apt"
                assert "status 1" in exc_info.value.detail
                assert "some error" in exc_info.value.detail

    def test_success_returns_package_list(self) -> None:
        fixture = FIXTURE_PATH.read_text()
        with patch("env_audit.collectors.apt.shutil.which", return_value="/usr/bin/apt"):
            with patch(
                "env_audit.collectors.apt.subprocess.run",
                return_value=_make_result(stdout=fixture),
            ):
                records = AptCollector().collect()
        assert len(records) > 0
        names = [r.name for r in records]
        assert "python3.11" in names
        assert "vim" in names

    def test_subprocess_called_with_lang_c(self) -> None:
        """Verify LANG=C is injected into the subprocess environment."""
        fixture = FIXTURE_PATH.read_text()
        with patch("env_audit.collectors.apt.shutil.which", return_value="/usr/bin/apt"):
            with patch(
                "env_audit.collectors.apt.subprocess.run",
                return_value=_make_result(stdout=fixture),
            ) as mock_run:
                AptCollector().collect()
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["env"]["LANG"] == "C"

    def test_timeout_passed_to_subprocess(self) -> None:
        fixture = FIXTURE_PATH.read_text()
        with patch("env_audit.collectors.apt.shutil.which", return_value="/usr/bin/apt"):
            with patch(
                "env_audit.collectors.apt.subprocess.run",
                return_value=_make_result(stdout=fixture),
            ) as mock_run:
                AptCollector().collect()
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["timeout"] == AptCollector.DEFAULT_TIMEOUT


# ---------------------------------------------------------------------------
# _parse() — output parsing
# ---------------------------------------------------------------------------


class TestParse:
    """Tests for AptCollector._parse(), called directly with raw strings."""

    def _parse(self, text: str):
        return AptCollector()._parse(text)

    def test_empty_string_returns_empty_list(self) -> None:
        assert self._parse("") == []

    def test_listing_header_is_skipped(self) -> None:
        assert self._parse("Listing... Done") == []

    def test_blank_lines_are_skipped(self) -> None:
        assert self._parse("\n\n   \n") == []

    def test_malformed_line_is_skipped(self) -> None:
        # No slash — does not match _LINE_RE
        assert self._parse("this is not valid apt output") == []

    def test_explicit_install_sets_reason(self) -> None:
        line = "wget/jammy 1.21.2-2ubuntu1 amd64 [installed]\n"
        records = self._parse(line)
        assert len(records) == 1
        assert records[0].metadata.install_reason == InstallReason.EXPLICIT

    def test_automatic_install_sets_dependency_reason(self) -> None:
        line = "apt/jammy-updates,jammy-security 2.4.12 amd64 [installed,automatic]\n"
        records = self._parse(line)
        assert len(records) == 1
        assert records[0].metadata.install_reason == InstallReason.DEPENDENCY

    def test_source_is_first_repo_only(self) -> None:
        """Comma-separated sources; only the first should be stored."""
        line = "apt/jammy-updates,jammy-security 2.4.12 amd64 [installed,automatic]\n"
        records = self._parse(line)
        assert records[0].source == "jammy-updates"

    def test_single_source_without_comma(self) -> None:
        line = "wget/jammy 1.21.2-2ubuntu1 amd64 [installed]\n"
        records = self._parse(line)
        assert records[0].source == "jammy"

    def test_ecosystem_is_apt(self) -> None:
        line = "wget/jammy 1.21.2-2ubuntu1 amd64 [installed]\n"
        records = self._parse(line)
        assert records[0].ecosystem == "apt"

    def test_architecture_stored_in_extensions(self) -> None:
        line = "wget/jammy 1.21.2-2ubuntu1 amd64 [installed]\n"
        records = self._parse(line)
        assert records[0].metadata.extensions["apt:architecture"] == "amd64"

    def test_version_raw_preserved(self) -> None:
        line = "vim/jammy-updates,jammy-security 2:8.2.3995-1ubuntu2.17 amd64 [installed]\n"
        records = self._parse(line)
        assert records[0].version_raw == "2:8.2.3995-1ubuntu2.17"

    def test_parseable_version_sets_version_parsed(self) -> None:
        line = "wget/jammy 1.21.2-2ubuntu1 amd64 [installed]\n"
        records = self._parse(line)
        assert records[0].version_parsed is not None
        assert records[0].version_parsed.major == 1
        assert records[0].version_parsed.minor == 21
        assert records[0].version_parsed.patch == 2

    def test_unparseable_version_leaves_version_parsed_none(self) -> None:
        # adduser 3.118ubuntu5 cannot be mapped to SemVer (no separator before ubuntu)
        line = "adduser/jammy,now 3.118ubuntu5 all [installed]\n"
        records = self._parse(line)
        assert records[0].version_raw == "3.118ubuntu5"
        assert records[0].version_parsed is None

    def test_fixture_file_round_trip(self) -> None:
        """Parse the complete fixture; spot-check a representative subset."""
        output = FIXTURE_PATH.read_text()
        records = self._parse(output)

        by_name = {r.name: r for r in records}

        # python3.11 — explicit, parseable version, tilde prerelease
        p311 = by_name["python3.11"]
        assert p311.ecosystem == "apt"
        assert p311.source == "jammy-updates"
        assert p311.metadata.install_reason == InstallReason.EXPLICIT
        assert p311.version_parsed == SemVer(major=3, minor=11, patch=6, prerelease="1~22.04")

        # apt — automatic
        apt_pkg = by_name["apt"]
        assert apt_pkg.metadata.install_reason == InstallReason.DEPENDENCY
        assert apt_pkg.version_parsed == SemVer(major=2, minor=4, patch=12)

        # vim — epoch version
        vim = by_name["vim"]
        assert vim.version_raw == "2:8.2.3995-1ubuntu2.17"
        assert vim.version_parsed == SemVer(
            major=8, minor=2, patch=3995, prerelease="1ubuntu2.17"
        )

        # adduser — unparseable version
        adduser = by_name["adduser"]
        assert adduser.version_parsed is None

        # zlib1g — epoch + dfsg (unparseable, dot after patch)
        zlib = by_name["zlib1g"]
        assert zlib.version_parsed is None

        # All records use the apt ecosystem
        assert all(r.ecosystem == "apt" for r in records)

    def test_header_mixed_with_data(self) -> None:
        text = "Listing... Done\nwget/jammy 1.21.2-2ubuntu1 amd64 [installed]\n"
        records = self._parse(text)
        assert len(records) == 1
        assert records[0].name == "wget"


# ---------------------------------------------------------------------------
# _try_parse_semver() — version parsing
# ---------------------------------------------------------------------------


class TestTryParseSemver:
    """Exhaustive branch coverage for _try_parse_semver."""

    def _p(self, v: str):
        return AptCollector()._try_parse_semver(v)

    # --- Successful parses ---

    def test_simple_three_part(self) -> None:
        sv = self._p("2.4.12")
        assert sv == SemVer(major=2, minor=4, patch=12)

    def test_with_dash_prerelease(self) -> None:
        sv = self._p("1.21.2-2ubuntu1")
        assert sv == SemVer(major=1, minor=21, patch=2, prerelease="2ubuntu1")

    def test_with_tilde_prerelease(self) -> None:
        sv = self._p("3.10.6-1~22.04")
        assert sv == SemVer(major=3, minor=10, patch=6, prerelease="1~22.04")

    def test_with_epoch_stripped(self) -> None:
        sv = self._p("2:8.2.3995-1ubuntu2.17")
        assert sv == SemVer(major=8, minor=2, patch=3995, prerelease="1ubuntu2.17")

    def test_major_only(self) -> None:
        """A version string with just a major component."""
        sv = self._p("3")
        assert sv == SemVer(major=3, minor=0, patch=0)

    def test_major_minor_only(self) -> None:
        sv = self._p("3.11")
        assert sv == SemVer(major=3, minor=11, patch=0)

    def test_major_minor_only_with_prerelease(self) -> None:
        sv = self._p("5.1-6ubuntu1")
        assert sv == SemVer(major=5, minor=1, patch=0, prerelease="6ubuntu1")

    def test_epoch_only_version(self) -> None:
        """Epoch with a single-component version number."""
        sv = self._p("1:2")
        assert sv == SemVer(major=2, minor=0, patch=0)

    def test_prerelease_is_none_when_absent(self) -> None:
        sv = self._p("2.4.12")
        assert sv.prerelease is None

    # --- Failures that return None ---

    def test_returns_none_for_non_numeric_start(self) -> None:
        assert self._p("abc") is None

    def test_returns_none_for_empty_string(self) -> None:
        assert self._p("") is None

    def test_returns_none_for_version_with_dot_after_patch(self) -> None:
        # dfsg versions: 1.2.11.dfsg-2ubuntu9 — dot after patch before prerelease
        assert self._p("1.2.11.dfsg-2ubuntu9") is None

    def test_returns_none_for_ubuntu_suffix_without_separator(self) -> None:
        # 3.118ubuntu5 — no dash/tilde before 'ubuntu'
        assert self._p("3.118ubuntu5") is None

    def test_returns_none_for_complex_dfsg_with_epoch(self) -> None:
        assert self._p("1:1.2.11.dfsg-2ubuntu9.2") is None