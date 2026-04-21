# env-audit-poc Architecture & Decision Record

This document explains the architectural choices made in `env-audit-poc`, the tradeoffs that were consciously accepted, and the reasoning behind decisions that might otherwise look surprising. It is intended for contributors, code reviewers, and future maintainers.

## Guiding Principles

Every significant design decision in this project traces back to one of four principles:

1. **Correctness over completeness** — it is better to surface a partial picture with documented gaps than to silently guess.
2. **Explainability over automation** — every finding should be traceable to a specific rule that a user can read and verify.
3. **Read-only by default** — an auditing tool that modifies the system it is auditing cannot be trusted.
4. **Independent failure** — no single component should be able to abort the entire audit.

## Layered Pipeline Architecture

The tool is structured as a strict one-way pipeline:

```
Collectors → Orchestrator → Normalizer → Analyzers → Renderers
```

Each layer's output is the next layer's only input. No layer reaches backwards. This has three consequences:

- **Independent testability.** Each layer can be tested in complete isolation with constructed inputs.
- **Substitutability.** Any renderer can be swapped in without touching collectors. Any new collector requires no changes downstream.
- **Predictable data flow.** A bug in the analysis layer cannot corrupt collection output, a renderer bug cannot affect normalization.

The CLI is the only component that sees the full pipeline. It is deliberately thin — it wires components together and handles formatting decisions, but contains no business logic of its own.

## Data Model: Why Pydantic with Frozen Models

`PackageRecord`, `BinaryRecord`, and `PackageMetadata` are all Pydantic `BaseModel` with `model_config = {"frozen": True}`.

**Why Pydantic over stdlib dataclasses?**
- Field validators run automatically on construction, catching malformed data at the boundary (e.g., relative paths in `BinaryRecord.path`, blank `source` strings)
- `model_dump(mode='json')` serializes the full object graph — including enums, nested models, and `None` fields — without any custom serialization code
- Frozen models prevent accidental mutation anywhere in the pipeline

**The cost:** Pydantic adds a dependency and makes construction slightly more verbose. This is acceptable for a tool where data correctness is the primary goal.

**Why frozen at all?** Once a `PackageRecord` is created by a collector, no downstream layer should modify it. Freezing enforces this at the type level rather than relying on convention. The normalizer does not mutate records — it selects, sorts, and groups them.

## Version Handling: Dual Storage

Every package carries both `version_raw` (the original string from the source) and `version_parsed` (a `SemVer` object, or `None`).

**Why keep `version_raw`?**
Many real-world version strings cannot be expressed as semantic versions: `3.118ubuntu5`, `1:1.2.11.dfsg-2ubuntu9.2`, `20230311ubuntu0.22.04.1`. Discarding them would make the inventory less accurate than the source. `version_raw` is always the authoritative record of what the package manager reported.

**Why `version_parsed`?**
Normalized versions enable the normalizer's deduplication logic (`_pick_best()` selects the highest version from intra-ecosystem duplicates). Attempting to compare `version_raw` strings lexicographically is error-prone and locale-sensitive.

**Why `None` on parse failure rather than raising?**
Version parsing is best-effort by design. `_try_parse_semver()` returning `None` keeps the collector's `_parse()` method free of exception-handling logic and allows the normalizer to degrade gracefully: if no record in a group has a parseable version, the first occurrence is kept.

## Metadata: Typed Core + Namespaced Extensions

`PackageMetadata` uses a hybrid approach:

```python
class PackageMetadata(BaseModel):
    install_date: datetime | None = None
    install_reason: InstallReason | None = None
    size_bytes: int | None = None
    extensions: dict[str, Any] = {}
```

**Typed core fields** (`install_date`, `install_reason`, `size_bytes`) are the fields that analyzers and future features can depend on unconditionally, with no ecosystem-specific knowledge.

**`extensions`** handles everything else — architecture (`apt:architecture`), editable installs (`pip:editable`), tap origin (`brew:tap`) — using a mandatory `ecosystem:key` namespace enforced by a field validator. The colon requirement prevents collisions between ecosystems without requiring a formal schema per ecosystem.

**The tradeoff:** `extensions` values are untyped `Any`. A consumer must know the key to use the value. This is acceptable because extensions are always ecosystem-specific and consumers that care about them (e.g., a future apt-specific analyzer) already know which ecosystem they are operating on.

