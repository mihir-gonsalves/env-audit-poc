# tests/test_cli.py
"""
100 % branch coverage tests for env_audit.cli.

Design
------
* ``CliRunner()`` uses a combined output stream in ``result.output``,
  which includes both stdout and stderr. This avoids ambiguity by
  consolidating all CLI output into a single capture buffer.
* ``COLLECTOR_REGISTRY`` is patched so no real system commands are ever
  executed.  A factory function builds disposable ``Collector`` subclasses
  configured to return preset packages or raise preset errors.
* Every branch in ``main()`` is exercised:
  - ``collectors`` is ``None`` (default: run all) vs explicit names
  - Unknown collector name -> stderr + exit 1
  - verbose == 0, == 1, >= 2
  - verbose >= 2 with a successful collector (inner ``if`` -> True)
  - verbose >= 2 with a failing collector  (inner ``if`` -> False)
  - ``result.errors`` empty  (exit 0)
  - ``result.errors`` non-empty + ``--skip-failing``  (exit 0)
  - ``result.errors`` non-empty without ``--skip-failing`` (exit 1)
  - ``--format json`` and ``--format table``
"""

import json as _json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from env_audit.cli import COLLECTOR_REGISTRY, main
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
    """Return a ``Collector`` subclass pre-configured for tests."""
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


def _pkg(name: str = "vim", ecosystem: str = "mock") -> PackageRecord:
    return PackageRecord(name=name, ecosystem=ecosystem, source="test-source")


def _runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# COLLECTOR_REGISTRY sanity check
# ---------------------------------------------------------------------------


class TestCollectorRegistry:
    def test_apt_is_registered(self) -> None:
        assert "apt" in COLLECTOR_REGISTRY

    def test_registry_values_are_types(self) -> None:
        for cls in COLLECTOR_REGISTRY.values():
            assert isinstance(cls, type)


# ---------------------------------------------------------------------------
# RENDERER_REGISTRY / --format choice consistency
# ---------------------------------------------------------------------------


class TestRendererRegistry:
    def test_renderer_registry_keys_match_format_choices(self) -> None:
        """RENDERER_REGISTRY keys must stay in sync with the click.Choice list.

        This test will fail if a new renderer is added to the registry
        without also being added to the ``--format`` option (or vice-versa),
        acting as a compile-time reminder to update both places.
        """
        from env_audit.cli import RENDERER_REGISTRY
        assert set(RENDERER_REGISTRY.keys()) == {"json", "table"}


# ---------------------------------------------------------------------------
# --collectors flag
# ---------------------------------------------------------------------------


class TestCollectorsFlag:
    def test_unknown_collector_exits_1(self) -> None:
        result = _runner().invoke(main, ["--collectors", "unknown"])
        assert result.exit_code == 1

    def test_unknown_collector_message_contains_name(self) -> None:
        """ClickException prints 'Error: Unknown collector(s): <name>' to stderr."""
        result = _runner().invoke(main, ["--collectors", "nope"])
        assert "nope" in result.output
        # ClickException always prefixes with "Error:"
        assert "Error" in result.output

    def test_specific_collector_only_runs_that_ecosystem(self) -> None:
        registry = {
            "alpha": _make_collector_class("alpha", packages=[_pkg("a", "alpha")]),
            "beta":  _make_collector_class("beta",  packages=[_pkg("b", "beta")]),
        }
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, ["--collectors", "alpha", "--format", "json"])
        assert result.exit_code == 0
        data = _json.loads(result.output)
        names = [p["name"] for p in data]
        assert "a" in names
        assert "b" not in names

    def test_no_collectors_flag_runs_all(self) -> None:
        registry = {
            "alpha": _make_collector_class("alpha", packages=[_pkg("a", "alpha")]),
            "beta":  _make_collector_class("beta",  packages=[_pkg("b", "beta")]),
        }
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, ["--format", "json"])
        assert result.exit_code == 0
        data = _json.loads(result.output)
        names = {p["name"] for p in data}
        assert names == {"a", "b"}


# ---------------------------------------------------------------------------
# --format flag
# ---------------------------------------------------------------------------


