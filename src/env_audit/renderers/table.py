# src/env_audit/renderers/table.py
"""
Terminal table renderer for env-audit-poc, powered by Rich.
"""

from io import StringIO

from rich.console import Console
from rich.table import Table

from env_audit.models import PackageRecord

from .base import Renderer

__all__ = ["TableRenderer"]


class TableRenderer(Renderer):
    """
    Render packages as a Rich terminal table.

    The table is written to an in-memory buffer so that the output can
    be captured and forwarded by the CLI without touching stdout directly.
    Colour codes are suppressed (``no_color=True``) to ensure clean output
    when the result is piped or redirected.
    """

    def __init__(self, width: int = 120) -> None:
        self._width = width

    def render(self, packages: list[PackageRecord]) -> str:
        """Return a formatted table string ending with ``'\\n'``."""
        table = Table(
            title="Installed Packages",
            show_header=True,
            header_style="bold",
        )
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Version")
        table.add_column("Ecosystem", style="green")
        table.add_column("Source")
        table.add_column("Install Reason")

        for pkg in packages:
            reason = (
                pkg.metadata.install_reason.value
                if pkg.metadata.install_reason is not None
                else "-"
            )
            table.add_row(
                pkg.name,
                pkg.display_version(),
                pkg.ecosystem,
                pkg.source,
                reason,
            )

        buf = StringIO()
        console = Console(
            file=buf,
            highlight=False,
            no_color=True,
            width=self._width,
        )
        console.print(table)
        return buf.getvalue()