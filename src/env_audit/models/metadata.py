# src/env_audit/models/metadata.py
"""
Package metadata models for env-audit-poc.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class InstallReason(str, Enum):
    """Why a package was installed."""

    EXPLICIT = "explicit"      # User explicitly installed
    DEPENDENCY = "dependency"  # Installed as a dependency
    UNKNOWN = "unknown"        # Cannot determine


class PackageMetadata(BaseModel):
    """
    Package metadata with typed core fields and flexible extensions.

    Core fields provide a consistent interface for analyzers.
    Extensions allow ecosystem-specific data using namespaced keys.
    """

    install_date: datetime | None = Field(
        default=None, description="When the package was installed"
    )
    install_reason: InstallReason | None = Field(
        default=None, description="Why the package was installed"
    )
    size_bytes: int | None = Field(
        default=None, description="Size of installed package in bytes", ge=0
    )
    extensions: dict[str, Any] = Field(
        default_factory=dict,
        description="Ecosystem-specific metadata (must use 'ecosystem:key' format)",
    )

    model_config = {"frozen": True}

    @field_validator("extensions")
    @classmethod
    def validate_extension_namespaces(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Ensure all extension keys use the 'ecosystem:key' format."""
        for key in v.keys():
            if ":" not in key:
                raise ValueError(
                    f"Extension key '{key}' must be namespaced (e.g., 'apt:architecture')"
                )
        return v