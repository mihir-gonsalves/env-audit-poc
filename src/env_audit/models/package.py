# src/env_audit/models/package.py
"""
Core package record models for env-audit.
"""

import functools

from pydantic import BaseModel, Field, field_validator

from .binary import BinaryRecord  # noqa: F401 (re-exported)
from .metadata import PackageMetadata  # noqa: F401 (re-exported)


@functools.total_ordering
class SemVer(BaseModel):
    """
    Semantic version representation.

    Used for version comparison and sorting. Not all versions can be
    parsed as semantic versions.

    Note: prerelease comparison follows SemVer spec ordering where
    presence of a prerelease lowers precedence (1.0.0-alpha < 1.0.0).
    Ubuntu epoch/revision strings (e.g. '1ubuntu1') are treated as
    opaque prerelease labels for ordering purposes.
    """

    major: int
    minor: int
    patch: int
    prerelease: str | None = None
    build: str | None = None

    model_config = {"frozen": True}

    def __str__(self) -> str:
        """String representation of the version."""
        version = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            version += f"-{self.prerelease}"
        if self.build:
            version += f"+{self.build}"
        return version

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return (
            self.major,
            self.minor,
            self.patch,
            self.prerelease,
        ) == (
            other.major,
            other.minor,
            other.patch,
            other.prerelease,
        )

    def __lt__(self, other: "SemVer") -> bool:
        """
        Compare versions per SemVer spec.

        Core version takes priority. When cores are equal, a release
        version (no prerelease) is greater than any prerelease.
        """
        if not isinstance(other, SemVer):
            return NotImplemented

        self_core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)

        if self_core != other_core:
            return self_core < other_core

        # Equal cores: prerelease lowers precedence per SemVer spec.
        # None (release) > any prerelease string.
        if self.prerelease is None and other.prerelease is not None:
            return False  # self is release, other is prerelease → self > other
        if self.prerelease is not None and other.prerelease is None:
            return True   # self is prerelease, other is release → self < other

        # Both have prerelease or both don't — compare lexicographically.
        return (self.prerelease or "") < (other.prerelease or "")

    def __hash__(self) -> int:
        return hash((self.major, self.minor, self.patch, self.prerelease))


class PackageRecord(BaseModel):
    """
    Canonical representation of an installed package.

    This is the core data structure that all collectors normalize to
    and all analyzers operate on.
    """

    name: str = Field(description="Package name")
    version_raw: str | None = Field(
        default=None, description="Original version string from source"
    )
    version_parsed: SemVer | None = Field(
        default=None, description="Parsed semantic version (best-effort)"
    )
    ecosystem: str = Field(description="Source ecosystem (e.g., 'apt', 'pip', 'npm')")
    source: str = Field(
        description="Specific source within ecosystem (e.g., 'universe', 'pypi')"
    )
    install_path: str | None = Field(
        default=None, description="Installation directory path"
    )
    binaries: list[BinaryRecord] = Field(
        default_factory=list, description="Binaries provided by this package"
    )
    metadata: PackageMetadata = Field(
        default_factory=PackageMetadata, description="Package metadata"
    )

    model_config = {"frozen": True}

    @field_validator("ecosystem")
    @classmethod
    def validate_ecosystem_lowercase(cls, v: str) -> str:
        """Ensure ecosystem is lowercase for consistency."""
        return v.lower()

    @field_validator("source")
    @classmethod
    def validate_source_nonempty(cls, v: str) -> str:
        """Ensure source is not blank."""
        if not v.strip():
            raise ValueError("source must not be blank")
        return v

    def display_version(self) -> str:
        """
        Get the best available version string for display.

        Prefers parsed version, falls back to raw version.
        """
        if self.version_parsed:
            return str(self.version_parsed)
        if self.version_raw:
            return self.version_raw
        return "unknown"