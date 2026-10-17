"""Behavioral regressions for legacy learning, restart, and MQTT repairs.

These execute the authoritative AppDaemon controller; only HA services and the
wall clock are replaced. Constructed humidity ratios represent a requested
signed delivery on the controller's documented fixed-density dry-air basis.
"""

import json

import hapsic_controller as module
import pytest

CURVE_ENTITY = "input_text.hapsic_boiler_curve"


@pytest.fixture
def make_legacy_controller(tmp_path, monkeypatch):
    clock = [10000.0]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])

    def make(curve=None, chi="0.94", backup_path=None):
        controller = module.HapsicController()
        states = {
            "input_number.humidifier_max_capacity": "2.7",
            "input_number.hapsic_chi_ema": chi,
            CURVE_ENTITY: curve or json.dumps({"v": 2, "curve": [0, 0, 0, 0], "counts": [0, 0, 0, 0]}),
            controller.SHELLY_LIGHT_ENTITY: "off",
        }
        calls = []

        def state(entity_id, attribute=None, **kwargs):
            value = states.get(entity_id)
            if attribute == "all":
                return {"state": value, "attributes": {"max": 255}} if value is not None else None
            if attribute == "max":
                return 255
            return value

        def service(name, **kwargs):
            calls.append((name, kwargs))
            if name in ("input_text/set_value", "input_number/set_value"):
                states[kwargs["entity_id"]] = kwargs["value"]

        controller.get_state = state
        controller.call_service = service
        controller.args = {"learning_backup_path": str(backup_path or tmp_path / "legacy-curve-v1.json")}
        controller.initialize()
        controller.supply_flow = 400.0
        controller.supply_w = 40.0
        controller.room_w = 45.0
        controller.outdoor_w = 30.0
        controller.steam_voltage = 9.0
        controller.boil_achieved = True
        controller.stasis_active = False
        controller.tick_counter = 1
        controller._test_states = states
        controller._test_calls = calls
        controller._test_clock = clock
        return controller

    return make


def deliver(controller, lbs_per_hour):
    dry_air_lbs_hr = controller.supply_flow * 0.5886 * 60.0 * controller.RHO
    controller.duct_w = controller.supply_w + lbs_per_hour * 7000.0 / dry_air_lbs_hr
    controller.run_diagnostics()


def mqtt(controller):
    controller.publish_telemetry()
    raw = next(
        kwargs["payload"]
        for name, kwargs in reversed(controller._test_calls)
        if name == "mqtt/publish" and kwargs["topic"] == "hapsic/telemetry/state"
    )
    return json.loads(raw, parse_constant=lambda token: pytest.fail(f"Non-JSON number: {token}"))


def test_first_fifty_observations_use_arithmetic_mean_and_then_existing_ema(make_legacy_controller):
    controller = make_legacy_controller()
    observations = [10.0] + [2.0] * 49
    for count, delivery in enumerate(observations, 1):
        deliver(controller, delivery)
        assert controller.boiler_curve[3] == pytest.approx(sum(observations[:count]) / count)
        assert controller.boiler_curve_counts[3] == count
        if count == 49:
            assert controller.get_effective_max_capacity() == pytest.approx(
                controller.MAX_CAPACITY * controller.chi_ema
            )
        if count == 50:
            assert controller.get_effective_max_capacity() == pytest.approx(2.16)
    deliver(controller, 4.0)
    assert controller.boiler_curve_counts[3] == 51
    assert controller.boiler_curve[3] == pytest.approx(2.16 * 0.998 + 4.0 * 0.002)


def test_constant_delivery_is_unbiased_at_training_boundary(make_legacy_controller):
    controller = make_legacy_controller()
    for _ in range(50):
        deliver(controller, 2.0)
    assert controller.get_effective_max_capacity() == pytest.approx(2.0)
    assert controller.voltage_for_steam_rate(2.0) == pytest.approx(9.0)


@pytest.mark.parametrize("delivery", [0.0, -1.0])
def test_nonpositive_delivery_does_not_advance_curve_training(make_legacy_controller, delivery):
    controller = make_legacy_controller()
    deliver(controller, delivery)
    assert controller.boiler_curve_counts == [0, 0, 0, 0]
    assert controller.boiler_curve == [0.0, 0.0, 0.0, 0.0]
    assert controller.chi_eligible is True
    assert controller.chi_instant == 0.0
    assert mqtt(controller)["health"]["measured_steam_lbs_hr"] == delivery


