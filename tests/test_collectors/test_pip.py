# tests/test_collectors/test_pip.py
"""
100 % coverage tests for env_audit.collectors.pip.

Design principles
-----------------
* All subprocess interaction is mocked - tests never touch the live system.
* The fixture file (tests/fixtures/pip/ubuntu-22.04.json) represents real
  ``pip list --format=json`` output and exercises the full parsing path.
* Each conditional branch in collect(), _parse(), and _try_parse_semver()
  gets at least one dedicated test.
"""

import csv
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from env_audit.collectors.pip import PipCollector, _PIP_VERSION_RE, _normalize_name
from env_audit.collectors.base import (
    CollectorParseError,
    CollectorTimeoutError,
    CollectorUnavailableError,
)
from env_audit.models import Confidence, PackageRecord, SemVer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "pip"
FIXTURE_PATH = FIXTURE_DIR / "ubuntu-22.04.json"
VERBOSE_FIXTURE_PATH = FIXTURE_DIR / "ubuntu-22.04-verbose.json"


def _make_env(tmp_path: Path) -> Path:
    """
    Build a miniature Python environment under *tmp_path* and return the
    site-packages directory.

    Layout mirrors a real ``--user`` install::

        <tmp>/bin/mypy                                  (regular file)
        <tmp>/bin/dmypy                                 (symlink -> mypy)
        <tmp>/lib/python3.10/site-packages/             (the 'location')
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    real = bin_dir / "mypy"
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    (bin_dir / "dmypy").symlink_to(real)

    site_packages = tmp_path / "lib" / "python3.10" / "site-packages"
    site_packages.mkdir(parents=True)
    return site_packages


def _write_record(dist_info: Path, rows: list[list[str]]) -> None:
    """Write *rows* as the RECORD CSV inside *dist_info*."""
    dist_info.mkdir(parents=True, exist_ok=True)
    with (dist_info / "RECORD").open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)


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
# collect() - subprocess layer
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

    def test_requests_verbose_output(self) -> None:
        # --verbose is what supplies the per-package 'location' key that
        # binary attribution depends on.
        fixture = FIXTURE_PATH.read_text()
        with patch("env_audit.collectors.pip.shutil.which", return_value="/usr/bin/pip3"):
            with patch(
                "env_audit.collectors.pip.subprocess.run",
                return_value=_make_result(stdout=fixture),
            ) as mock_run:
                PipCollector().collect()
        cmd = mock_run.call_args.args[0]
        assert "--verbose" in cmd
        assert "--format=json" in cmd

    def test_attaches_binaries_to_parsed_records(self) -> None:
        fixture = FIXTURE_PATH.read_text()
        collector = PipCollector()
        with patch("env_audit.collectors.pip.shutil.which", return_value="/usr/bin/pip3"):
            with patch(
                "env_audit.collectors.pip.subprocess.run",
                return_value=_make_result(stdout=fixture),
            ):
                with patch.object(
                    collector, "_attach_binaries", return_value=["sentinel"]
                ) as mock_attach:
                    result = collector.collect()

        assert result == ["sentinel"]
        parsed = mock_attach.call_args.args[0]
        assert all(isinstance(r, PackageRecord) for r in parsed)


# ---------------------------------------------------------------------------
# _parse() - output parsing
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

        # click - simple three-part version
        click = by_name["click"]
        assert click.ecosystem == "pip"
        assert click.source == "pypi"
        assert click.version_parsed == SemVer(major=8, minor=1, patch=7)

        # pydantic
        pydantic = by_name["pydantic"]
        assert pydantic.version_parsed == SemVer(major=2, minor=5, patch=3)

        # some-editable - prerelease suffix
        editable = by_name["some-editable"]
        assert editable.version_raw == "0.1.0-dev1"
        assert editable.version_parsed == SemVer(major=0, minor=1, patch=0, prerelease="dev1")

        assert all(r.ecosystem == "pip" for r in records)

    def test_location_becomes_install_path(self) -> None:
        records = self._parse(
            '[{"name": "mypy", "version": "1.0.0", "location": "/site-packages"}]'
        )
        assert records[0].install_path == "/site-packages"

    def test_missing_location_leaves_install_path_none(self) -> None:
        records = self._parse('[{"name": "mypy", "version": "1.0.0"}]')
        assert records[0].install_path is None

    def test_non_string_location_leaves_install_path_none(self) -> None:
        records = self._parse('[{"name": "mypy", "version": "1.0.0", "location": 42}]')
        assert records[0].install_path is None

    def test_empty_location_leaves_install_path_none(self) -> None:
        records = self._parse('[{"name": "mypy", "version": "1.0.0", "location": ""}]')
        assert records[0].install_path is None

    def test_editable_location_stored_as_namespaced_extension(self) -> None:
        records = self._parse(
            '[{"name": "proj", "version": "0.1.0", "location": "/sp",'
            ' "editable_project_location": "/home/u/proj"}]'
        )
        assert records[0].metadata.extensions == {
            "pip:editable_project_location": "/home/u/proj"
        }

    def test_non_editable_package_has_no_extensions(self) -> None:
        records = self._parse('[{"name": "mypy", "version": "1.0.0"}]')
        assert records[0].metadata.extensions == {}

    def test_non_string_editable_location_ignored(self) -> None:
        records = self._parse(
            '[{"name": "proj", "version": "0.1.0", "editable_project_location": 7}]'
        )
        assert records[0].metadata.extensions == {}

    def test_parse_never_touches_the_filesystem(self) -> None:
        # _parse() is contractually pure; binary attribution is a separate,
        # explicitly filesystem-reading phase.
        records = self._parse(VERBOSE_FIXTURE_PATH.read_text())
        assert all(r.binaries == [] for r in records)

    def test_verbose_fixture_round_trip(self) -> None:
        records = self._parse(VERBOSE_FIXTURE_PATH.read_text())
        by_name = {r.name: r for r in records}

        user_site = "/home/firstlast/.local/lib/python3.10/site-packages"
        assert by_name["mypy"].install_path == user_site
        assert by_name["mypy"].version_parsed == SemVer(major=1, minor=20, patch=1)

        # apt-installed package in the system dist-packages tree
        assert by_name["PyGObject"].install_path == "/usr/lib/python3/dist-packages"

        # editable install carries its project location
        assert (
            by_name["env-audit-poc"].metadata.extensions["pip:editable_project_location"]
            == "/home/firstlast/personal/projects/env-audit-poc"
        )

        # an entry from an older pip with no location key at all
        assert by_name["legacy-no-location"].install_path is None

        assert all(r.ecosystem == "pip" for r in records)
        assert all(r.source == "pypi" for r in records)

    def test_multiple_packages_parsed(self) -> None:
        data = json.dumps([
            {"name": "a", "version": "1.0.0"},
            {"name": "b", "version": "2.0.0"},
        ])
        records = self._parse(data)
        assert len(records) == 2
        assert {r.name for r in records} == {"a", "b"}


# ---------------------------------------------------------------------------
# _try_parse_semver() - version parsing
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


# ---------------------------------------------------------------------------
# _normalize_name() - PEP 503 name normalization
# ---------------------------------------------------------------------------


class TestNormalizeName:
    def test_underscores_become_dashes(self) -> None:
        assert _normalize_name("mypy_extensions") == "mypy-extensions"

    def test_dots_become_dashes(self) -> None:
        assert _normalize_name("lazr.uri") == "lazr-uri"

    def test_case_is_lowered(self) -> None:
        assert _normalize_name("PyGObject") == "pygobject"

    def test_runs_of_separators_collapse(self) -> None:
        assert _normalize_name("a__.--b") == "a-b"

    def test_all_spellings_agree(self) -> None:
        forms = ["mypy_extensions", "mypy-extensions", "Mypy.Extensions", "MYPY__EXTENSIONS"]
        assert len({_normalize_name(f) for f in forms}) == 1

    def test_already_normalized_is_unchanged(self) -> None:
        assert _normalize_name("pytest") == "pytest"


# ---------------------------------------------------------------------------
# _index_dist_info() - locating metadata directories
# ---------------------------------------------------------------------------


class TestIndexDistInfo:
    def _index(self, location: Path) -> dict[str, list[Path]]:
        return PipCollector()._index_dist_info(location)

    def test_finds_dist_info_directory(self, tmp_path: Path) -> None:
        (tmp_path / "pytest-8.3.3.dist-info").mkdir()
        index = self._index(tmp_path)
        assert list(index) == ["pytest"]

    def test_key_is_normalized(self, tmp_path: Path) -> None:
        # PEP 427 escapes '-' to '_' in the directory name; the lookup key
        # must come back to the PEP 503 form.
        (tmp_path / "mypy_extensions-1.0.0.dist-info").mkdir()
        assert "mypy-extensions" in self._index(tmp_path)

    def test_mixed_case_directory_is_normalized(self, tmp_path: Path) -> None:
        (tmp_path / "PyGObject-3.42.1.dist-info").mkdir()
        assert "pygobject" in self._index(tmp_path)

    def test_egg_info_is_ignored(self, tmp_path: Path) -> None:
        # apt-installed packages use .egg-info and carry no RECORD.
        (tmp_path / "Babel-2.8.0.egg-info").mkdir()
        assert self._index(tmp_path) == {}

    def test_package_directories_are_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "mypy").mkdir()
        (tmp_path / "click.py").write_text("")
        assert self._index(tmp_path) == {}

    def test_missing_location_returns_empty(self, tmp_path: Path) -> None:
        assert self._index(tmp_path / "nonexistent") == {}

    def test_oserror_returns_empty(self, tmp_path: Path) -> None:
        with patch.object(Path, "iterdir", side_effect=OSError("permission denied")):
            assert self._index(tmp_path) == {}

    def test_two_versions_of_same_name_both_recorded(self, tmp_path: Path) -> None:
        (tmp_path / "mypy-1.20.1.dist-info").mkdir()
        (tmp_path / "mypy-1.19.0.dist-info").mkdir()
        index = self._index(tmp_path)
        assert len(index["mypy"]) == 2

    def test_multiple_packages_indexed(self, tmp_path: Path) -> None:
        (tmp_path / "mypy-1.20.1.dist-info").mkdir()
        (tmp_path / "pytest-8.3.3.dist-info").mkdir()
        assert set(self._index(tmp_path)) == {"mypy", "pytest"}


# ---------------------------------------------------------------------------
# _pick_dist_info() - choosing between leftover metadata directories
# ---------------------------------------------------------------------------


class TestPickDistInfo:
    def _pick(self, candidates: list[Path], version: str | None) -> Path:
        return PipCollector()._pick_dist_info(candidates, version)

    def test_single_candidate_returned(self, tmp_path: Path) -> None:
        only = tmp_path / "mypy-1.20.1.dist-info"
        assert self._pick([only], "1.20.1") is only

    def test_version_match_preferred(self, tmp_path: Path) -> None:
        stale = tmp_path / "mypy-1.19.0.dist-info"
        current = tmp_path / "mypy-1.20.1.dist-info"
        assert self._pick([stale, current], "1.20.1") is current

    def test_falls_back_to_first_when_version_unknown(self, tmp_path: Path) -> None:
        a = tmp_path / "mypy-1.19.0.dist-info"
        b = tmp_path / "mypy-1.20.1.dist-info"
        assert self._pick([a, b], None) is a

    def test_falls_back_to_first_when_no_version_matches(self, tmp_path: Path) -> None:
        a = tmp_path / "mypy-1.19.0.dist-info"
        b = tmp_path / "mypy-1.20.1.dist-info"
        assert self._pick([a, b], "9.9.9") is a


# ---------------------------------------------------------------------------
# _binaries_from_record() - reading the RECORD manifest
# ---------------------------------------------------------------------------


class TestBinariesFromRecord:
    def _binaries(self, dist_info: Path, location: Path):
        return PipCollector()._binaries_from_record(dist_info, location)

    def test_bin_entry_resolves_to_absolute_path(self, tmp_path: Path) -> None:
        site = _make_env(tmp_path)
        dist_info = site / "mypy-1.20.1.dist-info"
        _write_record(dist_info, [["../../../bin/mypy", "sha256=x", "230"]])

        binaries = self._binaries(dist_info, site)
        assert len(binaries) == 1
        assert binaries[0].name == "mypy"
        assert binaries[0].path == str(tmp_path / "bin" / "mypy")

    def test_confidence_is_high(self, tmp_path: Path) -> None:
        # Manifest data, not a heuristic - this is what lets the normalizer
        # suppress the corresponding manual record.
        site = _make_env(tmp_path)
        dist_info = site / "mypy-1.20.1.dist-info"
        _write_record(dist_info, [["../../../bin/mypy", "sha256=x", "230"]])
        assert self._binaries(dist_info, site)[0].confidence == Confidence.HIGH

    def test_symlinked_script_records_target(self, tmp_path: Path) -> None:
        site = _make_env(tmp_path)
        dist_info = site / "mypy-1.20.1.dist-info"
        _write_record(dist_info, [["../../../bin/dmypy", "sha256=x", "234"]])

        binary = self._binaries(dist_info, site)[0]
        assert binary.is_symlink is True
        assert binary.symlink_target is not None
        assert binary.symlink_target.endswith("mypy")

    def test_pycache_under_bin_excluded(self, tmp_path: Path) -> None:
        site = _make_env(tmp_path)
        dist_info = site / "bottle-0.13.2.dist-info"
        _write_record(dist_info, [["../../../bin/__pycache__/bottle.cpython-310.pyc", "", "1"]])
        assert self._binaries(dist_info, site) == []

    def test_non_bin_data_entries_excluded(self, tmp_path: Path) -> None:
        site = _make_env(tmp_path)
        dist_info = site / "fonttools-4.0.0.dist-info"
        _write_record(
            dist_info,
            [
                ["../../../share/man/man1/ttx.1", "", "1"],
                ["../../../include/python3.10/greenlet/greenlet.h", "", "1"],
            ],
        )
        assert self._binaries(dist_info, site) == []

    def test_package_internal_files_excluded(self, tmp_path: Path) -> None:
        site = _make_env(tmp_path)
        dist_info = site / "mypy-1.20.1.dist-info"
        _write_record(
            dist_info,
            [
                ["mypy/__init__.py", "sha256=x", "1234"],
                ["mypy-1.20.1.dist-info/METADATA", "sha256=y", "50"],
            ],
        )
        assert self._binaries(dist_info, site) == []

    def test_missing_record_returns_empty(self, tmp_path: Path) -> None:
        site = _make_env(tmp_path)
        dist_info = site / "mypy-1.20.1.dist-info"
        dist_info.mkdir()
        assert self._binaries(dist_info, site) == []

    def test_unreadable_record_returns_empty(self, tmp_path: Path) -> None:
        site = _make_env(tmp_path)
        dist_info = site / "mypy-1.20.1.dist-info"
        _write_record(dist_info, [["../../../bin/mypy", "sha256=x", "230"]])
        with patch.object(Path, "open", side_effect=OSError("permission denied")):
            assert self._binaries(dist_info, site) == []

    def test_malformed_csv_returns_empty(self, tmp_path: Path) -> None:
        site = _make_env(tmp_path)
        dist_info = site / "mypy-1.20.1.dist-info"
        dist_info.mkdir()
        with patch("env_audit.collectors.pip.csv.reader", side_effect=csv.Error("bad")):
            (dist_info / "RECORD").write_text("whatever\n")
            assert self._binaries(dist_info, site) == []

    def test_blank_and_empty_rows_skipped(self, tmp_path: Path) -> None:
        site = _make_env(tmp_path)
        dist_info = site / "mypy-1.20.1.dist-info"
        dist_info.mkdir()
        (dist_info / "RECORD").write_text(
            "\n,,\n../../../bin/mypy,sha256=x,230\n", encoding="utf-8"
        )
        binaries = self._binaries(dist_info, site)
        assert [b.name for b in binaries] == ["mypy"]

    def test_relative_location_skipped(self, tmp_path: Path) -> None:
        # BinaryRecord requires an absolute path; a relative location must
        # be skipped rather than raise a validation error.
        site = _make_env(tmp_path)
        dist_info = site / "mypy-1.20.1.dist-info"
        _write_record(dist_info, [["../bin/mypy", "sha256=x", "230"]])
        assert self._binaries(dist_info, Path("relative/site-packages")) == []

    def test_nonexistent_script_still_recorded(self, tmp_path: Path) -> None:
        # The manifest is the authority on ownership; the normalizer matches
        # on path alone and never stats the file.
        site = _make_env(tmp_path)
        dist_info = site / "ghost-1.0.0.dist-info"
        _write_record(dist_info, [["../../../bin/ghost", "sha256=x", "10"]])
        binaries = self._binaries(dist_info, site)
        assert [b.name for b in binaries] == ["ghost"]
        assert binaries[0].is_symlink is False

    def test_multiple_scripts_all_recorded(self, tmp_path: Path) -> None:
        site = _make_env(tmp_path)
        dist_info = site / "mypy-1.20.1.dist-info"
        _write_record(
            dist_info,
            [
                ["../../../bin/mypy", "sha256=a", "230"],
                ["../../../bin/dmypy", "sha256=b", "234"],
                ["mypy/__init__.py", "sha256=c", "10"],
            ],
        )
        assert {b.name for b in self._binaries(dist_info, site)} == {"mypy", "dmypy"}


# ---------------------------------------------------------------------------
# _attach_binaries() - wiring records to their manifests
# ---------------------------------------------------------------------------


def _record(name: str, install_path: str | None, version: str = "1.0.0") -> PackageRecord:
    return PackageRecord(
        name=name,
        version_raw=version,
        ecosystem="pip",
        source="pypi",
        install_path=install_path,
    )


class TestAttachBinaries:
    def _attach(self, records: list[PackageRecord]) -> list[PackageRecord]:
        return PipCollector()._attach_binaries(records)

    def test_empty_list_returns_empty(self) -> None:
        assert self._attach([]) == []

    def test_record_without_install_path_passed_through(self) -> None:
        pkg = _record("legacy", None)
        result = self._attach([pkg])
        assert result[0] is pkg

    def test_record_with_no_matching_dist_info_unchanged(self, tmp_path: Path) -> None:
        site = _make_env(tmp_path)
        pkg = _record("unknown", str(site))
        assert self._attach([pkg])[0] is pkg

    def test_record_whose_manifest_lists_no_scripts_unchanged(self, tmp_path: Path) -> None:
        site = _make_env(tmp_path)
        _write_record(site / "libonly-1.0.0.dist-info", [["libonly/__init__.py", "", "1"]])
        pkg = _record("libonly", str(site))
        assert self._attach([pkg])[0] is pkg

    def test_binaries_attached_from_manifest(self, tmp_path: Path) -> None:
        site = _make_env(tmp_path)
        _write_record(
            site / "mypy-1.20.1.dist-info", [["../../../bin/mypy", "sha256=x", "230"]]
        )
        result = self._attach([_record("mypy", str(site), version="1.20.1")])
        assert [b.name for b in result[0].binaries] == ["mypy"]
        assert result[0].binaries[0].confidence == Confidence.HIGH

    def test_other_fields_preserved_on_the_copy(self, tmp_path: Path) -> None:
        site = _make_env(tmp_path)
        _write_record(
            site / "mypy-1.20.1.dist-info", [["../../../bin/mypy", "sha256=x", "230"]]
        )
        original = _record("mypy", str(site), version="1.20.1")
        updated = self._attach([original])[0]

        assert updated is not original          # frozen model: a copy is made
        assert original.binaries == []          # the input is never mutated
        assert updated.name == original.name
        assert updated.version_raw == original.version_raw
        assert updated.version_parsed == original.version_parsed
        assert updated.ecosystem == original.ecosystem
        assert updated.source == original.source
        assert updated.install_path == original.install_path
        assert updated.metadata == original.metadata

    def test_escaped_dist_info_name_matches_package_name(self, tmp_path: Path) -> None:
        # pip reports 'mypy-extensions'; the directory is 'mypy_extensions-…'.
        site = _make_env(tmp_path)
        _write_record(
            site / "mypy_extensions-1.0.0.dist-info",
            [["../../../bin/mypy-ext", "sha256=x", "10"]],
        )
        result = self._attach([_record("mypy-extensions", str(site))])
        assert [b.name for b in result[0].binaries] == ["mypy-ext"]

    def test_version_match_used_to_disambiguate(self, tmp_path: Path) -> None:
        site = _make_env(tmp_path)
        _write_record(
            site / "mypy-1.19.0.dist-info", [["../../../bin/stale", "sha256=x", "10"]]
        )
        _write_record(
            site / "mypy-1.20.1.dist-info", [["../../../bin/mypy", "sha256=y", "230"]]
        )
        result = self._attach([_record("mypy", str(site), version="1.20.1")])
        assert [b.name for b in result[0].binaries] == ["mypy"]

    def test_location_listed_once_per_directory(self, tmp_path: Path) -> None:
        # A real environment has 150+ packages sharing two or three
        # locations; the directory listing must be cached.
        site = _make_env(tmp_path)
        _write_record(site / "a-1.0.0.dist-info", [["../../../bin/a", "", "1"]])
        _write_record(site / "b-1.0.0.dist-info", [["../../../bin/b", "", "1"]])

        collector = PipCollector()
        with patch.object(
            collector, "_index_dist_info", wraps=collector._index_dist_info
        ) as spy:
            collector._attach_binaries(
                [_record("a", str(site)), _record("b", str(site))]
            )
        assert spy.call_count == 1

    def test_separate_locations_each_listed(self, tmp_path: Path) -> None:
        site_a = _make_env(tmp_path / "a")
        site_b = _make_env(tmp_path / "b")
        collector = PipCollector()
        with patch.object(
            collector, "_index_dist_info", wraps=collector._index_dist_info
        ) as spy:
            collector._attach_binaries(
                [_record("x", str(site_a)), _record("y", str(site_b))]
            )
        assert spy.call_count == 2

    def test_order_is_preserved(self, tmp_path: Path) -> None:
        site = _make_env(tmp_path)
        records = [_record("a", str(site)), _record("b", None), _record("c", str(site))]
        assert [r.name for r in self._attach(records)] == ["a", "b", "c"]