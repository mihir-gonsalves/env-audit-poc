# tests/test_cli.py
"""
100 % branch coverage tests for env_audit.cli.

Design
------
* ``CliRunner()`` captures both stdout and stderr (``mix_stderr=True`` default).
* ``COLLECTOR_REGISTRY`` and ``ANALYZER_REGISTRY`` are patched so no real
  system commands run.
* Every branch in ``main()`` is exercised:
  - ``collectors`` is None (all) vs explicit names
  - Unknown collector name -> ClickException + exit 1
  - verbose == 0, == 1, >= 2
  - verbose >= 2 with a successful collector (inner ``if`` -> True)
  - verbose >= 2 with a failing collector  (inner ``if`` -> False)
  - ``result.errors`` empty  (exit 0)
  - ``result.errors`` non-empty + ``--skip-failing``  (exit 0)
  - ``result.errors`` non-empty without ``--skip-failing`` (exit 1)
  - ``--format json`` and ``--format table``
  - ``--no-analyze`` suppresses analysis
  - analyzers run by default and their findings appear in output
  - verbose >= 1 shows finding count
  - verbose >= 2 shows per-analyzer counts
"""

import json as _json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from env_audit.analyzers.base import Finding
from env_audit.cli import ANALYZER_REGISTRY, COLLECTOR_REGISTRY, RENDERER_REGISTRY, main
from env_audit.collectors.base import (
    Collector,
    CollectorParseError,
    CollectorUnavailableError,
)
from env_audit.models import PackageRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_collector_class(
    ecosystem: str = "mock",
    packages: list[PackageRecord] | None = None,
    error: Exception | None = None,
) -> type:
    _pkgs = packages or []
    _err = error

    class _MockCollector(Collector):
        @property
        def ecosystem(self) -> str:
            return ecosystem

        def is_available(self) -> bool:
            return True

        def collect(self) -> list[PackageRecord]:
            if _err is not None:
                raise _err
            return _pkgs

    return _MockCollector


def _make_analyzer_class(findings: list[Finding] | None = None) -> type:
    _findings = findings or []

    class _MockAnalyzer:
        def analyze(self, packages):
            return _findings

    return _MockAnalyzer


def _pkg(name: str = "vim", ecosystem: str = "mock") -> PackageRecord:
    return PackageRecord(name=name, ecosystem=ecosystem, source="test-source")


def _runner() -> CliRunner:
    return CliRunner()


def _empty_analyzers() -> dict[str, type]:
    """Analyzer registry whose analyzers always return no findings."""
    return {
        "duplicates": _make_analyzer_class(),
        "path_shadow": _make_analyzer_class(),
        "orphans": _make_analyzer_class(),
    }


# ---------------------------------------------------------------------------
# COLLECTOR_REGISTRY sanity check
# ---------------------------------------------------------------------------


class TestCollectorRegistry:
    def test_apt_is_registered(self) -> None:
        assert "apt" in COLLECTOR_REGISTRY

    def test_pip_is_registered(self) -> None:
        assert "pip" in COLLECTOR_REGISTRY

    def test_npm_is_registered(self) -> None:
        assert "npm" in COLLECTOR_REGISTRY

    def test_manual_is_registered(self) -> None:
        assert "manual" in COLLECTOR_REGISTRY

    def test_registry_values_are_types(self) -> None:
        for cls in COLLECTOR_REGISTRY.values():
            assert isinstance(cls, type)


# ---------------------------------------------------------------------------
# ANALYZER_REGISTRY sanity check
# ---------------------------------------------------------------------------


class TestAnalyzerRegistry:
    def test_duplicates_is_registered(self) -> None:
        assert "duplicates" in ANALYZER_REGISTRY

    def test_path_shadow_is_registered(self) -> None:
        assert "path_shadow" in ANALYZER_REGISTRY

    def test_orphans_is_registered(self) -> None:
        assert "orphans" in ANALYZER_REGISTRY

    def test_registry_values_are_types(self) -> None:
        for cls in ANALYZER_REGISTRY.values():
            assert isinstance(cls, type)


# ---------------------------------------------------------------------------
# RENDERER_REGISTRY / --format Choice sync
# ---------------------------------------------------------------------------


class TestRendererRegistry:
    def test_renderer_registry_keys_match_format_choices(self) -> None:
        assert set(RENDERER_REGISTRY.keys()) == {"json", "table"}

    def test_renderer_registry_values_are_types(self) -> None:
        for cls in RENDERER_REGISTRY.values():
            assert isinstance(cls, type)


