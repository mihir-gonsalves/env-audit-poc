# src/env_audit/renderers/json.py
"""
JSON renderer for env-audit-poc.

Uses Pydantic's built-in serialisation so the output is guaranteed to
round-trip back to ``PackageRecord`` objects.
"""

import json as _json

from env_audit.models import PackageRecord

from .base import Renderer

__all__ = ["JsonRenderer"]


class JsonRenderer(Renderer):
    """Render packages as a pretty-printed JSON array."""

    def render(self, packages: list[PackageRecord]) -> str:
        """
        Return a JSON array of serialised ``PackageRecord`` objects.

        ``model_dump(mode='json')`` produces a JSON-compatible dict
        directly, coercing enums, datetimes, and nested models to their
        serialisable equivalents without an intermediate string round-trip.

        The returned string always ends with ``'\\n'``.
        """
        data = [pkg.model_dump(mode="json") for pkg in packages]
        return _json.dumps(data, indent=2) + "\n"