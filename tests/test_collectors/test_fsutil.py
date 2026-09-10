# tests/test_collectors/test_fsutil.py
"""
100 % coverage tests for env_audit.collectors.fsutil.

Design principles
-----------------
* A temporary filesystem fixture is built using ``tmp_path`` - tests never
  read the live system.
* Every branch, including each ``OSError`` guard, is exercised.  These
  helpers exist to make filesystem access non-fatal, so the failure paths
  matter more than the happy paths.
"""

from pathlib import Path
from unittest.mock import patch

from env_audit.collectors.fsutil import is_executable_file, is_symlink, symlink_target

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
# is_executable_file
# ---------------------------------------------------------------------------


class TestIsExecutableFile:
    def test_regular_executable_returns_true(self, tmp_path: Path) -> None:
        f = tmp_path / "tool"
        _make_executable(f)
        assert is_executable_file(f) is True

    def test_regular_non_executable_returns_false(self, tmp_path: Path) -> None:
        f = tmp_path / "data.txt"
        _make_non_executable(f)
        assert is_executable_file(f) is False

    def test_directory_returns_false(self, tmp_path: Path) -> None:
        d = tmp_path / "subdir"
        d.mkdir()
        assert is_executable_file(d) is False

    def test_oserror_returns_false(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "ghost"
        assert is_executable_file(nonexistent) is False


# ---------------------------------------------------------------------------
# is_symlink
# ---------------------------------------------------------------------------


class TestIsSymlink:
    def test_symlink_returns_true(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        _make_executable(target)
        link = tmp_path / "link"
        link.symlink_to(target)
        assert is_symlink(link) is True

    def test_regular_file_returns_false(self, tmp_path: Path) -> None:
        f = tmp_path / "file"
        _make_executable(f)
        assert is_symlink(f) is False

    def test_oserror_returns_false(self, tmp_path: Path) -> None:
        p = tmp_path / "x"
        with patch.object(Path, "is_symlink", side_effect=OSError("no perm")):
            assert is_symlink(p) is False


# ---------------------------------------------------------------------------
# symlink_target
# ---------------------------------------------------------------------------


class TestSymlinkTarget:
    def test_returns_resolved_target_for_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "real_tool"
        _make_executable(target)
        link = tmp_path / "tool"
        link.symlink_to(target)
        result = symlink_target(link)
        assert result is not None
        assert "real_tool" in result

    def test_returns_none_for_regular_file(self, tmp_path: Path) -> None:
        f = tmp_path / "file"
        _make_executable(f)
        assert symlink_target(f) is None

    def test_returns_none_for_missing_path(self, tmp_path: Path) -> None:
        assert symlink_target(tmp_path / "ghost") is None

    def test_returns_none_on_oserror(self, tmp_path: Path) -> None:
        p = tmp_path / "x"
        with patch.object(Path, "is_symlink", side_effect=OSError("no perm")):
            assert symlink_target(p) is None