# ---------------------------------------------------------------------------
# --collectors flag
# ---------------------------------------------------------------------------


class TestCollectorsFlag:
    def test_unknown_collector_exits_1(self) -> None:
        result = _runner().invoke(main, ["--collectors", "unknown"])
        assert result.exit_code == 1

    def test_unknown_collector_message_written_to_stderr(self) -> None:
        result = _runner().invoke(main, ["--collectors", "nope"])
        assert "nope" in result.output

    def test_specific_collector_only_runs_that_ecosystem(self) -> None:
        registry = {
            "alpha": _make_collector_class("alpha", packages=[_pkg("a", "alpha")]),
            "beta":  _make_collector_class("beta",  packages=[_pkg("b", "beta")]),
        }
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, ["--collectors", "alpha", "--format", "json"])
        assert result.exit_code == 0
        data = _json.loads(result.output)
        names = [p["name"] for p in data["packages"]]
        assert "a" in names
        assert "b" not in names

    def test_no_collectors_flag_runs_all(self) -> None:
        registry = {
            "alpha": _make_collector_class("alpha", packages=[_pkg("a", "alpha")]),
            "beta":  _make_collector_class("beta",  packages=[_pkg("b", "beta")]),
        }
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, ["--format", "json"])
        assert result.exit_code == 0
        data = _json.loads(result.output)
        names = {p["name"] for p in data["packages"]}
        assert names == {"a", "b"}


# ---------------------------------------------------------------------------
# --format flag
# ---------------------------------------------------------------------------


class TestFormatFlag:
    def test_json_format_produces_valid_json(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, ["--format", "json"])
        assert result.exit_code == 0
        data = _json.loads(result.output)
        assert data["packages"][0]["name"] == "vim"

    def test_json_format_output_has_packages_and_findings_keys(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, ["--format", "json"])
        data = _json.loads(result.output)
        assert "packages" in data
        assert "findings" in data

    def test_json_packages_is_a_list(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, ["--format", "json"])
        assert isinstance(_json.loads(result.output)["packages"], list)

    def test_json_findings_is_a_list(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, ["--format", "json"])
        assert isinstance(_json.loads(result.output)["findings"], list)

    def test_table_format_contains_package_name(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg("myvim")])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, ["--format", "table"])
        assert result.exit_code == 0
        assert "myvim" in result.output

    def test_default_format_is_table(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, [])
        assert result.exit_code == 0
        assert "Name" in result.output


# ---------------------------------------------------------------------------
# --no-analyze flag
# ---------------------------------------------------------------------------


class TestNoAnalyzeFlag:
    def test_no_analyze_skips_analysis_in_json(self) -> None:
        """With --no-analyze the findings list is absent (key not present is
        impossible — we still emit the key but it must be empty)."""
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, ["--format", "json", "--no-analyze"])
        assert result.exit_code == 0
        data = _json.loads(result.output)
        assert data["findings"] == []

    def test_no_analyze_skips_findings_table_in_table_format(self) -> None:
        finding = Finding(severity="warning", message="dup")

        class _FakeAnalyzer:
            def analyze(self, packages):
                return [finding]

        analyzer_registry = {"dup": _FakeAnalyzer}
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", analyzer_registry):
            result_with = _runner().invoke(main, ["--format", "table"])
            result_without = _runner().invoke(main, ["--format", "table", "--no-analyze"])
        assert "Analysis Findings" in result_with.output
        assert "Analysis Findings" not in result_without.output

    def test_no_analyze_does_not_call_analyzers(self) -> None:
        called = []

        class _SpyAnalyzer:
            def analyze(self, packages):
                called.append(True)
                return []

        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        analyzer_registry = {"spy": _SpyAnalyzer}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", analyzer_registry):
            _runner().invoke(main, ["--no-analyze"])
        assert called == []


# ---------------------------------------------------------------------------
# Analyzer findings in output
# ---------------------------------------------------------------------------


