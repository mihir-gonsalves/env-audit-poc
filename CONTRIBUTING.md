# env-audit-poc Contributor Guide

This document covers everything you need to add a new collector, analyzer, or renderer; run the test suite; and understand the project's quality expectations.

## Getting Started

**Prerequisites:** Python 3.10+, `git`

```bash
git clone <repo>
cd env-audit-poc

python -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
```

Verify the setup:

```bash
pytest
# Expected: all tests pass, 100% coverage
```

## Project Structure

```
src/env_audit/
├── models/          # Immutable data model (PackageRecord, BinaryRecord, …)
├── collectors/      # One collector per ecosystem: base class + exception hierarchy
├── analyzers/       # One analyzer per concern: base class + Finding hierarchy
├── renderers/       # One renderer per output format
├── normalizer.py    # Deduplication and sorting
├── orchestrator.py  # Runs collectors independently
└── cli.py           # Entry point, wires all layers together

tests/
├── test_models/
├── test_collectors/
├── test_analyzers/
├── test_renderers/
├── test_normalizer.py
├── test_orchestrator.py
├── test_cli.py
└── fixtures/        # Captured real command output for collector tests
    ├── apt/
    ├── npm/
    └── pip/
```

## Running Tests

```bash
# Full suite
pytest

# With coverage report
pytest --cov=src/env_audit --cov-report=term-missing

# Single module
pytest tests/test_analyzers/test_duplicates.py -v

# Watch mode (requires pytest-watch)
ptw tests/
```

Coverage must remain at 100% for all implemented modules. A PR that drops coverage will not be merged.

## Adding a New Collector

Collectors are the most common extension point. Here is the full process.

### 1. Capture a fixture file

On a real system with the tool installed, capture its output:

```bash
# Example for Homebrew
brew list --json=v2 > tests/fixtures/brew/macos-14.json
```

Store the file in `tests/fixtures/<ecosystem>/`. Use a descriptive name that includes the OS version.

### 2. Implement the collector

Create `src/env_audit/collectors/brew.py`. The mandatory structure:

```python
# src/env_audit/collectors/brew.py
import shutil
import subprocess

from env_audit.models import PackageMetadata, PackageRecord, SemVer
from .base import Collector, CollectorParseError, CollectorTimeoutError, CollectorUnavailableError

__all__ = ["BrewCollector"]


class BrewCollector(Collector):

    @property
    def ecosystem(self) -> str:
        return "brew"

    def is_available(self) -> bool:
        return shutil.which("brew") is not None

    def collect(self) -> list[PackageRecord]:
        if not self.is_available():
            raise CollectorUnavailableError(self.ecosystem, "brew not found in PATH")

        try:
            result = subprocess.run(
                ["brew", "list", "--json=v2"],
                capture_output=True,
                text=True,
                timeout=self.DEFAULT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise CollectorTimeoutError(self.ecosystem, self.DEFAULT_TIMEOUT)

        if result.returncode != 0:
            raise CollectorParseError(
                self.ecosystem,
                f"brew exited with status {result.returncode}: {result.stderr.strip()}",
            )

        return self._parse(result.stdout)

    def _parse(self, output: str) -> list[PackageRecord]:
        # Pure function: string in, list out. Never raises.
        ...

    def _try_parse_semver(self, version: str) -> SemVer | None:
        ...
```

**Rules for `_parse()`:**
- Must be a pure function: same input always produces same output
- Must never raise — malformed records are silently skipped
- Must never call subprocesses or read files

**Rules for `collect()`:**
- Must raise `CollectorUnavailableError` when the tool is not installed
- Must raise `CollectorTimeoutError` on subprocess timeout
- Must raise `CollectorParseError` on non-zero exit or unparseable output
- Must never raise anything else

### 3. Export and register

Add to `src/env_audit/collectors/__init__.py`:

```python
from .brew import BrewCollector
```

Add to `COLLECTOR_REGISTRY` in `src/env_audit/cli.py`:

```python
COLLECTOR_REGISTRY: dict[str, type] = {
    "apt": AptCollector,
    "pip": PipCollector,
    "npm": NpmCollector,
    "manual": ManualBinaryCollector,
    "brew": BrewCollector,   # ← add here
}
```

### 4. Write the tests

Create `tests/test_collectors/test_brew.py`. The required test classes:

| Class | What it covers |
|-|-|
| `TestEcosystem` | `ecosystem` property returns `"brew"` |
| `TestIsAvailable` | `True` when binary found, `False` otherwise |
| `TestCollect` | Unavailable error, timeout, non-zero exit, success path, timeout forwarded to subprocess |
| `TestParse` | Empty input, invalid JSON/format, each malformed-record variant, full fixture round-trip, ecosystem/source field values |
| `TestTryParseSemver` | Each version format the collector encounters, plus failure cases |

