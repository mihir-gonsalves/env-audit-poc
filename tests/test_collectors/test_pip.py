# tests/test_collectors/test_pip.py
"""
100 % coverage tests for env_audit.collectors.pip.

Design principles
-----------------
* All subprocess interaction is mocked — tests never touch the live system.
* The fixture file (tests/fixtures/pip/ubuntu-22.04.json) represents real
  ``pip list --format=json`` output and exercises the full parsing path.
* Each conditional branch in collect(), _parse(), and _try_parse_semver()
  gets at least one dedicated test.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from env_audit.collectors.pip import PipCollector, _PIP_VERSION_RE
from env_audit.collectors.base import (
    CollectorParseError,
    CollectorTimeoutError,
    CollectorUnavailableError,
)
from env_audit.models import SemVer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "pip" / "ubuntu-22.04.json"


def _make_result(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    r = MagicMock()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


# ---------------------------------------------------------------------------
# ecosystem property
# ---------------------------------------------------------------------------


class TestEcosystem:
    def test_returns_pip(self) -> None:
        assert PipCollector().ecosystem == "pip"


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_true_when_pip3_found(self) -> None:
        with patch("env_audit.collectors.pip.shutil.which", return_value="/usr/bin/pip3"):
            assert PipCollector().is_available() is True

    def test_true_when_only_pip_found(self) -> None:
        # pip3 not found, pip found
        def which(name: str) -> str | None:
            return "/usr/bin/pip" if name == "pip" else None
        with patch("env_audit.collectors.pip.shutil.which", side_effect=which):
            assert PipCollector().is_available() is True

    def test_false_when_both_missing(self) -> None:
        with patch("env_audit.collectors.pip.shutil.which", return_value=None):
            assert PipCollector().is_available() is False


# ---------------------------------------------------------------------------
# _pip_binary()
# ---------------------------------------------------------------------------


class TestPipBinary:
    def test_returns_pip3_when_available(self) -> None:
        with patch("env_audit.collectors.pip.shutil.which", return_value="/usr/bin/pip3"):
            assert PipCollector()._pip_binary() == "pip3"

    def test_falls_back_to_pip(self) -> None:
        def which(name: str) -> str | None:
            return None if name == "pip3" else "/usr/bin/pip"
        with patch("env_audit.collectors.pip.shutil.which", side_effect=which):
            assert PipCollector()._pip_binary() == "pip"


# ---------------------------------------------------------------------------
# collect() — subprocess layer
# ---------------------------------------------------------------------------


class TestCollect:
    def test_raises_unavailable_when_pip_missing(self) -> None:
        with patch("env_audit.collectors.pip.shutil.which", return_value=None):
            with pytest.raises(CollectorUnavailableError) as exc_info:
                PipCollector().collect()
            assert exc_info.value.ecosystem == "pip"

    def test_raises_timeout_on_subprocess_timeout(self) -> None:
        with patch("env_audit.collectors.pip.shutil.which", return_value="/usr/bin/pip3"):
            with patch(
                "env_audit.collectors.pip.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="pip3", timeout=30.0),
            ):
                with pytest.raises(CollectorTimeoutError) as exc_info:
                    PipCollector().collect()
                assert exc_info.value.ecosystem == "pip"
                assert exc_info.value.timeout == 30.0

    def test_raises_parse_error_on_non_zero_exit(self) -> None:
        with patch("env_audit.collectors.pip.shutil.which", return_value="/usr/bin/pip3"):
            with patch(
                "env_audit.collectors.pip.subprocess.run",
                return_value=_make_result(stdout="", stderr="ERROR: something", returncode=1),
            ):
                with pytest.raises(CollectorParseError) as exc_info:
                    PipCollector().collect()
                assert "status 1" in exc_info.value.detail

    def test_success_returns_package_list(self) -> None:
        fixture = FIXTURE_PATH.read_text()
        with patch("env_audit.collectors.pip.shutil.which", return_value="/usr/bin/pip3"):
            with patch(
                "env_audit.collectors.pip.subprocess.run",
                return_value=_make_result(stdout=fixture),
            ):
                records = PipCollector().collect()
        assert len(records) > 0
        names = [r.name for r in records]
        assert "click" in names
        assert "pydantic" in names

    def test_timeout_passed_to_subprocess(self) -> None:
        fixture = FIXTURE_PATH.read_text()
        with patch("env_audit.collectors.pip.shutil.which", return_value="/usr/bin/pip3"):
            with patch(
                "env_audit.collectors.pip.subprocess.run",
                return_value=_make_result(stdout=fixture),
            ) as mock_run:
                PipCollector().collect()
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["timeout"] == PipCollector.DEFAULT_TIMEOUT

    def test_uses_pip3_binary_when_available(self) -> None:
        fixture = FIXTURE_PATH.read_text()
        with patch("env_audit.collectors.pip.shutil.which", return_value="/usr/bin/pip3"):
            with patch(
                "env_audit.collectors.pip.subprocess.run",
                return_value=_make_result(stdout=fixture),
            ) as mock_run:
                PipCollector().collect()
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == "pip3"


# ---------------------------------------------------------------------------
# _parse() — output parsing
# ---------------------------------------------------------------------------


class TestParse:
    def _parse(self, text: str):
        return PipCollector()._parse(text)

    def test_empty_string_returns_empty_list(self) -> None:
        assert self._parse("") == []

    def test_invalid_json_returns_empty_list(self) -> None:
        assert self._parse("this is not json") == []

    def test_non_list_json_returns_empty_list(self) -> None:
        assert self._parse('{"name": "pip"}') == []

    def test_non_dict_entry_skipped(self) -> None:
        assert self._parse('["not a dict", 42]') == []

    def test_entry_missing_name_skipped(self) -> None:
        assert self._parse('[{"version": "1.0.0"}]') == []

    def test_entry_with_empty_name_skipped(self) -> None:
        assert self._parse('[{"name": "", "version": "1.0.0"}]') == []

    def test_entry_with_non_string_name_skipped(self) -> None:
        assert self._parse('[{"name": 42, "version": "1.0.0"}]') == []

    def test_entry_with_non_string_version_treats_as_none(self) -> None:
        records = self._parse('[{"name": "pkg", "version": 123}]')
        assert len(records) == 1
        assert records[0].version_raw is None

    def test_ecosystem_is_pip(self) -> None:
        records = self._parse('[{"name": "click", "version": "8.1.7"}]')
        assert records[0].ecosystem == "pip"

    def test_source_is_pypi(self) -> None:
        records = self._parse('[{"name": "click", "version": "8.1.7"}]')
        assert records[0].source == "pypi"

    def test_version_raw_preserved(self) -> None:
        records = self._parse('[{"name": "click", "version": "8.1.7"}]')
        assert records[0].version_raw == "8.1.7"

    def test_parseable_version_sets_version_parsed(self) -> None:
        records = self._parse('[{"name": "click", "version": "8.1.7"}]')
        assert records[0].version_parsed == SemVer(major=8, minor=1, patch=7)

    def test_unparseable_version_leaves_version_parsed_none(self) -> None:
        # A version that cannot be parsed (starts with letter)
        records = self._parse('[{"name": "pkg", "version": "abc.xyz"}]')
        assert records[0].version_raw == "abc.xyz"
        assert records[0].version_parsed is None

    def test_fixture_file_round_trip(self) -> None:
        output = FIXTURE_PATH.read_text()
        records = self._parse(output)
        by_name = {r.name: r for r in records}

        # click — simple three-part version
        click = by_name["click"]
        assert click.ecosystem == "pip"
        assert click.source == "pypi"
        assert click.version_parsed == SemVer(major=8, minor=1, patch=7)

        # pydantic
        pydantic = by_name["pydantic"]
        assert pydantic.version_parsed == SemVer(major=2, minor=5, patch=3)

        # some-editable — prerelease suffix
        editable = by_name["some-editable"]
        assert editable.version_raw == "0.1.0-dev1"
        assert editable.version_parsed == SemVer(major=0, minor=1, patch=0, prerelease="dev1")

        assert all(r.ecosystem == "pip" for r in records)

    def test_multiple_packages_parsed(self) -> None:
        data = json.dumps([
            {"name": "a", "version": "1.0.0"},
            {"name": "b", "version": "2.0.0"},
        ])
        records = self._parse(data)
        assert len(records) == 2
        assert {r.name for r in records} == {"a", "b"}


# ---------------------------------------------------------------------------
# _try_parse_semver() — version parsing
# ---------------------------------------------------------------------------


class TestTryParseSemver:
    def _p(self, v: str):
        return PipCollector()._try_parse_semver(v)

    def test_simple_three_part(self) -> None:
        assert self._p("8.1.7") == SemVer(major=8, minor=1, patch=7)

    def test_major_minor_only(self) -> None:
        assert self._p("3.11") == SemVer(major=3, minor=11, patch=0)

    def test_major_only(self) -> None:
        assert self._p("3") == SemVer(major=3, minor=0, patch=0)

    def test_with_dash_prerelease(self) -> None:
        assert self._p("0.1.0-dev1") == SemVer(major=0, minor=1, patch=0, prerelease="dev1")

    def test_with_dot_prerelease(self) -> None:
        sv = self._p("2.0.0.post1")
        assert sv == SemVer(major=2, minor=0, patch=0, prerelease="post1")

    def test_prerelease_is_none_when_absent(self) -> None:
        assert self._p("2.5.3").prerelease is None

    def test_returns_none_for_non_numeric_start(self) -> None:
        assert self._p("abc") is None

    def test_returns_none_for_empty_string(self) -> None:
        assert self._p("") is None