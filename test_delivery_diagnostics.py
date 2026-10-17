"""Behavioral contract for the independent measurement observer."""

import copy
import json
import math

import hapsic_controller as controller
import pytest

import conftest  # noqa: F401


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
    assert measured["raw_kg_h"] == pytest.approx(0.6995387620211214, rel=1e-9)
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


def test_diagnostic_app_has_no_actuator_calls_on_tick_or_failure(tmp_path):
    assert hasattr(controller, "HapsicDiagnostics"), "Independent AppDaemon application is missing"
    from unittest.mock import MagicMock

    app = controller.HapsicDiagnostics()
    app.args = {"data_path": str(tmp_path / "diagnostics.json")}
    app.call_service = MagicMock()
    app.listen_event = MagicMock()
    app.get_state = MagicMock(return_value=None)
    app.initialize()
    app.diagnostic_tick({})
    assert app.call_service.call_count > 0
    assert {call.args[0] for call in app.call_service.call_args_list} <= {"mqtt/publish", "mqtt/subscribe"}
    app.observer.observe = MagicMock(side_effect=RuntimeError("Injected observer failure"))
    app.diagnostic_tick({})
    assert {call.args[0] for call in app.call_service.call_args_list} <= {"mqtt/publish", "mqtt/subscribe"}
    app.terminate()


def test_fault_and_interruption_between_physical_samples_break_qualification():
    observer = engine()
    advance(observer, 1000, 1300, 9.5)
    before = observer.qualified_seconds
    values = inputs(2300, 0)
    observer.observe(values, 2301, 2301, synced=True)
    assert len(observer.window) <= 1  # A new off-period window may start immediately.
    assert observer.qualified_seconds == 0 <= before


def test_unsynchronized_future_clock_cannot_delete_retained_evidence():
    observer, now = commissioned()
    now, _ = reference_cycle(observer, now)
    before = copy.deepcopy(list(observer.evidence))
    observer.observe(inputs(now), now, now + 20 * 86400, synced=False)
    assert list(observer.evidence) == before


def test_corrupt_storage_is_rejected_without_partial_installation():
    observer, _ = commissioned()
    state = observer.export_state()
    state["baseline"]["offset"] = "corrupt"
    target = engine()
    with pytest.raises(ValueError): target.restore(state)
    assert target.baseline is None and target.reference is None


def test_source_identity_changes_invalidate_calibration():
    observer = engine()
    now = commission_baseline(observer)
    values = inputs(now, 9.5)
    values["upstream_rh"]["source"] = "replacement-physical-sensor"
    result = observer.observe(values, now, now, synced=True)
    assert observer.baseline is None
    assert result["qualification"]["reason"] == "SOURCE_IDENTITY_CHANGED"


def test_restarts_cannot_manufacture_separate_baseline_off_periods():
    observer = engine()
    observer.command("baseline_start")
    now = 1000
    for _ in range(4):
        advance(observer, now, 1505)
        restored = engine()
        restored.restore(observer.export_state())
        observer = restored
        now += 1510
    assert len(observer.review()["windows"]) == 1
    assert observer.review()["ready"] is False


def test_restart_quarantines_future_evidence():
    observer, now = commissioned()
    now, _ = reference_cycle(observer, now)
    restored = engine()
    restored.restore(observer.export_state())
    result = restored.observe(inputs(now - 1000), now + 5, now - 1000, synced=True)
    assert result["canister"]["value"] is None
    assert result["alert"]["decision"] == "inconclusive"


def test_return_to_envelope_does_not_credit_previous_outside_measurement():
    observer, now = commissioned()
    now, _ = reference_cycle(observer, now)
    outside = inputs(now, 9.5)
    outside["upstream_t"]["value"] = 20.01
    observer.observe(outside, now, now, synced=True)
    observer.observe(inputs(now + 5, 9.5), now + 5, now + 5, synced=True)
    assert not observer.delivery_window


def test_ack_transport_failure_cannot_reject_a_durable_action(tmp_path):
    from concurrent.futures import Future
    from unittest.mock import MagicMock

    app = controller.HapsicDiagnostics()
    app.args = {"data_path": str(tmp_path / "state.json")}
    app.call_service = MagicMock()
    app.listen_event = MagicMock()
    app.initialize()
    replacement = engine()
    replacement.command("baseline_start")
    future = Future()
    future.set_result(None)
    app.pending_save = (future, replacement, {"request_id": "durable-action"})
    app._publish = MagicMock(side_effect=RuntimeError("MQTT disconnected"))
    app._save_completed()
    assert app.observer is replacement
    assert app.storage_error is None
    assert app._publish.call_args.args[1]["status"] == "accepted"
    app.terminate()


def test_alert_contract_publishes_configured_thresholds_even_before_calibration():
    result = engine().telemetry(1000)
    assert result["alert"]["warning"] == 60
    assert result["alert"]["recovery"] == 65


def test_clock_loss_breaks_observed_off_continuity():
    observer = engine()
    observer.command("baseline_start")
    advance(observer, 1000, 1200)
    observer.observe(inputs(2205, 0, 30), 2205, 2205, synced=False)
    advance(observer, 2210, 900)
    assert not observer.review()["windows"]


