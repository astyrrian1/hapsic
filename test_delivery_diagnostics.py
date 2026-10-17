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


def commission_baseline(observer, known=False):
    observer.command("baseline_start")
    now = 1000
    for period in range(4):
        advance(observer, now, 1505, known=known)
        now += 1510
        advance(observer, now, 5, voltage=1, known=known)
        now += 10
    candidate = observer.review()
    assert candidate["ready"] is True
    observer.command("baseline_accept", candidate["revision"])
    return now


def test_baseline_requires_four_distinct_off_periods_and_explicit_acceptance():
    observer = engine()
    observer.command("baseline_start")
    advance(observer, 1000, 8000)
    assert len(observer.review()["windows"]) == 1
    assert observer.baseline is None
    with pytest.raises(ValueError): observer.command("baseline_accept", observer.review()["revision"])
    observer = engine()
    commission_baseline(observer)
    assert observer.baseline["offset"] == pytest.approx(0)
    assert observer.baseline["uncertainty_known"] is False


def test_replayed_acceptance_and_generation_change_cannot_reuse_evidence():
    observer = engine()
    commission_baseline(observer)
    accepted = observer.generation
    with pytest.raises(ValueError): observer.command("baseline_accept", 1)
    observer.command("baseline_reset")
    assert observer.baseline is None and observer.reference is None
    assert observer.generation != accepted
    assert not observer.evidence


def test_baseline_offset_does_not_normalize_away_zero_production():
    observer = engine()
    commission_baseline(observer)
    result = observer.measure(inputs(downstream_rh=30), 1000)
    assert result["corrected_kg_h"] == pytest.approx(0)
    assert result["eligible"] is True


def reference_cycle(observer, now, *, voltage=9.5, rh=50, known=False):
    advance(observer, now, 905, known=known)
    now += 910
    result = advance(observer, now, 1810, voltage, rh, known=known)
    return now + 1815, result


def commissioned(known=False):
    observer = engine()
    now = commission_baseline(observer, known)
    observer.command("reference_start", healthy=True)
    for _ in range(3):
        now, _ = reference_cycle(observer, now, known=known)
    review = observer.review()
    assert review["ready"] is True
    observer.command("reference_accept", review["revision"])
    return observer, now


def test_frozen_reference_retains_zero_and_does_not_clip_excursions():
    observer, now = commissioned()
    frozen = copy.deepcopy(observer.reference)
    now, result = reference_cycle(observer, now, rh=30)
    assert result["delivery"]["value"] == pytest.approx(0)
    now, result = reference_cycle(observer, now, rh=90)
    assert result["delivery"]["value"] > 100
    assert observer.reference == frozen


def test_unknown_uncertainty_never_warns_even_with_sustained_failure():
    observer, now = commissioned()
    for _ in range(9):
        now, result = reference_cycle(observer, now, rh=30)
    assert result["canister"]["value"] == pytest.approx(0)
    assert result["canister"]["status"] == "provisional"
    assert result["alert"]["decision"] == "inconclusive"


def test_persistence_restores_committed_evidence_but_not_continuity(tmp_path):
    observer, now = commissioned()
    now, _ = reference_cycle(observer, now)
    path = tmp_path / "diagnostics.json"
    assert hasattr(controller, "DiagnosticStore"), "Atomic diagnostic persistence is missing"
    store = controller.DiagnosticStore(path)
    store.save(observer.export_state())
    restored = engine()
    restored.restore(store.load())
    assert restored.reference == observer.reference
    assert list(restored.evidence) == list(observer.evidence)
    assert restored.previous is None and not restored.window
    result = restored.observe(inputs(now, 9.5), now, now, synced=True)
    assert result["qualification"]["reason"] == "WARMUP"
    path.write_text('{"schema_version":999}')
    with pytest.raises(ValueError): restored.restore(store.load())