## Collector Design: Pure Parsing Functions

Every collector separates subprocess execution from output parsing:

```python
def collect(self) -> list[PackageRecord]:
    result = subprocess.run(...)   # mocked in tests
    return self._parse(result.stdout)

def _parse(self, output: str) -> list[PackageRecord]:
    ...   # pure function, tested directly with fixture strings
```

**Why this split?** Mocking subprocess calls tests the wrong thing — it verifies that the code calls subprocess correctly, not that it parses real output correctly. Fixture files contain actual output from real systems. Tests call `_parse()` directly with those strings. This catches parsing bugs that subprocess mocking would miss.

**`_parse()` never raises.** Malformed lines are skipped, invalid JSON returns an empty list. This upholds the collector error contract: errors are either `CollectorError` subclasses (for systemic failures) or silent skips (for individual malformed records).

## Collector Error Hierarchy

```
CollectorError
├── CollectorUnavailableError(ecosystem, reason)
├── CollectorTimeoutError(ecosystem, timeout)
└── CollectorParseError(ecosystem, detail)
```

Each subclass carries structured context (`ecosystem`, `reason`/`timeout`/`detail`) rather than just a message string. This allows the orchestrator and CLI to format errors consistently without string parsing.

**Why not a single `CollectorError` with an enum type?** Typed subclasses allow `except CollectorTimeoutError` at a specific catch site without branching on a field, and make exhaustiveness checking easier in future typed callers.

## Orchestrator: Fail-Independent Collection

The `Orchestrator` catches `CollectorError` per-collector and accumulates results and errors separately:

```python
for collector in self._collectors:
    try:
        result.packages.extend(collector.collect())
    except CollectorError as exc:
        result.errors[collector.ecosystem] = exc
```

**Why not stop on first error?** A developer running the tool on a system without `npm` should still see their `apt` and `pip` packages. Collector independence is a core usability property.

**Why store errors by ecosystem rather than re-raising?** The CLI needs to display a warning per failed collector and decide whether to exit non-zero based on `--skip-failing`. Structured error storage makes both straightforward.

## Normalizer vs. DuplicateAnalyzer: Apparent Redundancy

Both the `Normalizer` and `DuplicateAnalyzer` detect packages with the same name in multiple ecosystems. They are not redundant.

The **normalizer** produces `NormalizerResult.cross_ecosystem_duplicates` as a structural metadata record — a `dict[str, list[str]]` used internally to understand the shape of the package set. The normalizer does not decide whether a cross-ecosystem duplicate is a problem, it just records the fact.

The **`DuplicateAnalyzer`** produces `CrossEcosystemDuplicate` findings — typed, severity-tagged, serializable objects designed for human and machine consumption. It is the analysis layer's job to decide that a cross-ecosystem duplicate is a `"warning"` and to produce a readable `message`.

The separation means that future analyzers that need cross-ecosystem information can read `NormalizerResult.cross_ecosystem_duplicates` directly, while the `DuplicateAnalyzer` can be updated (e.g., to exclude known intentional dual installs) without touching the normalizer.

## PathShadowAnalyzer: Audit-Time Only

`PathShadowAnalyzer` reads `os.environ["PATH"]` at analysis time (or accepts an override via the `path=` constructor parameter for testing). It does not attempt to:

- Parse `.bashrc`, `.zshenv`, or other shell configuration files
- Account for `direnv` or virtual environment activation
- Predict what PATH will look like in a user's interactive shell session

This is an explicit scope decision. PATH analysis that attempts to cover all shell configuration variants would be complex, error-prone, and fragile across shell flavors. Audit-time PATH analysis is simpler, deterministic, and documented honestly.

The `path=` constructor parameter is not a compromise — it is the correct API. It decouples the analyzer from the environment for testing without any mocking infrastructure.

## Findings: Frozen Dataclasses with `to_dict()`

Each `Finding` subclass is a frozen dataclass that inherits from `Finding`:

```python
@dataclasses.dataclass(frozen=True)
class CrossEcosystemDuplicate(Finding):
    name: str
    ecosystems: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)   # tuples stay as tuples via asdict
        d["kind"] = "cross_ecosystem_duplicate"
        return d
```

**Why dataclasses and not Pydantic?** `Finding` subclasses are internal analysis products — they are never parsed from external input and need no field validation. Stdlib dataclasses are sufficient and keep the analyzer layer free of Pydantic imports.