See `tests/test_collectors/test_apt.py` for a complete reference implementation.

## Adding a New Analyzer

### 1. Define the Finding subclass

```python
# src/env_audit/analyzers/size.py
import dataclasses
from typing import Any
from .base import Analyzer, Finding

@dataclasses.dataclass(frozen=True)
class OversizedPackageFinding(Finding):
    package_name: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)   # tuples stay as tuples via asdict
        d["kind"] = "oversized_package"
        return d
```

**Rules for `Finding` subclasses:**
- Must be `frozen=True`
- Must override `to_dict()` and add a `"kind"` key
- The `kind` value must be unique across all finders (use snake_case)
- Multi-value fields should use `tuple[str, ...]`, not `list` — tuples are immutable and survive `dataclasses.asdict()` as tuples

### 2. Implement the analyzer

```python
class SizeAnalyzer(Analyzer):
    THRESHOLD_BYTES = 500 * 1024 * 1024  # 500 MB

    def analyze(self, packages: list[PackageRecord]) -> list[Finding]:
        findings = []
        for pkg in packages:
            if pkg.metadata.size_bytes and pkg.metadata.size_bytes > self.THRESHOLD_BYTES:
                findings.append(
                    OversizedPackageFinding(
                        severity="info",
                        message=f"'{pkg.name}' is {pkg.metadata.size_bytes // 1_000_000} MB",
                        package_name=pkg.name,
                        size_bytes=pkg.metadata.size_bytes,
                    )
                )
        return sorted(findings, key=lambda f: f.package_name)
```

**Rules for `analyze()`:**
- Must accept an empty list without raising
- Must never raise — return an empty list instead
- Must return findings in a deterministic order (sort explicitly)
- Must never modify any `PackageRecord`

### 3. Register

Add to `src/env_audit/analyzers/__init__.py` and `ANALYZER_REGISTRY` in `cli.py`:

```python
ANALYZER_REGISTRY: dict[str, type] = {
    "duplicates":  DuplicateAnalyzer,
    "path_shadow": PathShadowAnalyzer,
    "orphans":     OrphanedBinaryAnalyzer,
    "size":        SizeAnalyzer,   # ← add here
}
```

### 4. Write the tests

Create `tests/test_analyzers/test_size.py`. Required coverage:

- `Finding` subclass: constructor, `to_dict()` (verify `kind` key and all fields), immutability, isinstance check against `Finding`
- `Analyzer.analyze()`: empty list, no findings case, finding case, sorting, message content

See `tests/test_analyzers/test_duplicates.py` for a complete reference.

## Adding a New Renderer

### 1. Implement

```python
# src/env_audit/renderers/markdown.py
from env_audit.models import PackageRecord
from .base import Renderer

class MarkdownRenderer(Renderer):
    def render(self, packages: list[PackageRecord]) -> str:
        lines = ["| Name | Version | Ecosystem |", "|||--|"]
        for pkg in packages:
            lines.append(f"| {pkg.name} | {pkg.display_version()} | {pkg.ecosystem} |")
        return "\n".join(lines) + "\n"
```

**Rules:**
- The returned string must always end with `"\n"`
- Must never modify the `PackageRecord` list
- Must never perform I/O or call subprocesses

### 2. Register

Add to `RENDERER_REGISTRY` in `cli.py` and the `click.Choice` list in `--format`. The `test_renderer_registry_keys_match_format_choices` test in `test_cli.py` will catch any mismatch.

### 3. Write the tests

Create `tests/test_renderers/test_markdown.py`. Required coverage: empty list, trailing newline, content correctness for each column, multiple rows.

## Code Style

This project does not use a formatter (Black, Ruff) by default, but follows these conventions consistently:

- **Type annotations everywhere** — all function signatures are fully annotated
- **`from __future__ import annotations`** at the top of files that use `X | Y` union syntax
- **`__all__`** declared in every module to make the public API explicit
- **Docstrings** on every public class and method, use the numpy/Google style (Parameters/Returns sections for non-trivial functions)
- **No bare `except:`** — always catch a specific exception type
- **No `# type: ignore`** unless accompanied by a comment explaining why it is safe

## Commit Guidelines

- One logical change per commit
- Present-tense imperative subject line: `Add BrewCollector`, `Fix version parsing for epoch strings`
- Breaking changes to the JSON output format or `Renderer` ABC require a comment in the commit body explaining the migration path

## What Not to Add

This tool is deliberately scoped. Please do not open PRs that:

- **Modify the system** — no package removal, no file writes outside the project directory
- **Add network calls** — the tool is designed to run without internet access
- **Add shell configuration parsing** — PATH shadowing is audit-time only, this is documented
- **Add auto-discovery of collectors** — explicit registry is intentional, auto-discovery is a post-v1 concern
- **Lower test coverage below 100%** — new code must be fully covered

If you want to discuss a larger architectural change, open an issue first.