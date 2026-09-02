#!/usr/bin/env python3
"""Validate and update Conditional Notifications release metadata."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


@dataclass(frozen=True)
class VersionSpec:
    """One version-bearing file and the pattern used to read and update it."""

    label: str
    path: str
    pattern: str
    flags: int = 0


SPECS = (
    VersionSpec(
        "runtime constant",
        "custom_components/conditional_notifications/const.py",
        r'(^VERSION: Final = ")([^"]+)("$)',
        re.MULTILINE,
    ),
    VersionSpec(
        "Home Assistant manifest",
        "custom_components/conditional_notifications/manifest.json",
        r'(^  "version": ")([^"]+)("(?=,?$))',
        re.MULTILINE,
    ),
)


def _match_once(root: Path, spec: VersionSpec) -> tuple[str, re.Match[str]]:
    path = root / spec.path
    text = path.read_text(encoding="utf-8")
    matches = list(re.finditer(spec.pattern, text, spec.flags))
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {spec.label} version in {spec.path}; found {len(matches)}"
        )
    return text, matches[0]


def read_versions(root: Path = ROOT) -> dict[str, str]:
    """Return every tracked release version."""
    return {spec.label: _match_once(root, spec)[1].group(2) for spec in SPECS}


def current_version(root: Path = ROOT) -> str:
    """Return the common tracked version, failing when metadata disagrees."""
    versions = read_versions(root)
    unique = set(versions.values())
    if len(unique) != 1:
        raise RuntimeError(f"Release versions are inconsistent: {versions}")
    version = next(iter(unique))
    if not SEMVER.fullmatch(version):
        raise RuntimeError(f"Tracked release version must use X.Y.Z format, got {version!r}")
    return version


def set_release_version(version: str, root: Path = ROOT) -> None:
    """Set every tracked release-version field to one semantic version."""
    if not SEMVER.fullmatch(version):
        raise ValueError(f"Release version must use X.Y.Z format, got {version!r}")

    current_version(root)
    for spec in SPECS:
        path = root / spec.path
        text, _ = _match_once(root, spec)

        def replacement(match: re.Match[str]) -> str:
            return f"{match.group(1)}{version}{match.group(3)}"

        updated, count = re.subn(
            spec.pattern,
            replacement,
            text,
            count=1,
            flags=spec.flags,
        )
        if count != 1:
            raise RuntimeError(
                f"Expected exactly one writable {spec.label} version in {spec.path}; found {count}"
            )
        path.write_text(updated, encoding="utf-8")

    staged = current_version(root)
    if staged != version:
        raise RuntimeError(f"Version staging produced {staged!r}, expected {version!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("version", nargs="?", help="target release version in X.Y.Z format")
    parser.add_argument(
        "--check",
        action="store_true",
        help="only verify that all tracked release metadata agrees",
    )
    args = parser.parse_args()

    if args.check:
        if args.version is not None:
            parser.error("--check does not accept a target version")
        print(current_version())
        return

    if args.version is None:
        parser.error("a target version is required unless --check is used")

    before = current_version()
    set_release_version(args.version)
    print(f"Staged release version {before} -> {args.version}")


if __name__ == "__main__":
    main()
