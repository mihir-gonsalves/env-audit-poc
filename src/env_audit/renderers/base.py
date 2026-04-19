# src/env_audit/renderers/base.py
"""
Abstract base class for all output renderers.
"""

from abc import ABC, abstractmethod

from env_audit.models import PackageRecord

__all__ = ["Renderer"]


class Renderer(ABC):
    """
    Contract that every renderer must satisfy.

    A renderer takes a list of ``PackageRecord`` objects and converts
    them into a single string ready for display or file output.  It
    must never modify the records or perform I/O beyond string building.
    """

    @abstractmethod
    def render(self, packages: list[PackageRecord]) -> str:
        """
        Convert *packages* to a formatted string.

        The returned string should end with a newline so that callers
        can forward it to ``click.echo(..., nl=False)`` without losing
        the terminal cursor position.
        """