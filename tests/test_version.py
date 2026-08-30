"""Version metadata regression tests."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.conditional_notifications.const import VERSION

MANIFEST = (
    Path(__file__).parents[1]
    / "custom_components"
    / "conditional_notifications"
    / "manifest.json"
)


def test_manifest_version_matches_runtime_version() -> None:
    """Keep the manifest, panel cache key, and runtime version in lockstep."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["version"] == VERSION