def test_partial_training_restores_exact_count_and_resumes_mean(make_legacy_controller):
    original = make_legacy_controller()
    for _ in range(23):
        deliver(original, 2.0)
    original.tick_counter = 60
    deliver(original, 4.0)
    stored = original._test_states[CURVE_ENTITY]
    record = json.loads(stored)
    assert record["v"] == 2
    assert record["counts"] == [0, 0, 0, 24]
    assert len(stored) <= 255

    restored = make_legacy_controller(stored)
    assert restored.boiler_curve_counts == [0, 0, 0, 24]
    assert restored.boiler_curve[3] == pytest.approx(50.0 / 24.0, abs=5e-5)
    deliver(restored, 4.0)
    assert restored.boiler_curve_counts[3] == 25
    assert restored.boiler_curve[3] == pytest.approx(54.0 / 25.0, abs=5e-5)
    assert restored.get_effective_max_capacity() == pytest.approx(restored.MAX_CAPACITY * restored.chi_ema)


def test_legacy_payload_is_backed_up_without_fabricating_trained_counts(make_legacy_controller, tmp_path):
    legacy = "[0.1,0.5,0.8,1.2]"
    backup = tmp_path / "old-curve.json"
    controller = make_legacy_controller(legacy, backup_path=backup)
    assert backup.read_text() == legacy
    assert controller.boiler_curve_counts == [0, 0, 0, 0]
    assert controller.boiler_curve == [0.0, 0.0, 0.0, 0.0]
    assert controller.chi_ema == 0.94
    controller.tick_counter = 60
    deliver(controller, 2.0)
    restored = make_legacy_controller(controller._test_states[CURVE_ENTITY], backup_path=backup)
    assert restored.boiler_curve_counts == [0, 0, 0, 1]
    assert backup.read_text() == legacy


def test_failed_backup_prevents_destructive_helper_migration(make_legacy_controller, tmp_path):
    blocked_parent = tmp_path / "regular-file"
    blocked_parent.write_text("sentinel")
    legacy = "[0.1,0.5,0.8,1.2]"
    controller = make_legacy_controller(legacy, backup_path=blocked_parent / "old-curve.json")
    controller.tick_counter = 60
    deliver(controller, 2.0)
    assert controller._test_states[CURVE_ENTITY] == legacy
    assert blocked_parent.read_text() == "sentinel"


@pytest.mark.parametrize(
    "record",
    [
        {"v": 2, "curve": [0, 0, 0, 1.2], "counts": [0, 0, 0, True]},
        {"v": 2, "curve": [0, 0, 0, 1.2], "counts": [0, 0, 0, -1]},
        {"v": 2, "curve": [0, 0, 0, 1.2], "counts": [0, 0, 0, 0]},
        {"v": 2, "curve": [0, 0, 0, float("nan")], "counts": [0, 0, 0, 50]},
        {"v": 3, "curve": [0, 0, 0, 1.2], "counts": [0, 0, 0, 50]},
    ],
)
def test_invalid_persisted_learning_is_not_promoted_to_trained(make_legacy_controller, record):
    controller = make_legacy_controller(json.dumps(record))
    assert controller.boiler_curve_counts == [0, 0, 0, 0]
    assert controller.boiler_curve == [0.0, 0.0, 0.0, 0.0]


def test_count_saturates_without_changing_ema_update(make_legacy_controller):
    maximum_count = 2**32 - 1
    controller = make_legacy_controller(json.dumps({"v": 2, "curve": [0, 0, 0, 2], "counts": [0, 0, 0, maximum_count]}))
    deliver(controller, 4.0)
    assert controller.boiler_curve_counts[3] == maximum_count
    assert controller.boiler_curve[3] == pytest.approx(2.004)


