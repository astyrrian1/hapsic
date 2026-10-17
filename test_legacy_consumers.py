"""Render distributed legacy MQTT consumers against old and repaired payloads."""

import math
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment

ROOT = Path(__file__).parent


class HomeAssistantLoader(yaml.SafeLoader):
    """Keep HA include/secret tags inert while reading sensor definitions."""


def tagged_value(loader, _tag, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


HomeAssistantLoader.add_multi_constructor("!", tagged_value)


PACKAGES = [("distributed", ROOT / "packages")]


def configs(directory):
    return [
        yaml.load(path.read_text(), Loader=HomeAssistantLoader)
        for name in ("humid.yaml", "hapsic_sensors.yaml", "hapsic_diagnostics.yaml")
        if (path := directory / name).exists()
    ]


def mqtt_sensor(directory, unique_id):
    matches = [
        sensor
        for config in configs(directory)
        for sensor in config.get("mqtt", {}).get("sensor", [])
        if sensor.get("unique_id") == unique_id
    ]
    assert len(matches) == 1, f"Expected one {unique_id} definition in {directory}"
    return matches[0]


def is_number(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def rendered(sensor, payload):
    environment = Environment()
    environment.tests["number"] = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool)
    environment.tests["is_number"] = is_number
    environment.filters["is_number"] = is_number
    available = True
    if "availability_template" in sensor:
        availability = environment.from_string(sensor["availability_template"]).render(value_json=payload).strip()
        available = availability.lower() in ("true", "online", "1", "yes")
    if not available:
        return False, None
    value = environment.from_string(sensor["value_template"]).render(value_json=payload).strip()
    return True, value


@pytest.mark.parametrize("name,directory", PACKAGES, ids=[name for name, _ in PACKAGES])
@pytest.mark.parametrize(
    "unique_id,field,value",
    [
        ("hapsic_health_measured_steam", "measured_steam_lbs_hr", -0.4),
        ("hapsic_health_production_efficiency", "production_efficiency", 275.6),
    ],
)
def test_legacy_health_consumers_accept_v1_and_preserve_scale(name, directory, unique_id, field, value):
    sensor = mqtt_sensor(directory, unique_id)
    available, output = rendered(sensor, {"health": {field: value}})
    assert available
    assert float(output) == pytest.approx(value)


@pytest.mark.parametrize("name,directory", PACKAGES, ids=[name for name, _ in PACKAGES])
@pytest.mark.parametrize(
    "unique_id,field,value",
    [
        ("hapsic_health_measured_steam", "measured_steam_lbs_hr", 0.0),
        ("hapsic_health_production_efficiency", "production_efficiency", 0.0),
    ],
)
def test_v2_ineligible_health_never_looks_like_zero_percent_health(name, directory, unique_id, field, value):
    sensor = mqtt_sensor(directory, unique_id)
    payload = {"health": {"math_revision": 2, "chi_eligible": False, field: value}}
    available, _ = rendered(sensor, payload)
    assert available is False


@pytest.mark.parametrize("name,directory", PACKAGES, ids=[name for name, _ in PACKAGES])
@pytest.mark.parametrize(
    "unique_id,field,value",
    [
        ("hapsic_health_measured_steam", "measured_steam_lbs_hr", -0.4),
        ("hapsic_health_production_efficiency", "production_efficiency", 275.6),
    ],
)
def test_v2_eligible_health_is_signed_and_scaled_once(name, directory, unique_id, field, value):
    sensor = mqtt_sensor(directory, unique_id)
    payload = {"health": {"math_revision": 2, "chi_eligible": True, field: value}}
    available, output = rendered(sensor, payload)
    assert available
    assert float(output) == pytest.approx(value)


@pytest.mark.parametrize("name,directory", PACKAGES, ids=[name for name, _ in PACKAGES])
@pytest.mark.parametrize(
    "unique_id,field",
    [
        ("hapsic_health_measured_steam", "measured_steam_lbs_hr"),
        ("hapsic_health_production_efficiency", "production_efficiency"),
    ],
)
def test_v2_null_health_is_unavailable_without_numeric_fallback(name, directory, unique_id, field):
    sensor = mqtt_sensor(directory, unique_id)
    payload = {"health": {"math_revision": 2, "chi_eligible": True, field: None}}
    available, _ = rendered(sensor, payload)
    assert available is False


@pytest.mark.parametrize("name,directory", PACKAGES, ids=[name for name, _ in PACKAGES])
def test_learning_helper_has_capacity_and_restores_previous_state(name, directory):
    definitions = [
        config["input_text"]["hapsic_boiler_curve"]
        for config in configs(directory)
        if "hapsic_boiler_curve" in config.get("input_text", {})
    ]
    assert len(definitions) == 1, f"Missing or duplicate learning helper in {name}"
    assert definitions[0]["max"] == 255
    assert "initial" not in definitions[0], "An initial value would erase persisted learning at HA startup"
