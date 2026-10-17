"""Behavioral contract for the independent measurement observer."""

import copy
import json
import math

import pytest

import conftest  # noqa: F401
import hapsic_controller as controller


def sample(value, unit, now=1000, *, accuracy=None):
    return dict(value=value, unit=unit, reported_at=now, report_id=str(now),
                source="physical-source", verified=True, accuracy=accuracy)


def inputs(now=1000, voltage=9.5, downstream_rh=50, *, known=False):
    values = {
        "upstream_t": sample(20, "°C", now, accuracy=0.1 if known else None),
        "upstream_rh": sample(30, "%", now, accuracy=0.5 if known else None),
        "downstream_t": sample(20, "°C", now, accuracy=0.1 if known else None),
        "downstream_rh": sample(downstream_rh, "%", now, accuracy=0.5 if known else None),
        "flow": sample(200, "m³/h", now, accuracy=2 if known else None),
        "pressure": sample(101325, "Pa", now, accuracy=20 if known else None),
        "command": sample(voltage, "V", now),
    }
    return values


def config(**overrides):
    return dict(airflow_basis="actual_upstream", reporting_verified=True, **overrides)


def engine(**overrides):
    assert hasattr(controller, "DeliveryObserver"), "Independent delivery observer is missing"
    return controller.DeliveryObserver(config(**overrides))


def test_psychrolib_known_point_and_signed_delivery():
    observer = engine()
    measured = observer.measure(inputs(downstream_rh=50), 1000)
    # ASHRAE/PsychroLib SI reference at 20 C, 101325 Pa; 200 m3/h upstream.
    assert measured["raw_kg_h"] == pytest.approx(0.6978, abs=0.002)
    negative = observer.measure(inputs(downstream_rh=20), 1000)
    assert negative["raw_kg_h"] < 0
    assert measured["uncertainty_kg_h"] is None


@pytest.mark.parametrize("unit,value", [("Pa", 101325), ("kPa", 101.325), ("hPa", 1013.25),
                                        ("inHg", 29.9212524)])
def test_pressure_and_temperature_conversion(unit, value):
    observer = engine()
    values = inputs()
    expected = observer.measure(values, 1000)["raw_kg_h"]
    values["pressure"].update(value=value, unit=unit)
    values["upstream_t"].update(value=68, unit="°F")
    assert observer.measure(values, 1000)["raw_kg_h"] == pytest.approx(expected, rel=1e-6)


@pytest.mark.parametrize("problem", ["missing", "nan", "rh", "age", "skew", "unit"])
def test_invalid_sources_never_become_valid_zero(problem):
    observer = engine()
    values = inputs()
    if problem == "missing": del values["downstream_rh"]
    if problem == "nan": values["flow"]["value"] = math.nan
    if problem == "rh": values["downstream_rh"]["value"] = 101
    if problem == "age": values["flow"]["reported_at"] = 800
    if problem == "skew": values["downstream_rh"]["reported_at"] = 950
    if problem == "unit": values["pressure"]["unit"] = "ambiguous"
    result = observer.measure(values, 1000)
    assert result["status"] == "unavailable"
    assert result["raw_kg_h"] is None
    json.dumps(result, allow_nan=False)


def test_unverified_provenance_and_airflow_prevent_evidence():
    observer = engine()
    values = inputs()
    values["flow"]["verified"] = False
    assert observer.measure(values, 1000)["eligible"] is False
    assert controller.DeliveryObserver({}).measure(inputs(), 1000)["eligible"] is False


def test_known_error_encloses_the_point_without_clipping():
    result = engine().measure(inputs(known=True), 1000)
    assert result["lower_kg_h"] < result["raw_kg_h"] < result["upper_kg_h"]
    assert result["uncertainty_kg_h"] > 0


def advance(observer, start, duration, voltage=0, downstream_rh=30, known=False):
    result = None
    for now in range(start, start + duration + 1, 5):
        result = observer.observe(inputs(now, voltage, downstream_rh, known=known), now, now, synced=True)
    return result


def test_warmup_is_independent_of_successful_production():
    observer = engine()
    result = advance(observer, 1000, 895, 9.5, 30)
    assert result["qualification"]["reason"] == "WARMUP"
    assert result["qualification"]["qualified_seconds"] == 0
    result = advance(observer, 1900, 300, 9.5, 30)
    assert result["qualification"]["stable"] is True
    assert result["measurement"]["raw_kg_h"] == pytest.approx(0)


def test_duplicate_polling_and_clock_jumps_do_not_create_duration():
    observer = engine()
    snapshot = inputs(1000)
    observer.observe(snapshot, 1000, 1000, synced=True)
    for now in range(1005, 1100, 5):
        result = observer.observe(snapshot, now, now, synced=True)
    assert result["qualification"]["qualified_seconds"] == 0
    result = observer.observe(inputs(1100), 1100, 1200, synced=True)
    assert result["clock_valid"] is False


def test_input_snapshots_are_not_modified():
    observer = engine()
    values = inputs()
    before = copy.deepcopy(values)
    observer.observe(values, 1000, 1000, synced=True)
    assert values == before
