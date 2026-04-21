# env-audit-poc

`env-audit-poc` is a read-only, developer-focused environment auditing tool that produces a unified, explainable inventory of software installed on a machine across multiple package ecosystems.

It is designed for developers who have accumulated tools over time using different package managers, manual installs, and version managers, and want a clear, trustworthy view of what is installed, where it came from, and where duplication or shadowing exists.

This project prioritizes correctness, transparency, and extensibility over automation or cleanup.

> **Note:** This project is a proof of concept and will eventually be rewritten in Go for performance reasons.

## How To Use

### Installation

Create and activate a virtual environment, then install in editable mode:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Basic Usage

Run a full audit across all supported ecosystems:

```bash
env-audit-poc
```

This collects packages from `apt`, `pip`, `npm`, and manual binary directories, normalizes the results, runs all analyzers, and prints a package table followed by an analysis findings table.

### Common Invocations

| Goal | Command |
|------|---------|
| Full audit, default table output | `env-audit-poc` |
| Machine-readable JSON | `env-audit-poc --format json` |
| Specific ecosystems only | `env-audit-poc --collectors apt,pip` |
| Skip the analysis step | `env-audit-poc --no-analyze` |
| Continue if a collector fails | `env-audit-poc --skip-failing` |
| Collector summary (stderr) | `env-audit-poc -v` |
| Full per-collector detail (stderr) | `env-audit-poc -vv` |
| Redirect output to a file | `env-audit-poc --format json > audit.json` |

### Understanding the Output

**Package table** — one row per unique `(ecosystem, name)` pair after normalization. Intra-ecosystem duplicates are collapsed to the highest parsed version.

**Analysis Findings table** — printed below the package table when any findings exist. Findings are sorted by severity (`warning` before `info`). Each row shows:

- **Severity** — `warning` or `info`
- **Kind** — the finding type (`cross_ecosystem_duplicate`, `path_shadow`, `orphaned_binary`)
- **Message** — a human-readable explanation

**JSON output** — a top-level object with two keys:

```json
{
  "packages": [ { "name": "...", "ecosystem": "...", ... } ],
  "findings": [ { "kind": "...", "severity": "...", "message": "..." } ]
}
```

### Verbosity Flags

`-v` prints a one-line collector summary and total finding count to stderr — useful when piping stdout elsewhere.

`-vv` adds per-collector package counts and per-analyzer finding counts to stderr.

Both flags write to stderr so they never pollute piped output.

## Problem Statement

Modern development environments accumulate technical debt:

- System packages installed via OS package managers
- Language-specific tools installed globally
- Multiple versions of the same runtime
- Manually installed binaries that are no longer tracked
- PATH collisions that hide tools unintentionally

There is no single tool that provides a complete, ecosystem-agnostic view of a developer machine.

`env-audit-poc` addresses this gap by generating a unified inventory and highlighting risks and redundancies without modifying the system.

## Non-Goals

This tool intentionally does **not**:

- Automatically uninstall or modify packages
- Guarantee perfect detection of unused tools
- Replace language-specific environment managers
- Act as a configuration management system

All output is advisory and explainable.

## Key Features

- Unified inventory across multiple installation sources
- Immutable, normalized data model
- Read-only by default
- Deterministic output (at audit time)
- Explainable insights (no black-box behavior)
- Extensible plugin architecture
- Machine-readable and human-readable output formats
- Graceful degradation when collectors fail

## Supported Ecosystems

- System packages (`apt`)
- Python (`pip`, system and user)
- Node.js (`npm -g`)
- Manually installed binaries (`/usr/local/bin`, `~/bin`, `~/.local/bin`)

Support for additional ecosystems is straightforward to add via the plugin architecture.

## Architecture Overview

`env-audit-poc` is structured as a layered, composable pipeline:

```
 ------------------
|       CLI        |
 ------------------
         ↓
 ------------------
|   Orchestrator   |
 ------------------
         ↓
 ------------------
|    Collectors    |  ← Plugin-based, fail independently
 ------------------
         ↓
 ------------------
|    Normalizer    |
 ------------------
         ↓
 ------------------
|    Analyzers     |
 ------------------
         ↓
 ------------------
|    Renderers     |
 ------------------
```

Each layer has a single responsibility and is independently testable.

### Collectors

Collectors gather raw data from a single installation source. They do not deduplicate, analyze, or infer intent.

All collectors implement the `Collector` interface:

```python
class Collector(ABC):
    DEFAULT_TIMEOUT: float = 30.0

    @property
    @abstractmethod
    def ecosystem(self) -> str:
        """Unique lowercase identifier (e.g., 'apt', 'pip', 'npm')"""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this collector can run on the current system."""

    @abstractmethod
    def collect(self) -> list[PackageRecord]:
        """Gather and normalize package data. Raises CollectorError on failure."""
```

Collectors raise typed errors that the orchestrator catches per-collector:

```python
CollectorError
├── CollectorUnavailableError(ecosystem, reason)
├── CollectorTimeoutError(ecosystem, timeout)
└── CollectorParseError(ecosystem, detail)
```

### Normalizer

The normalization layer converts heterogeneous raw data into a canonical, sorted, deduplicated list.

- Collapses intra-ecosystem duplicates (keeps highest parsed version)
- Sorts by `(ecosystem, name)` for deterministic output
- Tracks cross-ecosystem duplicates for downstream analyzers

### Analyzers

Analyzers operate on the normalized package list and return typed `Finding` objects.

| Analyzer | Finding Kind | Severity | Description |
|----------|-------------|----------|-------------|
| `DuplicateAnalyzer` | `cross_ecosystem_duplicate` | `warning` | Same package name in multiple ecosystems |
| `PathShadowAnalyzer` | `path_shadow` | `warning` | Binary name collision resolved by PATH order |
| `OrphanedBinaryAnalyzer` | `orphaned_binary` | `info` | Manually installed binary with no known package owner |

### Renderers

Renderers are responsible only for output formatting.

Supported formats:
- **Table** — Rich-powered terminal table (default)
- **JSON** — structured object with `packages` and `findings` arrays

## Core Data Model

The canonical unit of information is a `PackageRecord`:

```python
class PackageRecord(BaseModel):
    name: str
    version_raw: str | None
    version_parsed: SemVer | None
    ecosystem: str
    source: str
    install_path: str | None
    binaries: list[BinaryRecord]
    metadata: PackageMetadata
```

### Binary Resolution

Binary ownership is tracked with confidence levels:

```python
class BinaryRecord(BaseModel):
    name: str
    path: str
    confidence: Literal["high", "medium", "low"]
    is_symlink: bool
    symlink_target: str | None
```

### Metadata Model

```python
class PackageMetadata(BaseModel):
    install_date: datetime | None = None
    install_reason: Literal["explicit", "dependency", "unknown"] | None = None
    size_bytes: int | None = None
    extensions: dict[str, Any] = {}   # must use "ecosystem:key" format
```

## Known Limitations

### PATH Resolution
- Reports PATH state **at audit time only**
- Does not parse shell configuration files (`.bashrc`, `.zshrc`)
- Cannot detect runtime PATH modifications (virtual environments, direnv)

### Version Detection
- Version parsing is best-effort and ecosystem-dependent
- Some binaries lack standardized version output
- Non-semantic versions (`3.118ubuntu5`) are stored as `version_raw` with `version_parsed = None`

### Binary Ownership
- Ownership detection uses heuristics for manual installs
- Symlink chains may be ambiguous
- Confidence levels reflect this uncertainty

## Safety and Trust Model

- No commands are run with elevated privileges
- No files are modified
- No network access is required
- All heuristics are transparent and explainable
- Collectors fail independently without stopping the audit
- All collectors are subject to configurable timeouts

## Testing

Run the full test suite:

```bash
pytest
```

Coverage report:

```bash
pytest --cov=src/env_audit --cov-report=term-missing
```

Tests never depend on the developer's actual system — all collector tests use fixture files with captured command output.

## Extending env-audit-poc

To add a new ecosystem:

1. Implement the `Collector` interface
2. Map raw output to the canonical `PackageRecord` model
3. Use namespaced extensions for ecosystem-specific metadata
4. Register the collector in `COLLECTOR_REGISTRY` in `cli.py`
5. Add fixture files for testing

No changes to analyzers or renderers are required.

```python
class BrewCollector(Collector):
    @property
    def ecosystem(self) -> str:
        return "brew"

    def is_available(self) -> bool:
        return shutil.which("brew") is not None

    def collect(self) -> list[PackageRecord]:
        ...
```

To add a new analyzer:

1. Subclass `Analyzer` and `Finding`
2. Implement `analyze(packages) -> list[Finding]`
3. Register in `ANALYZER_REGISTRY` in `cli.py`

## Development Status

Proof of concept complete. A rewrite in Go is planned.

## Project Philosophy

- Prefer clarity over cleverness
- Make unsafe operations impossible
- Optimize for maintainability, not novelty
- Treat developer environments as production systems
- Fail gracefully and explain limitations
- Provide actionable, explainable insights

## License

```
MIT
```