@pytest.mark.parametrize("blocked", ["cold", "stasis", "low_command", "low_theory"])
def test_algorithm_ineligible_tick_clears_chi_without_learning(make_legacy_controller, blocked):
    controller = make_legacy_controller()
    deliver(controller, 2.0)
    previous_curve = list(controller.boiler_curve)
    previous_counts = list(controller.boiler_curve_counts)
    previous_chi_ema = controller.chi_ema
    if blocked == "cold":
        controller.boil_achieved = False
    elif blocked == "stasis":
        controller.stasis_active = True
    elif blocked == "low_command":
        controller.steam_voltage = 1.0
    else:
        controller.MAX_CAPACITY = 0.01
    controller.run_diagnostics()
    payload = mqtt(controller)
    assert payload["health"]["chi_eligible"] is False
    assert payload["health"]["chi_ratio"] is None
    assert controller.boiler_curve == previous_curve
    assert controller.boiler_curve_counts == previous_counts
    assert controller.chi_ema == previous_chi_ema


def test_watchdog_fault_publishes_final_zero_command_and_no_stale_diagnostics(make_legacy_controller):
    controller = make_legacy_controller()
    deliver(controller, 2.0)
    controller.last_tick_ts = controller._test_clock[0] - 125.0
    controller.master_tick({})
    payload = mqtt(controller)
    assert payload["fsm"]["state"] == "FAULT"
    assert payload["io"]["steam_volts"] == 0.0
    assert payload["io"]["steam_mass"] == 0.0
    assert payload["health"]["chi_eligible"] is False
    assert payload["health"]["chi_ratio"] is None
    assert payload["health"]["measured_steam_lbs_hr"] is None
    assert payload["health"]["boil_status"] == "UNKNOWN"
    assert payload["physics"]["flux_net"] is None
    assert payload["physics"]["loss_vent"] is None


@pytest.mark.parametrize("delivery", [3.5, -1.0])
def test_actual_mqtt_preserves_signed_delivery_and_single_percent_scaling(make_legacy_controller, delivery):
    controller = make_legacy_controller()
    deliver(controller, delivery)
    payload = mqtt(controller)
    theory = controller.steam_voltage / 10 * controller.MAX_CAPACITY
    assert payload["health"]["math_revision"] == 2
    assert payload["health"]["chi_eligible"] is True
    assert payload["health"]["measured_steam_lbs_hr"] == delivery
    assert payload["health"]["production_efficiency"] == pytest.approx(
        round(100 * delivery / theory, 1) if delivery > 0 else 0.0
    )
    assert payload["health"]["chi_ratio"] == pytest.approx(round(max(0, min(2, delivery / theory)), 3))


def test_actual_mqtt_converts_nonfinite_numbers_to_json_null(make_legacy_controller):
    controller = make_legacy_controller()
    deliver(controller, 2.0)
    controller.calc_flux = float("nan")
    controller.calc_loss_vent = float("inf")
    payload = mqtt(controller)
    assert payload["physics"]["flux_net"] is None
    assert payload["physics"]["loss_vent"] is None


def test_existing_backup_is_resynchronized_before_migration(make_legacy_controller, tmp_path, monkeypatch):
    legacy = "[0.1,0.5,0.8,1.2]"
    backup = tmp_path / "existing.json"
    backup.write_text(legacy)
    monkeypatch.setattr(module.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("durability failed")))
    controller = make_legacy_controller(legacy, backup_path=backup)
    assert controller.learning_write_ready is False
    assert controller._test_states[CURVE_ENTITY] == legacy


def test_late_helper_readiness_preserves_live_training(make_legacy_controller):
    controller = make_legacy_controller()
    controller.learning_write_ready = False
    for _ in range(7):
        deliver(controller, 2.0)
    counts = list(controller.boiler_curve_counts)
    controller._checkpoint_learning()
    assert controller.learning_write_ready is True
    assert controller.boiler_curve_counts == counts
    assert json.loads(controller._test_states[CURVE_ENTITY])["counts"] == counts


def test_checkpoint_honors_actual_helper_size_limit(make_legacy_controller):
    controller = make_legacy_controller()
    original = controller.get_state
    controller.get_state = lambda entity, attribute=None, **kwargs: (
        10 if entity == CURVE_ENTITY and attribute == "max" else original(entity, attribute, **kwargs)
    )
    before = controller._test_states[CURVE_ENTITY]
    controller._checkpoint_learning()
    assert controller._test_states[CURVE_ENTITY] == before