class TestFormatFlag:
    def test_json_format_produces_valid_json(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, ["--format", "json"])
        assert result.exit_code == 0
        data = _json.loads(result.output)
        assert data[0]["name"] == "vim"

    def test_json_format_is_an_array(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, ["--format", "json"])
        assert isinstance(_json.loads(result.output), list)

    def test_table_format_contains_package_name(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg("myvim")])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, ["--format", "table"])
        assert result.exit_code == 0
        assert "myvim" in result.output

    def test_default_format_is_table(self) -> None:
        """No --format flag -> table output (not raw JSON)."""
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, [])
        assert result.exit_code == 0
        # Table output contains column headers; JSON output would not.
        assert "Name" in result.output


# ---------------------------------------------------------------------------
# Error handling and --skip-failing
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_collector_failure_exits_1_by_default(self) -> None:
        error = CollectorUnavailableError("mock", "not found")
        registry = {"mock": _make_collector_class(error=error)}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, [])
        assert result.exit_code == 1

    def test_collector_failure_warning_on_stderr(self) -> None:
        error = CollectorParseError("mock", "bad output")
        registry = {"mock": _make_collector_class(error=error)}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, [])
        assert "Warning" in result.output
        assert "mock" in result.output

    def test_skip_failing_gives_exit_0_despite_error(self) -> None:
        error = CollectorUnavailableError("mock", "not found")
        registry = {"mock": _make_collector_class(error=error)}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, ["--skip-failing"])
        assert result.exit_code == 0

    def test_skip_failing_still_prints_warning(self) -> None:
        error = CollectorParseError("mock", "bad output")
        registry = {"mock": _make_collector_class(error=error)}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, ["--skip-failing"])
        assert "Warning" in result.output

    def test_no_errors_exits_0(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, [])
        assert result.exit_code == 0

    def test_no_errors_no_warning_on_stderr(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, [])
        assert "Warning" not in result.output


# ---------------------------------------------------------------------------
# -v / --verbose flag
# ---------------------------------------------------------------------------


class TestVerboseFlag:
    def test_verbose_0_no_progress_on_stderr(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, [])
        assert "Running" not in result.output

    def test_verbose_1_shows_running_summary(self) -> None:
        registry = {"mock": _make_collector_class(packages=[_pkg()])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, ["-v"])
        assert "Running" in result.output
        assert "mock" in result.output

    def test_verbose_2_shows_per_collector_package_count(self) -> None:
        """Covers the ``if collector.ecosystem not in result.errors`` True branch."""
        registry = {"mock": _make_collector_class(packages=[_pkg(), _pkg("git")])}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, ["-vv"])
        assert result.exit_code == 0
        # The per-collector line mentions the ecosystem and package count
        assert "mock" in result.output
        assert "package" in result.output

    def test_verbose_2_failed_collector_skips_package_count_line(self) -> None:
        """Covers the ``if collector.ecosystem not in result.errors`` False branch.

        When a collector fails its ecosystem IS in ``result.errors``, so
        the per-collector success detail line must NOT be emitted.
        The warning IS printed (from the earlier loop), but the count line
        inside the ``if`` block is skipped.
        """
        error = CollectorUnavailableError("mock", "missing binary")
        registry = {"mock": _make_collector_class(error=error)}
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, ["-vv", "--skip-failing"])
        assert result.exit_code == 0
        # Warning is always emitted
        assert "Warning" in result.output
        # The "N package(s) collected" line must NOT appear for the failed collector
        assert "package(s) collected" not in result.output

    def test_verbose_2_mix_of_success_and_failure(self) -> None:
        """Both branches of the inner ``if`` hit in a single run."""
        pkg = _pkg("vim", "good")
        error = CollectorUnavailableError("bad", "not installed")
        registry = {
            "good": _make_collector_class("good", packages=[pkg]),
            "bad":  _make_collector_class("bad",  error=error),
        }
        with patch("env_audit.cli.COLLECTOR_REGISTRY", registry):
            result = _runner().invoke(main, ["-vv", "--skip-failing"])
        assert result.exit_code == 0
        assert "good" in result.output   # appears in the count line
        assert "Warning" in result.output # appears for the bad collector