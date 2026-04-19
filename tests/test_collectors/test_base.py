# tests/test_collectors/test_base.py
"""
100 % coverage tests for env_audit.collectors.base.

Strategy
--------
* Exercise every line of the three exception __init__ bodies directly.
* Verify the exception hierarchy (isinstance checks).
* Instantiate the ABC via a minimal ConcreteCollector to cover the
  class-level attribute and the abstract-method declarations.
* Assert that Collector itself cannot be instantiated.
"""

import pytest

from env_audit.collectors.base import (
    Collector,
    CollectorError,
    CollectorParseError,
    CollectorTimeoutError,
    CollectorUnavailableError,
)
from env_audit.models import PackageRecord


# ---------------------------------------------------------------------------
# Minimal concrete subclass used throughout this module
# ---------------------------------------------------------------------------


class ConcreteCollector(Collector):
    """Trivial implementation that satisfies all abstract requirements."""

    @property
    def ecosystem(self) -> str:
        return "test"

    def is_available(self) -> bool:
        return True

    def collect(self) -> list[PackageRecord]:
        return []


# ---------------------------------------------------------------------------
# CollectorError (base)
# ---------------------------------------------------------------------------


class TestCollectorError:
    def test_is_exception(self) -> None:
        err = CollectorError("something went wrong")
        assert isinstance(err, Exception)

    def test_message(self) -> None:
        err = CollectorError("boom")
        assert str(err) == "boom"


# ---------------------------------------------------------------------------
# CollectorUnavailableError
# ---------------------------------------------------------------------------


class TestCollectorUnavailableError:
    def test_attributes_set(self) -> None:
        err = CollectorUnavailableError("apt", "binary not found")
        assert err.ecosystem == "apt"
        assert err.reason == "binary not found"

    def test_str_message(self) -> None:
        err = CollectorUnavailableError("apt", "binary not found")
        assert str(err) == "Collector 'apt' is unavailable: binary not found"

    def test_is_collector_error(self) -> None:
        err = CollectorUnavailableError("pip", "pip3 missing")
        assert isinstance(err, CollectorError)

    def test_can_be_raised_and_caught(self) -> None:
        with pytest.raises(CollectorUnavailableError) as exc_info:
            raise CollectorUnavailableError("npm", "node not installed")
        assert exc_info.value.ecosystem == "npm"

    def test_also_caught_as_collector_error(self) -> None:
        with pytest.raises(CollectorError):
            raise CollectorUnavailableError("brew", "not on Linux")


# ---------------------------------------------------------------------------
# CollectorTimeoutError
# ---------------------------------------------------------------------------


class TestCollectorTimeoutError:
    def test_attributes_set(self) -> None:
        err = CollectorTimeoutError("apt", 30.0)
        assert err.ecosystem == "apt"
        assert err.timeout == 30.0

    def test_str_message_one_decimal(self) -> None:
        err = CollectorTimeoutError("apt", 30.0)
        assert str(err) == "Collector 'apt' timed out after 30.0s"

    def test_str_message_fractional_timeout(self) -> None:
        err = CollectorTimeoutError("pip", 5.5)
        assert str(err) == "Collector 'pip' timed out after 5.5s"

    def test_is_collector_error(self) -> None:
        err = CollectorTimeoutError("npm", 10.0)
        assert isinstance(err, CollectorError)

    def test_can_be_raised_and_caught(self) -> None:
        with pytest.raises(CollectorTimeoutError) as exc_info:
            raise CollectorTimeoutError("apt", 60.0)
        assert exc_info.value.timeout == 60.0


# ---------------------------------------------------------------------------
# CollectorParseError
# ---------------------------------------------------------------------------


class TestCollectorParseError:
    def test_attributes_set(self) -> None:
        err = CollectorParseError("apt", "unexpected token on line 3")
        assert err.ecosystem == "apt"
        assert err.detail == "unexpected token on line 3"

    def test_str_message(self) -> None:
        err = CollectorParseError("apt", "unexpected token on line 3")
        assert str(err) == (
            "Collector 'apt' failed to parse output: unexpected token on line 3"
        )

    def test_is_collector_error(self) -> None:
        err = CollectorParseError("pip", "missing field")
        assert isinstance(err, CollectorError)

    def test_can_be_raised_and_caught(self) -> None:
        with pytest.raises(CollectorParseError) as exc_info:
            raise CollectorParseError("npm", "JSON decode error")
        assert exc_info.value.detail == "JSON decode error"


# ---------------------------------------------------------------------------
# Collector (abstract base class)
# ---------------------------------------------------------------------------


class TestCollectorABC:
    def test_cannot_instantiate_directly(self) -> None:
        """Collector is abstract; direct instantiation must raise TypeError."""
        with pytest.raises(TypeError):
            Collector()  # type: ignore[abstract]

    def test_default_timeout_class_attribute(self) -> None:
        assert Collector.DEFAULT_TIMEOUT == 30.0

    def test_concrete_inherits_default_timeout(self) -> None:
        c = ConcreteCollector()
        assert c.DEFAULT_TIMEOUT == 30.0

    def test_ecosystem_property(self) -> None:
        c = ConcreteCollector()
        assert c.ecosystem == "test"

    def test_is_available_returns_bool(self) -> None:
        c = ConcreteCollector()
        assert c.is_available() is True

    def test_collect_returns_list(self) -> None:
        c = ConcreteCollector()
        result = c.collect()
        assert isinstance(result, list)
        assert result == []

    def test_concrete_is_instance_of_collector(self) -> None:
        c = ConcreteCollector()
        assert isinstance(c, Collector)

    def test_default_timeout_can_be_overridden(self) -> None:
        class FastCollector(Collector):
            DEFAULT_TIMEOUT = 5.0

            @property
            def ecosystem(self) -> str:
                return "fast"

            def is_available(self) -> bool:
                return False

            def collect(self) -> list[PackageRecord]:
                return []

        c = FastCollector()
        assert c.DEFAULT_TIMEOUT == 5.0