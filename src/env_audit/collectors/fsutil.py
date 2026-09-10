# src/env_audit/collectors/fsutil.py
"""
Filesystem helpers shared by collectors that inspect files on disk.

Two collectors need to look at real paths:

* ``ManualBinaryCollector`` scans directories for executables.
* ``PipCollector`` resolves console-script paths out of each package's
  ``RECORD`` manifest.

Both need the same three questions answered about a path, and both need
those answers to degrade gracefully rather than raise.  Every function
here swallows ``OSError`` and returns a safe default: an audit must never
abort because one file in one directory could not be stat-ed.
"""

import stat
from pathlib import Path

__all__ = ["is_executable_file", "is_symlink", "symlink_target"]


def is_executable_file(path: Path) -> bool:
    """Return True if *path* is a regular file with any execute bit set."""
    try:
        st = path.stat()
        return stat.S_ISREG(st.st_mode) and bool(st.st_mode & 0o111)
    except OSError:
        return False


def is_symlink(path: Path) -> bool:
    """Return True if *path* is a symbolic link (lstat does not follow)."""
    try:
        return path.is_symlink()
    except OSError:
        return False


def symlink_target(path: Path) -> str | None:
    """Return the symlink target as a string, or None if not a symlink."""
    try:
        if path.is_symlink():
            return str(path.resolve())
    except OSError:
        pass
    return None
