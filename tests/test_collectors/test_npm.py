# tests/test_collectors/test_npm.py
"""
100 % coverage tests for env_audit.collectors.npm.

Design principles
-----------------
* All subprocess interaction is mocked — tests never touch the live system.
* The fixture file (tests/fixtures/npm/global.json) represents real
  ``npm list -g --json`` output and exercises the full parsing path.
* Each conditional branch in collect(), _parse(), and _try_parse_semver()
  gets at least one dedicated test.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from env_audit.collectors.npm import NpmCollector
from env_audit.collectors.base import (
    CollectorParseError,
    CollectorTimeoutError,
    CollectorUnavailableError,
)
from env_audit.models import SemVer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "npm" / "global.json"


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
    def test_returns_npm(self) -> None:
        assert NpmCollector().ecosystem == "npm"


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_true_when_npm_found(self) -> None:
        with patch("env_audit.collectors.npm.shutil.which", return_value="/usr/bin/npm"):
            assert NpmCollector().is_available() is True

    def test_false_when_npm_missing(self) -> None:
        with patch("env_audit.collectors.npm.shutil.which", return_value=None):
            assert NpmCollector().is_available() is False


# ---------------------------------------------------------------------------
# collect() — subprocess layer
# ---------------------------------------------------------------------------


class TestCollect:
    def test_raises_unavailable_when_npm_missing(self) -> None:
        with patch("env_audit.collectors.npm.shutil.which", return_value=None):
            with pytest.raises(CollectorUnavailableError) as exc_info:
                NpmCollector().collect()
            assert exc_info.value.ecosystem == "npm"
            assert "npm binary not found" in exc_info.value.reason

    def test_raises_timeout_on_subprocess_timeout(self) -> None:
        with patch("env_audit.collectors.npm.shutil.which", return_value="/usr/bin/npm"):
            with patch(
                "env_audit.collectors.npm.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="npm", timeout=30.0),
            ):
                with pytest.raises(CollectorTimeoutError) as exc_info:
                    NpmCollector().collect()
                assert exc_info.value.ecosystem == "npm"
                assert exc_info.value.timeout == 30.0

    def test_exit_code_1_not_an_error(self) -> None:
        """npm exits 1 for unmet peer deps but still outputs valid JSON."""
        fixture = FIXTURE_PATH.read_text()
        with patch("env_audit.collectors.npm.shutil.which", return_value="/usr/bin/npm"):
            with patch(
                "env_audit.collectors.npm.subprocess.run",
                return_value=_make_result(stdout=fixture, returncode=1),
            ):
                records = NpmCollector().collect()
        # Should succeed with packages despite exit code 1
        assert len(records) > 0

    def test_exit_code_2_raises_parse_error(self) -> None:
        with patch("env_audit.collectors.npm.shutil.which", return_value="/usr/bin/npm"):
            with patch(
                "env_audit.collectors.npm.subprocess.run",
                return_value=_make_result(stdout="", stderr="fatal error", returncode=2),
            ):
                with pytest.raises(CollectorParseError) as exc_info:
                    NpmCollector().collect()
                assert "status 2" in exc_info.value.detail

    def test_success_returns_package_list(self) -> None:
        fixture = FIXTURE_PATH.read_text()
        with patch("env_audit.collectors.npm.shutil.which", return_value="/usr/bin/npm"):
            with patch(
                "env_audit.collectors.npm.subprocess.run",
                return_value=_make_result(stdout=fixture),
            ):
                records = NpmCollector().collect()
        assert len(records) > 0
        names = [r.name for r in records]
        assert "typescript" in names
        assert "yarn" in names

    def test_timeout_passed_to_subprocess(self) -> None:
        fixture = FIXTURE_PATH.read_text()
        with patch("env_audit.collectors.npm.shutil.which", return_value="/usr/bin/npm"):
            with patch(
                "env_audit.collectors.npm.subprocess.run",
                return_value=_make_result(stdout=fixture),
            ) as mock_run:
                NpmCollector().collect()
        assert mock_run.call_args.kwargs["timeout"] == NpmCollector.DEFAULT_TIMEOUT


# ---------------------------------------------------------------------------
# _parse() — output parsing
# ---------------------------------------------------------------------------


class TestParse:
    def _parse(self, text: str):
        return NpmCollector()._parse(text)

    def test_empty_string_returns_empty_list(self) -> None:
        assert self._parse("") == []

    def test_invalid_json_returns_empty_list(self) -> None:
        assert self._parse("not json") == []

    def test_non_dict_json_returns_empty_list(self) -> None:
        assert self._parse('["array", "not", "dict"]') == []

    def test_missing_dependencies_key_returns_empty_list(self) -> None:
        assert self._parse('{"version": "1.0.0"}') == []

    def test_non_dict_dependencies_returns_empty_list(self) -> None:
        assert self._parse('{"dependencies": ["not", "a", "dict"]}') == []

    def test_non_dict_entry_value_skipped(self) -> None:
        data = '{"dependencies": {"pkg": "not-a-dict"}}'
        assert self._parse(data) == []

    def test_empty_name_skipped(self) -> None:
        data = json.dumps({"dependencies": {"": {"version": "1.0.0"}}})
        assert self._parse(data) == []

    def test_non_string_version_treated_as_none(self) -> None:
        data = json.dumps({"dependencies": {"pkg": {"version": 123}}})
        records = self._parse(data)
        assert len(records) == 1
        assert records[0].version_raw is None
        assert records[0].version_parsed is None

    def test_missing_version_treated_as_none(self) -> None:
        data = json.dumps({"dependencies": {"pkg": {"resolved": "https://..."}}})
        records = self._parse(data)
        assert len(records) == 1
        assert records[0].version_raw is None

    def test_ecosystem_is_npm(self) -> None:
        data = json.dumps({"dependencies": {"typescript": {"version": "5.3.3"}}})
        records = self._parse(data)
        assert records[0].ecosystem == "npm"

    def test_source_is_npmjs(self) -> None:
        data = json.dumps({"dependencies": {"typescript": {"version": "5.3.3"}}})
        records = self._parse(data)
        assert records[0].source == "npmjs"

    def test_version_raw_preserved(self) -> None:
        data = json.dumps({"dependencies": {"typescript": {"version": "5.3.3"}}})
        assert self._parse(data)[0].version_raw == "5.3.3"

    def test_parseable_version_sets_version_parsed(self) -> None:
        data = json.dumps({"dependencies": {"typescript": {"version": "5.3.3"}}})
        records = self._parse(data)
        assert records[0].version_parsed == SemVer(major=5, minor=3, patch=3)

    def test_unparseable_version_leaves_version_parsed_none(self) -> None:
        data = json.dumps({"dependencies": {"pkg": {"version": "notaversion"}}})
        records = self._parse(data)
        assert records[0].version_parsed is None

    def test_scoped_package_name_preserved(self) -> None:
        data = json.dumps({"dependencies": {"@angular/cli": {"version": "17.0.6"}}})
        records = self._parse(data)
        assert records[0].name == "@angular/cli"

    def test_fixture_file_round_trip(self) -> None:
        output = FIXTURE_PATH.read_text()
        records = self._parse(output)
        by_name = {r.name: r for r in records}

        # npm
        npm_pkg = by_name["npm"]
        assert npm_pkg.ecosystem == "npm"
        assert npm_pkg.source == "npmjs"
        assert npm_pkg.version_parsed == SemVer(major=10, minor=2, patch=4)

        # typescript
        ts = by_name["typescript"]
        assert ts.version_parsed == SemVer(major=5, minor=3, patch=3)

        # yarn
        yarn = by_name["yarn"]
        assert yarn.version_raw == "1.22.21"
        assert yarn.version_parsed == SemVer(major=1, minor=22, patch=21)

        # scoped package
        angular = by_name["@angular/cli"]
        assert angular.version_parsed == SemVer(major=17, minor=0, patch=6)

        assert all(r.ecosystem == "npm" for r in records)

    def test_multiple_packages_all_present(self) -> None:
        data = json.dumps({
            "dependencies": {
                "a": {"version": "1.0.0"},
                "b": {"version": "2.0.0"},
                "c": {"version": "3.0.0"},
            }
        })
        records = self._parse(data)
        assert len(records) == 3
        assert {r.name for r in records} == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# _try_parse_semver()
# ---------------------------------------------------------------------------


class TestTryParseSemver:
    def _p(self, v: str):
        return NpmCollector()._try_parse_semver(v)

    def test_simple_three_part(self) -> None:
        assert self._p("5.3.3") == SemVer(major=5, minor=3, patch=3)

    def test_major_minor_only(self) -> None:
        assert self._p("1.22") == SemVer(major=1, minor=22, patch=0)

    def test_major_only(self) -> None:
        assert self._p("10") == SemVer(major=10, minor=0, patch=0)

    def test_with_prerelease(self) -> None:
        assert self._p("1.0.0-beta.1") == SemVer(major=1, minor=0, patch=0, prerelease="beta.1")

    def test_prerelease_none_when_absent(self) -> None:
        assert self._p("10.2.4").prerelease is None

    def test_returns_none_for_non_numeric(self) -> None:
        assert self._p("notaversion") is None

    def test_returns_none_for_empty(self) -> None:
        assert self._p("") is None