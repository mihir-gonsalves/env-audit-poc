# src/env_audit/cli.py
import click
from env_audit.collectors import AptCollector, ManualBinaryCollector, NpmCollector, PipCollector
from env_audit.orchestrator import Orchestrator
from env_audit.normalizer import Normalizer
from env_audit.renderers.json import JsonRenderer
from env_audit.renderers.table import TableRenderer

__all__ = ["main", "COLLECTOR_REGISTRY"]

COLLECTOR_REGISTRY: dict[str, type] = {
    "apt": AptCollector,
    "pip": PipCollector,
    "npm": NpmCollector,
    "manual": ManualBinaryCollector,
}

RENDERER_REGISTRY: dict[str, type] = {
    "json": JsonRenderer,
    "table": TableRenderer,
}

@click.command()
@click.option("--collectors", default=None, metavar="NAMES", help="Comma-separated collectors to run (default: all).")
@click.option("--format", "output_format", default="table", type=click.Choice(["json", "table"]), show_default=True, help="Output format.")
@click.option("--skip-failing", is_flag=True, default=False, help="Exit 0 even when one or more collectors fail.")
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v: collector summary, -vv: per-collector detail).")
def main(collectors: str | None, output_format: str, skip_failing: bool, verbose: int) -> None:
    """Audit installed packages across multiple ecosystems."""
    if collectors is not None:
        names = [n.strip() for n in collectors.split(",") if n.strip()]
        unknown = [n for n in names if n not in COLLECTOR_REGISTRY]
        if unknown:
            raise click.ClickException(f"Unknown collector(s): {', '.join(unknown)}")
        selected = [COLLECTOR_REGISTRY[n]() for n in names]
    else:
        selected = [cls() for cls in COLLECTOR_REGISTRY.values()]

    if verbose >= 1:
        ecosystems = ", ".join(c.ecosystem for c in selected)
        click.echo(f"Running {len(selected)} collector(s): {ecosystems}", err=True)

    result = Orchestrator(selected).run()

    for ecosystem, error in result.errors.items():
        click.echo(f"Warning: collector '{ecosystem}' failed: {error}", err=True)

    if verbose >= 2:
        for collector in selected:
            if collector.ecosystem not in result.errors:
                count = sum(1 for p in result.packages if p.ecosystem == collector.ecosystem)
                click.echo(f"  [{collector.ecosystem}] {count} package(s) collected", err=True)

    normalized = Normalizer().normalize(result.packages)
    click.echo(RENDERER_REGISTRY[output_format]().render(normalized.packages), nl=False)

    if result.errors and not skip_failing:
        raise SystemExit(1)