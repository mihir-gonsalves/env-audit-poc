# tests/test_models/test_metadata.py
"""
Tests for core data models.

These tests validate that the models enforce their contracts correctly.
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from env_audit.models import (
    InstallReason,
    PackageMetadata,
)


class TestPackageMetadata:
    """Tests for package metadata validation."""

    def test_minimal_metadata(self) -> None:
        """Test creating metadata with no fields."""
        metadata = PackageMetadata()
        assert metadata.install_date is None
        assert metadata.install_reason is None
        assert metadata.size_bytes is None
        assert metadata.extensions == {}

    def test_full_metadata(self) -> None:
        """Test creating metadata with all fields."""
        metadata = PackageMetadata(
            install_date=datetime(2024, 1, 15),
            install_reason=InstallReason.EXPLICIT,
            size_bytes=1024,
            extensions={"apt:architecture": "amd64"},
        )
        assert metadata.install_date == datetime(2024, 1, 15)
        assert metadata.install_reason == InstallReason.EXPLICIT
        assert metadata.size_bytes == 1024

    def test_extensions_must_be_namespaced(self) -> None:
        """Test that extension keys must include a colon."""
        with pytest.raises(ValidationError, match="must be namespaced"):
            PackageMetadata(extensions={"architecture": "amd64"})  # Missing namespace!

    def test_valid_namespaced_extensions(self) -> None:
        """Test that properly namespaced extensions are accepted."""
        metadata = PackageMetadata(
            extensions={
                "apt:architecture": "amd64",
                "apt:section": "python",
                "custom:field": "value",
            }
        )
        assert metadata.extensions["apt:architecture"] == "amd64"

    def test_size_cannot_be_negative(self) -> None:
        """Test that size must be non-negative."""
        with pytest.raises(ValidationError):
            PackageMetadata(size_bytes=-100)