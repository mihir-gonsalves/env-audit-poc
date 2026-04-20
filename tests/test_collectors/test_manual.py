# tests/test_collectors/test_manual.py
"""
100 % coverage tests for env_audit.collectors.manual.

Design principles
-----------------
* A temporary filesystem fixture is built using ``tmp_path`` — tests never
  read the live system.
* Every branch in ``collect()``, ``_scan_directory()``, the helper functions
  (``_is_executable_file``, ``_is_symlink``, ``_symlink_target``), and
  ``is_available()`` is exercised.
"""

import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from env_audit.collectors.manual import (
    ManualBinaryCollector,
    _is_executable_file,
    _is_symlink,
    _symlink_target,
    DEFAULT_SCAN_DIRS,
)
from env_audit.collectors.base import CollectorUnavailableError
from env_audit.models import Confidence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executable(path: Path) -> None:
    """Write a trivial shell script and make it executable."""
    path.write_text("#!/bin/sh\necho ok\n")
    path.chmod(0o755)


def _make_non_executable(path: Path) -> None:
    """Write a file with no execute permission."""
    path.write_text("data")
    path.chmod(0o644)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestIsExecutableFile:
    def test_regular_executable_returns_true(self, tmp_path: Path) -> None:
        f = tmp_path / "tool"
        _make_executable(f)
        assert _is_executable_file(f) is True

    def test_regular_non_executable_returns_false(self, tmp_path: Path) -> None:
        f = tmp_path / "data.txt"
        _make_non_executable(f)
        assert _is_executable_file(f) is False

    def test_directory_returns_false(self, tmp_path: Path) -> None:
        d = tmp_path / "subdir"
        d.mkdir()
        assert _is_executable_file(d) is False

    def test_oserror_returns_false(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "ghost"
        assert _is_executable_file(nonexistent) is False


class TestIsSymlink:
    def test_symlink_returns_true(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        _make_executable(target)
        link = tmp_path / "link"
        link.symlink_to(target)
        assert _is_symlink(link) is True

    def test_regular_file_returns_false(self, tmp_path: Path) -> None:
        f = tmp_path / "file"
        _make_executable(f)
        assert _is_symlink(f) is False

    def test_oserror_returns_false(self, tmp_path: Path) -> None:
        # Patch Path.is_symlink to raise OSError
        p = tmp_path / "x"
        with patch.object(Path, "is_symlink", side_effect=OSError("no perm")):
            assert _is_symlink(p) is False


class TestSymlinkTarget:
    def test_returns_resolved_target_for_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "real_tool"
        _make_executable(target)
        link = tmp_path / "tool"
        link.symlink_to(target)
        result = _symlink_target(link)
        assert result is not None
        assert "real_tool" in result

    def test_returns_none_for_regular_file(self, tmp_path: Path) -> None:
        f = tmp_path / "file"
        _make_executable(f)
        assert _symlink_target(f) is None

    def test_returns_none_on_oserror(self, tmp_path: Path) -> None:
        p = tmp_path / "x"
        with patch.object(Path, "is_symlink", side_effect=OSError("no perm")):
            assert _symlink_target(p) is None


# ---------------------------------------------------------------------------
# ecosystem / DEFAULT_SCAN_DIRS
# ---------------------------------------------------------------------------


class TestEcosystem:
    def test_returns_manual(self) -> None:
        assert ManualBinaryCollector().ecosystem == "manual"


class TestDefaultScanDirs:
    def test_default_scan_dirs_is_non_empty(self) -> None:
        assert len(DEFAULT_SCAN_DIRS) > 0

    def test_custom_scan_dirs_accepted(self, tmp_path: Path) -> None:
        c = ManualBinaryCollector(scan_dirs=(str(tmp_path),))
        assert c._scan_dirs == (str(tmp_path),)


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_true_when_at_least_one_dir_exists(self, tmp_path: Path) -> None:
        c = ManualBinaryCollector(scan_dirs=(str(tmp_path), "/nonexistent_xyz"))
        assert c.is_available() is True

    def test_false_when_no_dir_exists(self) -> None:
        c = ManualBinaryCollector(scan_dirs=("/nonexistent_xyz_1", "/nonexistent_xyz_2"))
        assert c.is_available() is False


# ---------------------------------------------------------------------------
# collect() — high-level contract
# ---------------------------------------------------------------------------


class TestCollect:
    def test_raises_unavailable_when_no_dirs_exist(self) -> None:
        c = ManualBinaryCollector(scan_dirs=("/no/such/dir",))
        with pytest.raises(CollectorUnavailableError) as exc_info:
            c.collect()
        assert exc_info.value.ecosystem == "manual"
        assert "none of the scan directories" in exc_info.value.reason

    def test_returns_empty_when_dir_is_empty(self, tmp_path: Path) -> None:
        c = ManualBinaryCollector(scan_dirs=(str(tmp_path),))
        assert c.collect() == []

    def test_skips_nonexistent_dirs_gracefully(self, tmp_path: Path) -> None:
        _make_executable(tmp_path / "tool")
        c = ManualBinaryCollector(
            scan_dirs=(str(tmp_path), str(tmp_path / "nonexistent"))
        )
        records = c.collect()
        assert len(records) == 1

    def test_finds_executables(self, tmp_path: Path) -> None:
        _make_executable(tmp_path / "mytool")
        _make_executable(tmp_path / "anothertool")
        c = ManualBinaryCollector(scan_dirs=(str(tmp_path),))
        records = c.collect()
        names = [r.name for r in records]
        assert "mytool" in names
        assert "anothertool" in names

    def test_skips_non_executables(self, tmp_path: Path) -> None:
        _make_executable(tmp_path / "tool")
        _make_non_executable(tmp_path / "readme.txt")
        c = ManualBinaryCollector(scan_dirs=(str(tmp_path),))
        records = c.collect()
        assert len(records) == 1
        assert records[0].name == "tool"

    def test_skips_directories(self, tmp_path: Path) -> None:
        _make_executable(tmp_path / "tool")
        sub = tmp_path / "subdir"
        sub.mkdir()
        c = ManualBinaryCollector(scan_dirs=(str(tmp_path),))
        records = c.collect()
        assert len(records) == 1

    def test_combines_multiple_dirs(self, tmp_path: Path) -> None:
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        _make_executable(dir_a / "tool_a")
        _make_executable(dir_b / "tool_b")
        c = ManualBinaryCollector(scan_dirs=(str(dir_a), str(dir_b)))
        records = c.collect()
        names = [r.name for r in records]
        assert "tool_a" in names
        assert "tool_b" in names

    def test_record_fields(self, tmp_path: Path) -> None:
        _make_executable(tmp_path / "mytool")
        c = ManualBinaryCollector(scan_dirs=(str(tmp_path),))
        records = c.collect()
        pkg = records[0]
        assert pkg.name == "mytool"
        assert pkg.ecosystem == "manual"
        assert pkg.source == str(tmp_path)
        assert pkg.version_raw is None
        assert pkg.version_parsed is None
        assert len(pkg.binaries) == 1

    def test_binary_record_fields(self, tmp_path: Path) -> None:
        _make_executable(tmp_path / "mytool")
        c = ManualBinaryCollector(scan_dirs=(str(tmp_path),))
        binary = c.collect()[0].binaries[0]
        assert binary.name == "mytool"
        assert binary.path == str(tmp_path / "mytool")
        assert binary.confidence == Confidence.MEDIUM
        assert binary.is_symlink is False
        assert binary.symlink_target is None

    def test_symlink_recorded_correctly(self, tmp_path: Path) -> None:
        target = tmp_path / "real_tool"
        _make_executable(target)
        link = tmp_path / "link_tool"
        link.symlink_to(target)
        c = ManualBinaryCollector(scan_dirs=(str(tmp_path),))
        records = c.collect()
        by_name = {r.name: r for r in records}
        link_record = by_name["link_tool"]
        binary = link_record.binaries[0]
        assert binary.is_symlink is True
        assert binary.symlink_target is not None
        assert "real_tool" in binary.symlink_target

    def test_output_is_sorted_alphabetically(self, tmp_path: Path) -> None:
        for name in ["zzz", "aaa", "mmm"]:
            _make_executable(tmp_path / name)
        c = ManualBinaryCollector(scan_dirs=(str(tmp_path),))
        names = [r.name for r in c.collect()]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# _scan_directory — OSError handling
# ---------------------------------------------------------------------------


class TestScanDirectory:
    def test_oserror_on_iterdir_returns_empty(self, tmp_path: Path) -> None:
        c = ManualBinaryCollector(scan_dirs=(str(tmp_path),))
        with patch.object(Path, "iterdir", side_effect=OSError("permission denied")):
            result = c._scan_directory(tmp_path)
        assert result == []