**Why `frozen=True`?** Findings are produced by an analyzer, returned in a list, and consumed by a renderer. No layer should modify them. Freezing enforces this.

**Why tuples for multi-value fields (`ecosystems`, `shadowed_paths`)?** Tuples signal that these sequences are ordered and immutable. `dataclasses.asdict()` preserves tuples (it uses `type(obj)(...)` when recursing, so `tuple` stays `tuple`). This is intentional — JSON serialization converts them to arrays at the renderer layer, where that conversion belongs.

**Why a `kind` field added in `to_dict()` rather than a class attribute?** The `kind` string is a serialization concern, not a modeling concern. Adding it in `to_dict()` keeps the dataclass fields clean and avoids the need for a `ClassVar` annotation.

## Renderers: Package-Scoped, Findings Handled by CLI

The `Renderer` ABC has a single method: `render(packages: list[PackageRecord]) -> str`. It does not accept findings.

**Why not extend the ABC to include findings?**
Renderers are independently testable against package lists with no findings infrastructure. Extending the ABC would require every renderer test to construct findings fixtures and every renderer implementation to handle both concerns. The package table and the findings table are visually and structurally distinct — keeping them separate in the code reflects this.

**Where are findings rendered?** In the CLI. For `--format table`, the CLI calls `TableRenderer().render(packages)` then `_render_findings_table(findings)` as a second Rich table. For `--format json`, the CLI builds `{"packages": ..., "findings": ...}` directly. The CLI is the right place for these assembly decisions — it is the only component with visibility into both the output format and the findings list simultaneously.

## JSON Output Envelope

The JSON output format is:

```json
{
  "packages": [...],
  "findings": [...]
}
```

Rather than the original flat array `[...]`.

**Why the change?** A flat array can only contain one kind of data. Adding findings to a flat package array would require type-based dispatch on the consumer side. A keyed envelope is unambiguous: `data["packages"]` is always a list of package records, `data["findings"]` is always a list of findings. Both lists are stable, independently iterable, and suitable for `jq` queries.

**`--no-analyze` still emits `"findings": []`.** The key is always present so consumers do not need to guard against missing keys. An empty list and an absent key are semantically equivalent but the former is strictly easier to consume.

## CLI Design: Registries over Hardcoded Lists

`COLLECTOR_REGISTRY` and `ANALYZER_REGISTRY` are module-level dicts that map string keys to classes. The CLI instantiates from these dicts.

**Why registries?**
- The `--collectors` flag can validate user input against `COLLECTOR_REGISTRY.keys()` with a single membership test
- Tests patch `COLLECTOR_REGISTRY` and `ANALYZER_REGISTRY` to inject mocks without touching any real collector or analyzer code
- Adding a new collector or analyzer requires editing exactly one dict — there is no list to keep in sync with a `click.Choice` elsewhere

**Why not auto-discovery via entry points?** Auto-discovery is powerful but adds complexity: the `importlib.metadata` API, `pyproject.toml` entry point declarations, and potential ordering ambiguity. The current explicit registry is simpler, more debuggable, and appropriate for the current scope. Auto-discovery is a natural Phase 6+ enhancement.

## Testing Strategy

### Fixture-Based Collector Tests

Collector tests never call real system commands. Instead:

1. Real command output is captured once from a live system and stored in `tests/fixtures/`
2. `_parse()` is a pure method that accepts a string and returns a list
3. Tests call `_parse(fixture_file.read_text())` directly — no mocking required for parsing tests
4. The subprocess layer is mocked only in `collect()` tests that verify error handling behavior

This approach catches real-world parsing bugs (format changes, locale variations, edge cases) that subprocess mocking would miss entirely.

### No Environment Dependence

Tests never read `os.environ["PATH"]`, check for installed binaries, or scan real directories. `PathShadowAnalyzer` accepts `path=` in its constructor. `ManualBinaryCollector` accepts `scan_dirs=`. The CLI is tested via `CliRunner` with patched registries.

### 100% Coverage as a Policy

100% coverage is maintained not as a vanity metric but as a correctness signal. Every line being covered means every branch has been explicitly reasoned about and tested. Untested branches in a read-only auditing tool are potential silent failures — cases where the tool produces no output rather than an accurate (partial) result.