def test_raw_input_relay_preserves_source_observation_identity(tmp_path):
    from unittest.mock import MagicMock

    app = controller.HapsicDiagnostics()
    app.args = {"data_path": str(tmp_path / "state.json")}
    app.call_service = MagicMock()
    app.listen_event = MagicMock()
    app.initialize()
    app.get_state = MagicMock(return_value={"state": "68", "last_reported": 1000,
                                           "attributes": {"unit_of_measurement": "°F"}})
    app._publish = MagicMock()
    app.diagnostic_tick({})
    packets = [call.args[1] for call in app._publish.call_args_list
               if call.args[0].endswith("/inputs")]
    assert packets, "Firmware needs source observations, not HA receipt timestamps"
    first = packets[-1]["samples"]["upstream_t"]
    assert (first["value"], first["unit"], first["reported_at"], first["report_id"]) == (68, "°F", 1000, "1000")
    app.diagnostic_tick({})
    second = [call.args[1] for call in app._publish.call_args_list
              if call.args[0].endswith("/inputs")][-1]["samples"]["upstream_t"]
    assert second == first, "Polling cached state must not manufacture another observation"
    app.terminate()


def test_known_full_canister_warning_hysteresis_and_episode_survive_restart():
    observer, now = commissioned(known=True)
    for _ in range(3):
        advance(observer, now, 905, known=True)
        now += 910
        result = advance(observer, now, 3605, 9.5, 30, known=True)
        now += 3610
    assert result["canister"]["ready"] is True
    assert result["canister"]["seconds"] == pytest.approx(7200)
    assert result["alert"]["decision"] == "warn"
    episode = result["alert"]["episode"]
    warning_evidence = result["alert"]["evidence"]
    assert warning_evidence["upper"] < 60
    restored = engine()
    restored.restore(observer.export_state())
    result = restored.observe(inputs(now, 9.5, 30, known=True), now, now, synced=True)
    assert result["alert"]["episode"] == episode
    assert result["alert"]["decision"] == "warn"
    assert result["alert"]["evidence"] == warning_evidence
    for _ in range(3):
        advance(restored, now, 905, known=True)
        now += 910
        result = advance(restored, now, 3605, 9.5, 50, known=True)
        now += 3610
    assert result["canister"]["seconds"] == pytest.approx(7200)
    assert result["alert"]["decision"] == "clear"
    assert result["alert"]["episode"] == episode
    stale = restored.telemetry(now + 86401)
    assert stale["canister"]["status"] == "stale"
    assert stale["alert"]["decision"] == "inconclusive"


def test_controller_outputs_and_learning_are_identical_with_observer_faults(monkeypatch):
    from test_output_safety import make_controller

    plain = make_controller()
    observed = make_controller()
    plain_calls, observed_calls = [], []
    plain.call_service = lambda *args, **kwargs: plain_calls.append((args, kwargs))
    observed.call_service = lambda *args, **kwargs: observed_calls.append((args, kwargs))
    observer = engine()
    now = 100000
    monkeypatch.setattr(controller.time, "time", lambda: now)
    for tick in range(500):
        now += 5
        for control in (plain, observed):
            control.last_tick_ts = now - 5
            control.master_tick({})
        values = inputs(now, observed.steam_voltage)
        if tick % 13 == 0: values["flow"]["value"] = math.nan
        observer.observe(values, now, now, synced=tick % 17 != 0)
        if tick % 19 == 0:
            with pytest.raises(ValueError): observer.restore({"schema_version": 999})
        for field in ("fsm_state", "steam_voltage", "chi_ema", "boiler_curve", "boiler_curve_counts"):
            assert getattr(plain, field) == getattr(observed, field), field
    assert plain_calls == observed_calls


def test_clock_loss_never_republishes_an_old_measurement_as_current():
    observer = engine()
    observer.observe(inputs(1000), 1000, 1000, synced=True)
    result = observer.observe(inputs(1005), 1005, 1005, synced=False)
    assert result["measurement"]["corrected_kg_h"] is None
    assert result["measurement"]["status"] == "unavailable"


def test_outside_baseline_envelope_is_explicitly_provisional():
    observer = engine()
    now = commission_baseline(observer, known=True)
    values = inputs(now, known=True)
    values["upstream_t"]["value"] = 20.1
    result = observer.measure(values, now)
    assert result["status"] == "provisional"
    assert result["reason"] == "OUTSIDE_BASELINE_ENVELOPE"


def test_reference_review_shows_incomplete_bin_progress_without_accepting_it():
    observer = engine()
    now = commission_baseline(observer)
    observer.command("reference_start", healthy=True)
    reference_cycle(observer, now)
    candidate = observer.review()
    assert candidate["bins"], "Review must show partial qualified minutes and contributing cycles"
    bucket = next(iter(candidate["bins"].values()))
    assert bucket["seconds"] > 0 and len(bucket["cycles"]) == 1
    assert candidate["ready"] is False
    with pytest.raises(ValueError): observer.command("reference_accept", candidate["revision"])


def test_adapter_transport_failure_breaks_off_and_warmup_history(tmp_path):
    from unittest.mock import MagicMock

    app = controller.HapsicDiagnostics()
    app.args = {"data_path": str(tmp_path / "state.json")}
    app.call_service = MagicMock()
    app.listen_event = MagicMock()
    app.initialize()
    app.observer.zero_since = 100
    app.observer.demand_since = 100
    app._publish = MagicMock(side_effect=RuntimeError("MQTT disconnected"))
    app.diagnostic_tick({})
    assert app.observer.zero_since is None and app.observer.demand_since is None
    app.terminate()
