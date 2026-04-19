# tests/test_renderers/test_json.py
"""
100 % coverage tests for env_audit.renderers.json.
"""

import json as _json

import pytest

from env_audit.models import InstallReason, PackageMetadata, PackageRecord, SemVer
from env_audit.renderers.json import JsonRenderer


def _pkg(name: str = "vim", **kwargs: object) -> PackageRecord:
    return PackageRecord(name=name, ecosystem="apt", source="universe", **kwargs)


class TestJsonRenderer:
    def test_empty_list_produces_empty_json_array(self) -> None:
        result = JsonRenderer().render([])
        assert _json.loads(result) == []

    def test_output_always_ends_with_newline(self) -> None:
        assert JsonRenderer().render([]).endswith("\n")
        assert JsonRenderer().render([_pkg()]).endswith("\n")

    def test_single_package_name_preserved(self) -> None:
        data = _json.loads(JsonRenderer().render([_pkg("wget")]))
        assert data[0]["name"] == "wget"

    def test_single_package_ecosystem_preserved(self) -> None:
        data = _json.loads(JsonRenderer().render([_pkg()]))
        assert data[0]["ecosystem"] == "apt"

    def test_multiple_packages_all_serialised(self) -> None:
        pkgs = [_pkg("vim"), _pkg("git")]
        data = _json.loads(JsonRenderer().render(pkgs))
        assert len(data) == 2
        assert {p["name"] for p in data} == {"vim", "git"}

    def test_output_is_indented(self) -> None:
        """Indented JSON contains interior newlines."""
        result = JsonRenderer().render([_pkg()])
        # Strip the trailing newline we add ourselves; there must still be
        # newlines inside the body (from indent=2).
        assert "\n" in result.rstrip("\n")

    def test_version_raw_preserved(self) -> None:
        pkg = _pkg(version_raw="3.11.6-1~22.04")
        data = _json.loads(JsonRenderer().render([pkg]))
        assert data[0]["version_raw"] == "3.11.6-1~22.04"

    def test_parsed_version_serialised_as_object(self) -> None:
        pkg = _pkg(version_parsed=SemVer(major=3, minor=11, patch=6))
        data = _json.loads(JsonRenderer().render([pkg]))
        assert data[0]["version_parsed"]["major"] == 3
        assert data[0]["version_parsed"]["minor"] == 11

    def test_install_reason_serialised_as_string(self) -> None:
        pkg = _pkg(metadata=PackageMetadata(install_reason=InstallReason.EXPLICIT))
        data = _json.loads(JsonRenderer().render([pkg]))
        assert data[0]["metadata"]["install_reason"] == "explicit"

    def test_null_install_reason_serialised_as_none(self) -> None:
        data = _json.loads(JsonRenderer().render([_pkg()]))
        assert data[0]["metadata"]["install_reason"] is None

    def test_extensions_preserved(self) -> None:
        pkg = _pkg(
            metadata=PackageMetadata(extensions={"apt:architecture": "amd64"})
        )
        data = _json.loads(JsonRenderer().render([pkg]))
        assert data[0]["metadata"]["extensions"]["apt:architecture"] == "amd64"

    def test_output_is_valid_json_for_complex_record(self) -> None:
        """Round-trip: render -> parse must not raise."""
        pkg = PackageRecord(
            name="python3.11",
            version_raw="3.11.6-1~22.04",
            version_parsed=SemVer(major=3, minor=11, patch=6, prerelease="1~22.04"),
            ecosystem="apt",
            source="jammy-updates",
            metadata=PackageMetadata(
                install_reason=InstallReason.DEPENDENCY,
                size_bytes=4096,
                extensions={"apt:architecture": "amd64"},
            ),
        )
        _json.loads(JsonRenderer().render([pkg]))  # must not raise