# env-audit-poc

`env-audit-poc` is a read-only, developer-focused environment auditing tool that produces a unified, explainable inventory of software installed on a machine across multiple package ecosystems.

It is designed for developers who have accumulated tools over time using different package managers, manual installs, and version managers, and want a clear, trustworthy view of what is installed, where it came from, and where duplication or shadowing exists.

This project prioritizes correctness, transparency, and extensibility over automation or cleanup.

Note: This project is a proof of concept. It will eventually be rewritten in Go for performance reasons.

---

## Problem Statement

Modern development environments accumulate technical debt:

- System packages installed via OS package managers
- Language-specific tools installed globally
- Multiple versions of the same runtime
- Manually installed binaries that are no longer tracked
- PATH collisions that hide tools unintentionally

There is no single tool that provides a complete, ecosystem-agnostic view of a developer machine.

`env-audit-poc` addresses this gap by generating a unified inventory and highlighting risks and redundancies without modifying the system.

---

## Non-Goals

This tool intentionally does **not**:

- Automatically uninstall or modify packages
- Guarantee perfect detection of unused tools
- Replace language-specific environment managers
- Act as a configuration management system

All output is advisory and explainable.

---

## Key Features

- Unified inventory across multiple installation sources
- Immutable, normalized data model
- Read-only by default
- Deterministic output (at audit time)
- Explainable insights (no black-box behavior)
- Extensible plugin architecture
- Machine-readable and human-readable output formats
- Graceful degradation when collectors fail

---

## Supported Ecosystems (Initial Scope)

- System packages (`apt`)
- Python (`pip`, system and user)
- Node.js (`npm -g`)
- Manually installed binaries (`/usr/local/bin`, `~/bin`)

Support for additional ecosystems is intentionally straightforward to add via the plugin architecture.

---

## Architecture Overview

`env-audit-poc` is structured as a layered, composable pipeline:

```text
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
    DEFAULT_TIMEOUT: float = 30.0  # override per subclass

    @property
    @abstractmethod
    def ecosystem(self) -> str:
        """Unique lowercase identifier (e.g., 'apt', 'pip', 'npm')"""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this collector can run on the current system.
        Must not raise — return False instead."""

    @abstractmethod
    def collect(self) -> list[PackageRecord]:
        """Gather and normalise package data. Raises CollectorError
        subclasses on failure; never modifies system state."""
```

Collectors raise typed errors that the orchestrator catches per-collector:

```python
CollectorError              # base
├── CollectorUnavailableError(ecosystem, reason)
├── CollectorTimeoutError(ecosystem, timeout)
└── CollectorParseError(ecosystem, detail)
```

Collectors are:
- **Independent**: One failing collector does not stop others
- **Time-bounded**: Subject to configurable timeouts
- **Locale-aware**: Set `LANG=C` to ensure consistent output parsing

Examples:
- `AptCollector`
- `PipCollector`
- `NpmCollector`
- `ManualBinaryCollector`

### Normalizer

The normalization layer converts heterogeneous raw data into a canonical model with version parsing and binary resolution.

- Resolves symlinks and binary ownership
- Performs best-effort version normalization
- Attaches confidence scores
- Does not infer intent or usage

### Analyzers

Analyzers operate on normalized data and generate explainable insights.

Examples:
- Duplicate versions of the same tool
- PATH shadowing between binaries (at audit time)
- Orphaned binaries without a known owner
- Package size and installation date analysis

### Renderers

Renderers are responsible only for output formatting.

Supported formats:
- Table (terminal-friendly)
- JSON
- Markdown

---

## Core Data Model

The canonical unit of information is a `PackageRecord`:

```python
class PackageRecord(BaseModel):
    name: str
    version_raw: str | None             # Original version string from source
    version_parsed: SemVer | None       # Parsed semantic version (best-effort)
    ecosystem: str                      # How the package is managed (apt, pip, npm)
    source: str                         # Where it originated (repo, tap, index, manual)
    install_path: str | None
    binaries: list[BinaryRecord]        # Linked binaries with confidence levels
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

Confidence levels:
- **High**: Binary directly provided by package manifest
- **Medium**: Binary found via heuristics (naming, location)
- **Low**: Binary found but ownership unclear

### Metadata Model

Package metadata uses a hybrid approach with typed core fields and flexible extensions:

```python
class PackageMetadata(BaseModel):
    # Core fields that analyzers can depend on
    install_date: datetime | None = None
    install_reason: Literal["explicit", "dependency", "unknown"] | None = None
    size_bytes: int | None = None
    
    # Ecosystem-specific extensions, clearly namespaced (e.g., "apt:architecture")
    extensions: dict[str, Any] = field(default_factory=dict)
