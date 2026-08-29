"""Assist satellite delivery validation tests."""

from __future__ import annotations

import pytest
from custom_components.conditional_notifications.validation import (
    DefinitionError,
    validate_definition,
)


def _base(delivery: dict) -> dict:
    return {
        "name": "Kitchen announcement",
        "triggers": [
            {
                "type": "state",
                "entity_id": "binary_sensor.kitchen",
                "to": "on",
            }
        ],
        "title": "Kitchen",
        "message": "Kitchen motion detected",
        "delivery": delivery,
    }


def test_accepts_assist_satellite_targets() -> None:
    result = validate_definition(
        _base(
            {
                "use_defaults": False,
                "persistent_notification": False,
                "assist_satellites": ["assist_satellite.kitchen"],
            }
        )
    )
    assert result["delivery"]["assist_satellites"] == ["assist_satellite.kitchen"]


def test_rejects_non_satellite_entities_in_satellite_targets() -> None:
    with pytest.raises(DefinitionError, match="Assist satellite entity IDs"):
        validate_definition(
            _base(
                {
                    "use_defaults": False,
                    "assist_satellites": ["media_player.kitchen"],
                }
            )
        )
