# tests/test_models/test_binary.py
"""
Tests for core data models.

These tests validate that the models enforce their contracts correctly.
"""

import pytest
from pydantic import ValidationError

from env_audit.models import (
    BinaryRecord,
    Confidence,
)


class TestBinaryRecord:
    """Tests for binary record validation."""

    def test_valid_binary(self) -> None:
        """Test creating a valid binary record."""
        binary = BinaryRecord(
            name="python3",
            path="/usr/bin/python3",
            confidence=Confidence.HIGH,
            is_symlink=False,
            symlink_target=None,
        )
        assert binary.name == "python3"
        assert binary.confidence == Confidence.HIGH

    def test_symlink_requires_target(self) -> None:
        """Test that symlinks must provide a target."""
        with pytest.raises(ValidationError, match="symlink_target must be provided"):
            BinaryRecord(
                name="python3",
                path="/usr/bin/python3",
                confidence=Confidence.HIGH,
                is_symlink=True,
                symlink_target=None,  # Invalid!
            )

    def test_non_symlink_cannot_have_target(self) -> None:
        """Test that non-symlinks cannot have a target."""
        with pytest.raises(ValidationError, match="symlink_target must be None"):
            BinaryRecord(
                name="python3",
                path="/usr/bin/python3",
                confidence=Confidence.HIGH,
                is_symlink=False,
                symlink_target="/usr/bin/python3.11",  # Invalid!
            )

    def test_path_must_be_absolute(self) -> None:
        """Test that paths must be absolute."""
        with pytest.raises(ValidationError, match="Path must be absolute"):
            BinaryRecord(
                name="python3",
                path="usr/bin/python3",  # Missing leading /
                confidence=Confidence.HIGH,
                is_symlink=False,
            )

    def test_path_cannot_be_root(self) -> None:
        """Test that '/' alone is rejected as a binary path."""
        with pytest.raises(ValidationError, match="Path must be non-root"):
            BinaryRecord(
                name="python3",
                path="/",
                confidence=Confidence.HIGH,
                is_symlink=False,
            )

    def test_binary_immutable(self) -> None:
        """Test that binary records are immutable."""
        binary = BinaryRecord(
            name="python3",
            path="/usr/bin/python3",
            confidence=Confidence.HIGH,
            is_symlink=False,
        )
        with pytest.raises(ValidationError):
            binary.path = "/usr/local/bin/python3"  # type: ignore