```

All extension keys must use the `ecosystem:key` naming convention to prevent collisions.

All downstream analysis depends exclusively on this model.

---

## Usage

Run a full audit:
```bash
env-audit-poc
```

Render output in JSON:
```bash
env-audit-poc --format json
```

Generate a Markdown report:
```bash
env-audit-poc --format markdown > report.md
```

Restrict collectors:
```bash
env-audit-poc --collectors apt,pip
```

Skip failing collectors and continue:
```bash
env-audit-poc --skip-failing
```

Increase verbosity:
```bash
env-audit-poc -vv
```

---

## Example Output (Abbreviated)

```bash
Package: python3
Versions detected: 2
  - python3.10 (apt, /usr/bin/python3.10)
  - python3.11 (apt, /usr/bin/python3.11)

Binary Resolution:
  /usr/bin/python3 → python3.11 (high confidence)
  
PATH Analysis:
  Resolved version: /usr/bin/python3.11
  Shadowed versions: python3.10 (not in PATH priority)
```

**Insight:**
Multiple Python versions installed. `python3.11` takes precedence via symlink.

---

## Known Limitations

`env-audit-poc` makes best-effort attempts to provide accurate information, but some limitations are inherent to system-level auditing:

### PATH Resolution
- Reports PATH state **at audit time only**
- Does not parse shell configuration files (`.bashrc`, `.zshrc`)
- Cannot detect runtime PATH modifications (virtual environments, direnv)
- Shell aliases and functions may override binaries

### Version Detection
- Version parsing is best-effort and ecosystem-dependent
- Some binaries lack standardized version output
- Version comparison may fail for non-semantic versions
- Keeps both raw and parsed versions for transparency

### Binary Ownership
- Ownership detection uses heuristics for manual installs
- Symlink chains may be ambiguous
- Confidence levels reflect uncertainty

### Performance
- Collectors run serially by default
- Large package sets may take time to scan
- Filesystem scanning depends on directory size

These limitations are documented to set appropriate expectations and avoid false confidence.

---

## Safety and Trust Model

- No commands are run with elevated privileges by default
- No files are modified
- No network access is required
- All heuristics are transparent and explainable
- Collectors fail independently without stopping the audit
- All collectors are subject to timeouts

---

## Testing Strategy

- **Collectors**: Tested using fixture files with real command output from multiple OS versions
- **Normalization and analyzers**: Unit-tested with comprehensive edge cases
- **Integration tests**: Use isolated filesystem fixtures
- **Locale testing**: Explicitly test with `LANG=C` and other locales
- **No environment dependence**: Tests never rely on the developer's actual machine state

---

## Extending env-audit-poc

To add support for a new ecosystem:

1. Implement the `Collector` interface
2. Map raw output to the canonical `PackageRecord` model
3. Use namespaced extensions for ecosystem-specific metadata
4. Register the collector in the plugin system
5. Add fixture files for testing

No changes to analyzers or renderers are required.

Example:

```python
class BrewCollector(Collector):
    @property
    def ecosystem(self) -> str:
        return "brew"
    
    def is_available(self) -> bool:
        return shutil.which("brew") is not None
    
    def collect(self) -> list[PackageRecord]:
        # Implementation
        return [
            PackageRecord(
                name="wget",
                version_raw="1.21.3",
                version_parsed=SemVer(major=1, minor=21, patch=3),
                ecosystem="brew",
                source="homebrew/core",
                metadata=PackageMetadata(
                    install_reason=InstallReason.EXPLICIT,
                    extensions={
                        "brew:formula": "wget",
                        "brew:tap": "homebrew/core"
                    }
                )
            )
        ]
```

---

## Project Philosophy

- Prefer clarity over cleverness
- Make unsafe operations impossible
- Optimize for maintainability, not novelty
- Treat developer environments as production systems
- Fail gracefully and explain limitations
- Provide actionable, explainable insights

---

## Development Status

Phase 3 (CLI, orchestrator, renderers) is complete. See [PROJECT_STATUS.md](PROJECT_STATUS.md) for current implementation progress.

---

## Setup

Create virtual environment:
```bash
python -m venv .venv
```

Active venv:
```bash
source .venv/bin/activate
```

Install the project in editable mode:
```bash
pip install -e ".[dev]"
```
---

## Test

With pytest:
```bash
pytest
```

## Future Goals

- Expand supported ecosystems (Docker images, gem, Maven, Go modules)
- Parallel collector execution for performance
- Interactive TUI mode for exploration
- Configuration file support for customization

---

## License

```text
MIT
```