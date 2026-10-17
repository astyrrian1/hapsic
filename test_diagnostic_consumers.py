"""Exercise the distributed MQTT extraction contract with real JSON/Jinja."""

import json
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment

ROOT = Path(__file__).parent


def sensors():
    path = ROOT / "packages/hapsic_diagnostics.yaml"
    assert path.exists(), "Versioned diagnostic MQTT consumers are missing"
    return yaml.safe_load(path.read_text())["mqtt"]["sensor"]


@pytest.mark.parametrize("value", [None, 0.0, -0.1, 275.6])
def test_numeric_values_are_neither_defaulted_nor_rescaled(value):
    sensor = next(s for s in sensors() if s["unique_id"] == "hapsic_delivery_performance")
    rendered = Environment().from_string(sensor["value_template"]).render(value_json={"delivery": {"value": value}})
    assert rendered.strip() == str(value)


def test_consumers_expire_and_reject_wrong_producer_or_schema():
    env = Environment()
    for sensor in sensors():
        assert sensor["expire_after"] == 30
        template = env.from_string(sensor["availability_template"])
        for producer, version, expected in [("hapsic-python", 1, "online"), ("hapsic-desk", 1, "offline"),
                                            ("hapsic-python", 999, "offline")]:
            value = dict(producer_id=producer, schema_version=version)
            assert template.render(value_json=value).strip() == expected
        assert "float(0)" not in json.dumps(sensor)


def test_dashboard_uses_estimates_and_has_no_clipped_health_scale():
    content = (ROOT / "dashboards/mission-control.yaml").read_text()
    assert "sensor.hapsic_estimated_steam_delivery" in content
    assert "sensor.hapsic_delivery_performance" in content
    assert "name: Actual Steam" not in content
    assert "name: Effective Max" not in content
    assert "max: 120" not in content