class TestAnalyzerOutput:
    def test_findings_appear_in_json_output(self) -> None:
        finding = Finding(severity="warning", message="test finding")

        class _FakeAnalyzer:
            def analyze(self, packages):
                return [finding]

        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        analyzer_registry = {"fake": _FakeAnalyzer}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", analyzer_registry):
            result = _runner().invoke(main, ["--format", "json"])
        data = _json.loads(result.output)
        assert len(data["findings"]) == 1
        assert data["findings"][0]["message"] == "test finding"

    def test_findings_table_shown_in_table_format_when_findings_exist(self) -> None:
        finding = Finding(severity="warning", message="something bad")

        class _FakeAnalyzer:
            def analyze(self, packages):
                return [finding]

        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        analyzer_registry = {"fake": _FakeAnalyzer}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", analyzer_registry):
            result = _runner().invoke(main, ["--format", "table"])
        assert "Analysis Findings" in result.output
        assert "something bad" in result.output

    def test_findings_table_not_shown_when_no_findings(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, ["--format", "table"])
        assert "Analysis Findings" not in result.output

    def test_findings_sorted_by_severity_in_table(self) -> None:
        findings = [
            Finding(severity="info", message="info msg"),
            Finding(severity="warning", message="warn msg"),
        ]

        class _FakeAnalyzer:
            def analyze(self, packages):
                return findings

        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        analyzer_registry = {"fake": _FakeAnalyzer}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", analyzer_registry):
            result = _runner().invoke(main, ["--format", "table"])
        warn_pos = result.output.find("warn msg")
        info_pos = result.output.find("info msg")
        assert warn_pos < info_pos  # warnings before info


# ---------------------------------------------------------------------------
# Error handling and --skip-failing
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_collector_failure_exits_1_by_default(self) -> None:
        error = CollectorUnavailableError("mock", "not found")
        registry = {"mock": _make_collector_class(error=error)}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, [])
        assert result.exit_code == 1

    def test_collector_failure_warning_on_stderr(self) -> None:
        error = CollectorParseError("mock", "bad output")
        registry = {"mock": _make_collector_class(error=error)}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, [])
        assert "Warning" in result.output
        assert "mock" in result.output

    def test_skip_failing_gives_exit_0_despite_error(self) -> None:
        error = CollectorUnavailableError("mock", "not found")
        registry = {"mock": _make_collector_class(error=error)}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, ["--skip-failing"])
        assert result.exit_code == 0

    def test_skip_failing_still_prints_warning(self) -> None:
        error = CollectorParseError("mock", "bad output")
        registry = {"mock": _make_collector_class(error=error)}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, ["--skip-failing"])
        assert "Warning" in result.output

    def test_no_errors_exits_0(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, [])
        assert result.exit_code == 0

    def test_no_errors_no_warning_on_stderr(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, [])
        assert "Warning" not in result.output


# ---------------------------------------------------------------------------
# -v / --verbose flag
# ---------------------------------------------------------------------------


class TestVerboseFlag:
    def test_verbose_0_no_progress_on_stderr(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, [])
        assert "Running" not in result.output

    def test_verbose_1_shows_running_summary(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, ["-v"])
        assert "Running" in result.output
        assert "mock" in result.output

    def test_verbose_1_shows_finding_count(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, ["-v"])
        assert "finding(s)" in result.output

    def test_verbose_2_shows_per_collector_package_count(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg(), _pkg("git")])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, ["-vv"])
        assert result.exit_code == 0
        assert "mock" in result.output
        assert "package" in result.output

    def test_verbose_2_shows_per_analyzer_finding_count(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, ["-vv"])
        assert "finding(s)" in result.output

    def test_verbose_2_failed_collector_skips_package_count_line(self) -> None:
        error = CollectorUnavailableError("mock", "missing binary")
        registry = {"mock": _make_collector_class(error=error)}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, ["-vv", "--skip-failing"])
        assert result.exit_code == 0
        assert "Warning" in result.output
        assert "package(s) collected" not in result.output

    def test_verbose_2_mix_of_success_and_failure(self) -> None:
        pkg = _pkg("vim", "good")
        error = CollectorUnavailableError("bad", "not installed")
        registry = {
            "good": _make_collector_class("good", packages=[pkg]),
            "bad":  _make_collector_class("bad",  error=error),
        }
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry), \
             patch("env_audit.cli.ANALYZER_REGISTRY", _empty_analyzers()):
            result = _runner().invoke(main, ["-vv", "--skip-failing"])
        assert result.exit_code == 0
        assert "good" in result.output
        assert "Warning" in result.output

    def test_verbose_1_no_analyze_omits_finding_count(self) -> None:
        """--no-analyze skips analysis so no finding count line is emitted."""
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, ["-v", "--no-analyze"])
        assert "finding(s)" not in result.output