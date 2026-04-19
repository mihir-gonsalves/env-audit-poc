# tests/test_renderers/test_base.py
"""
100 % coverage tests for env_audit.renderers.base.

The abstract class itself cannot be instantiated, so a minimal concrete
subclass (``ConcreteRenderer``) is used to verify the contract and to
exercise the abstract method declaration.
"""

import pytest

from env_audit.models import PackageRecord
from env_audit.renderers.base import Renderer


class ConcreteRenderer(Renderer):
    """Trivial implementation that satisfies the abstract contract."""

    def render(self, packages: list[PackageRecord]) -> str:
        return f"count:{len(packages)}\n"


class TestRendererABC:
    def test_cannot_instantiate_abstract_class(self) -> None:
        with pytest.raises(TypeError):
            Renderer()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self) -> None:
        r = ConcreteRenderer()
        assert isinstance(r, Renderer)

    def test_render_with_empty_list(self) -> None:
        r = ConcreteRenderer()
        assert r.render([]) == "count:0\n"

    def test_render_with_packages(self) -> None:
        pkgs = [
            PackageRecord(name="vim", ecosystem="apt", source="universe"),
            PackageRecord(name="git", ecosystem="apt", source="jammy"),
        ]
        r = ConcreteRenderer()
        assert r.render(pkgs) == "count:2\n"

    def test_renderer_is_base_type(self) -> None:
        """``isinstance`` check ensures the ABC is in the MRO."""
        r = ConcreteRenderer()
        assert isinstance(r, Renderer)