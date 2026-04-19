# src/env_audit/cli.py
"""
Click-based CLI for env-audit-poc.

Entry point: ``env-audit-poc`` (wired in pyproject.toml).
"""

import sys

import click

from env_audit.collectors import AptCollector
from env_audit.orchestrator import Orchestrator
from env_audit.renderers.json import JsonRenderer
from env_audit.renderers.table import TableRenderer

__all__ = ["main", "COLLECTOR_REGISTRY", "RENDERER_REGISTRY"]

# ---------------------------------------------------------------------------
# Registries — extend these as new collectors / renderers are added.
#
# COLLECTOR_REGISTRY: maps ecosystem name -> Collector subclass.
#   Patched in tests to avoid touching the live system.
#
# RENDERER_REGISTRY: maps --format value -> Renderer subclass.
#   Keys here must stay in sync with the click.Choice list on --format.
#   Exported so tests can assert that the two stay consistent.
# ---------------------------------------------------------------------------

COLLECTOR_REGISTRY: dict[str, type] = {
    "apt": AptCollector,
}

RENDERER_REGISTRY: dict[str, type] = {
    "json": JsonRenderer,
    "table": TableRenderer,
}


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command()
@click.option(
    "--collectors",
    default=None,
    metavar="NAMES",
    help="Comma-separated collectors to run (default: all).",
)
@click.option(
    "--format",
    "output_format",
    default="table",
    type=click.Choice(["json", "table"]),
    show_default=True,
    help="Output format.",
)
@click.option(
    "--skip-failing",
    is_flag=True,
    default=False,
    help="Exit 0 even when one or more collectors fail.",
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Increase verbosity (-v: collector summary, -vv: per-collector detail).",
)
def main(
    collectors: str | None,
    output_format: str,
    skip_failing: bool,
    verbose: int,
) -> None:
    """Audit installed packages across multiple ecosystems."""

    # ------------------------------------------------------------------
    # Resolve which collectors to run
    # ------------------------------------------------------------------
    if collectors is not None:
        names = [n.strip() for n in collectors.split(",") if n.strip()]
        unknown = [n for n in names if n not in COLLECTOR_REGISTRY]
        if unknown:
            raise click.ClickException(
                f"Unknown collector(s): {', '.join(unknown)}"
            )
        selected = [COLLECTOR_REGISTRY[n]() for n in names]
    else:
        selected = [cls() for cls in COLLECTOR_REGISTRY.values()]

    if verbose >= 1:
        ecosystems = ", ".join(c.ecosystem for c in selected)
        click.echo(f"Running {len(selected)} collector(s): {ecosystems}", err=True)

    # ------------------------------------------------------------------
    # Run the audit
    # ------------------------------------------------------------------
    result = Orchestrator(selected).run()

    # Always surface collector failures on stderr
    for ecosystem, error in result.errors.items():
        click.echo(f"Warning: collector '{ecosystem}' failed: {error}", err=True)

    # Per-collector package counts at -vv
    if verbose >= 2:
        for collector in selected:
            if collector.ecosystem not in result.errors:
                count = sum(
                    1 for p in result.packages if p.ecosystem == collector.ecosystem
                )
                click.echo(
                    f"  [{collector.ecosystem}] {count} package(s) collected",
                    err=True,
                )

    # ------------------------------------------------------------------
    # Render and emit output
    # ------------------------------------------------------------------
    click.echo(RENDERER_REGISTRY[output_format]().render(result.packages), nl=False)

    if result.errors and not skip_failing:
        sys.exit(1)