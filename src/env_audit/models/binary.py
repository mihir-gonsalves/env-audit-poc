# src/env_audit/models/binary.py
"""
Binary executable models for env-audit-poc.
"""

from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


class Confidence(str, Enum):
    """Confidence level for binary ownership attribution."""

    HIGH = "high"      # Binary directly provided by package manifest
    MEDIUM = "medium"  # Binary found via heuristics (naming, location)
    LOW = "low"        # Binary found but ownership unclear


class BinaryRecord(BaseModel):
    """
    Represents a binary executable associated with a package.

    Tracks the relationship between packages and their binaries,
    including confidence in the attribution and symlink resolution.
    """

    name: str = Field(description="Binary name (e.g., 'python3')")
    path: str = Field(description="Absolute path to the binary")
    confidence: Confidence = Field(description="Confidence in ownership attribution")
    is_symlink: bool = Field(description="Whether this binary is a symlink")
    symlink_target: str | None = Field(
        default=None, description="Target path if this is a symlink"
    )

    model_config = {"frozen": True}

    @field_validator("path")
    @classmethod
    def validate_absolute_path(cls, v: str) -> str:
        """Ensure path is absolute and non-root."""
        if not v.startswith("/"):
            raise ValueError(f"Path must be absolute, got: {v!r}")
        if v == "/":
            raise ValueError(f"Path must be non-root, got: {v!r}")
        return v

    @model_validator(mode="after")
    def validate_symlink_consistency(self) -> "BinaryRecord":
        """If is_symlink is True, symlink_target must be provided."""
        if self.is_symlink and self.symlink_target is None:
            raise ValueError("symlink_target must be provided when is_symlink=True")
        if not self.is_symlink and self.symlink_target is not None:
            raise ValueError("symlink_target must be None when is_symlink=False")
        return self