# src/env_audit/cli.py
from __future__ import annotations

import json as _json
from io import StringIO

import click
from rich.console import Console
from rich.table import Table

from env_audit.analyzers import (
    DuplicateAnalyzer,
    Finding,
    OrphanedBinaryAnalyzer,
    PathShadowAnalyzer,
)
from env_audit.collectors import AptCollector, ManualBinaryCollector, NpmCollector, PipCollector
from env_audit.normalizer import Normalizer
from env_audit.orchestrator import Orchestrator
from env_audit.renderers.json import JsonRenderer
from env_audit.renderers.table import TableRenderer

__all__ = ["main", "COLLECTOR_REGISTRY", "ANALYZER_REGISTRY"]

COLLECTOR_REGISTRY: dict[str, type] = {
    "apt": AptCollector,
    "pip": PipCollector,
    "npm": NpmCollector,
    "manual": ManualBinaryCollector,
}

ANALYZER_REGISTRY: dict[str, type] = {
    "duplicates": DuplicateAnalyzer,
    "path_shadow": PathShadowAnalyzer,
    "orphans": OrphanedBinaryAnalyzer,
}

RENDERER_REGISTRY: dict[str, type] = {
    "json": JsonRenderer,
    "table": TableRenderer,
}

# Severity display order for sorting findings in table output.
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _render_findings_table(findings: list[Finding], width: int = 120) -> str:
    """Return a Rich-formatted findings table string ending with a newline."""
    table = Table(title="Analysis Findings", show_header=True, header_style="bold")
    table.add_column("Severity", no_wrap=True)
    table.add_column("Kind", no_wrap=True)
    table.add_column("Message")

    sorted_findings = sorted(
        findings,
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), f.severity),
    )
    for f in sorted_findings:
        d = f.to_dict()
        table.add_row(f.severity, d.get("kind", "unknown"), f.message)

    buf = StringIO()
    Console(file=buf, highlight=False, no_color=True, width=width).print(table)
    return buf.getvalue()


@click.command()
@click.option("--collectors", default=None, metavar="NAMES",
              help="Comma-separated collectors to run (default: all).")
@click.option("--format", "output_format", default="table",
              type=click.Choice(["json", "table"]), show_default=True,
              help="Output format.")
@click.option("--skip-failing", is_flag=True, default=False,
              help="Exit 0 even when one or more collectors fail.")
@click.option("--no-analyze", is_flag=True, default=False,
              help="Skip the analysis step; output packages only.")
@click.option("-v", "--verbose", count=True,
              help="Increase verbosity (-v: summary + finding count, -vv: per-collector/analyzer detail).")
def main(
    collectors: str | None,
    output_format: str,
    skip_failing: bool,
    no_analyze: bool,
    verbose: int,
) -> None:
    """Audit installed packages across multiple ecosystems."""
    # ── Collector selection ────────────────────────────────────────────
    if collectors is not None:
        names = [n.strip() for n in collectors.split(",") if n.strip()]
        unknown = [n for n in names if n not in COLLECTOR_REGISTRY]
        if unknown:
            raise click.ClickException(f"Unknown collector(s): {', '.join(unknown)}")
        selected = [COLLECTOR_REGISTRY[n]() for n in names]
    else:
        selected = [cls() for cls in COLLECTOR_REGISTRY.values()]

    if verbose >= 1:
        click.echo(f"Running {len(selected)} collector(s): {', '.join(c.ecosystem for c in selected)}", err=True)

    # ── Collect ────────────────────────────────────────────────────────
    result = Orchestrator(selected).run()

    for ecosystem, error in result.errors.items():
        click.echo(f"Warning: collector '{ecosystem}' failed: {error}", err=True)

    if verbose >= 2:
        for collector in selected:
            if collector.ecosystem not in result.errors:
                count = sum(1 for p in result.packages if p.ecosystem == collector.ecosystem)
                click.echo(f"  [{collector.ecosystem}] {count} package(s) collected", err=True)

    # ── Normalize ──────────────────────────────────────────────────────
    normalized = Normalizer().normalize(result.packages)

    # ── Analyze ────────────────────────────────────────────────────────
    all_findings: list[Finding] = []

    if not no_analyze:
        analyzer_instances = [cls() for cls in ANALYZER_REGISTRY.values()]
        analyzer_findings: dict[str, list[Finding]] = {}

        for analyzer in analyzer_instances:
            key = type(analyzer).__name__
            findings = analyzer.analyze(normalized.packages)
            analyzer_findings[key] = findings
            all_findings.extend(findings)

        if verbose >= 1:
            click.echo(
                f"Analysis complete: {len(all_findings)} finding(s) across "
                f"{len(analyzer_instances)} analyzer(s)",
                err=True,
            )
        if verbose >= 2:
            for key, findings in analyzer_findings.items():
                click.echo(f"  [{key}] {len(findings)} finding(s)", err=True)

    # ── Render ─────────────────────────────────────────────────────────
    if output_format == "json":
        packages_data = [pkg.model_dump(mode="json") for pkg in normalized.packages]
        findings_data = [f.to_dict() for f in all_findings]
        click.echo(
            _json.dumps({"packages": packages_data, "findings": findings_data}, indent=2) + "\n",
            nl=False,
        )
    else:
        click.echo(TableRenderer().render(normalized.packages), nl=False)
        if all_findings:
            click.echo(_render_findings_table(all_findings), nl=False)

    # ── Exit code ──────────────────────────────────────────────────────
    if result.errors and not skip_failing:
        raise SystemExit(1)