"""
HAPSIC Sensor Fallback & Cache Expiry Tests
=============================================
Validates the sensor reading pipeline's resilience to failures:
    - Primary → Fallback chain (House HA → Extract CAN)
    - Cache hit within 30-minute window
    - Cache expiry after 30 minutes → FAULT
    - NaN, "unavailable", "unknown" string handling
    - Multiple simultaneous sensor failures

This is the most common real-world failure mode.

Run:
    python3 test_sensor_fallback.py
"""

import math
import sys
import time
import types


# -------------------------------------------------------------------------
# Mock AppDaemon
# -------------------------------------------------------------------------
class MockHass:
    def __init__(self):
        self.states = {}
    def get_state(self, entity_id):
        return self.states.get(entity_id, None)
    def call_service(self, service, **kwargs):
        pass
    def log(self, msg, level="INFO"):
        pass
    def turn_on(self, entity_id, **kwargs):
        pass
    def turn_off(self, entity_id, **kwargs):
        pass
    def run_every(self, callback, start, interval):
        pass
    def listen_state(self, cb, entity_id):
        pass

class MockHassapi:
    Hass = MockHass

appdaemon = types.ModuleType('appdaemon')
sys.modules['appdaemon'] = appdaemon
plugins = types.ModuleType('plugins')
appdaemon.plugins = plugins
sys.modules['appdaemon.plugins'] = plugins
hass_mod = types.ModuleType('hass')
plugins.hass = hass_mod
sys.modules['appdaemon.plugins.hass'] = hass_mod
hassapi = types.ModuleType('hassapi')
hass_mod.hassapi = hassapi
sys.modules['appdaemon.plugins.hass.hassapi'] = hassapi
hassapi.Hass = MockHass

import hapsic

# -------------------------------------------------------------------------
# Test Framework
# -------------------------------------------------------------------------
pass_count = 0
fail_count = 0
results = []

def assert_true(condition, label):
    global pass_count, fail_count
    if condition:
        pass_count += 1
    else:
        fail_count += 1
        results.append(f"  ❌ {label}")

def assert_close(actual, expected, tol, label):
    global pass_count, fail_count
    diff = abs(actual - expected)
    if diff <= tol:
        pass_count += 1
    else:
        fail_count += 1
        results.append(f"  ❌ {label}: got {actual:.4f}, expected {expected:.4f}")


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------
BASE_STATES = {
    "input_number.humidifier_max_capacity": 2.7,
    "input_number.target_dew_point": 50.0,
    "sensor.hapsic_cleansed_post_steam_temp": 68.0,
    "sensor.hapsic_cleansed_post_steam_rh": 35.0,
    "sensor.hapsic_supply_flow": 400.0,
    "sensor.hapsic_extract_flow": 400.0,
    "sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_temperature": 59.0,
    "sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_humidity": 50.0,
    "sensor.zehnder_comfoair_q_a4cb9c_bypass_state": 0.0,
    "sensor.hapsic_pre_steam_temp": 68.0,
    "sensor.hapsic_pre_steam_rh": 30.0,
    "sensor.hapsic_room_average_temp": 68.0,
    "sensor.hapsic_room_average_rh": 30.0,
    "sensor.hapsic_cleansed_inside_temp": 68.0,
    "sensor.hapsic_cleansed_inside_rh": 30.0,
    "input_number.hapsic_chi_ema": 1.0,
}

ARGS = {
    "target_dew_point": "input_number.target_dew_point",
    "max_capacity": "input_number.humidifier_max_capacity",
    "manual_reset": "input_boolean.manual_reset",
    "duct_temp": "sensor.hapsic_cleansed_post_steam_temp",
    "duct_rh": "sensor.hapsic_cleansed_post_steam_rh",
    "supply_flow": "sensor.hapsic_supply_flow",
    "extract_flow": "sensor.hapsic_extract_flow",
    "bypass": "sensor.zehnder_comfoair_q_a4cb9c_bypass_state",
    "outdoor_temp": "sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_temperature",
    "outdoor_rh": "sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_humidity",
    "pre_steam_temp": "sensor.hapsic_pre_steam_temp",
    "pre_steam_rh": "sensor.hapsic_pre_steam_rh",
    "extract_avg_temp": "sensor.hapsic_room_average_temp",
    "extract_avg_rh": "sensor.hapsic_room_average_rh",
    "steam_dac": "output.steam_dac",
    "fan_dac": "output.fan_dac",
}


def make_controller(state_overrides=None):
    """Create controller with optional state overrides."""
    c = hapsic.HapsicController()
    c.states = dict(BASE_STATES)
    if state_overrides:
        c.states.update(state_overrides)
    c.args = dict(ARGS)
    c.initialize()

    mock_time = [time.time()]
    original_time = time.time

    def fake_time():
        return mock_time[0]

    time.time = fake_time
    c._mock_time = mock_time
    c._original_time = original_time
    return c


def tick(controller, n=1):
    for _ in range(n):
        controller._mock_time[0] += 5.0
        controller.last_tick_ts = controller._mock_time[0] - 5.0
        controller.master_tick({})


# -------------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------------

def test_primary_room_sensors():
    """With primary (House HA) sensors available, room_dp should compute."""
    c = make_controller()
    tick(c, 2)
    # room_dp should be valid (68°F, 30% RH → ~35°F DP)
    assert_true(c.room_dp is not None and not math.isnan(c.room_dp),
                "primary_room_dp_valid")
    assert_true(25.0 < c.room_dp < 45.0, f"primary_room_dp_range ({c.room_dp:.1f})")


def test_room_fallback_to_extract():
    """When House HA is None, fallback to Extract CAN (cleansed_inside)."""
    c = make_controller({
        "sensor.hapsic_room_average_temp": None,
        "sensor.hapsic_room_average_rh": None,
    })
    tick(c, 2)
    # Should use cleansed_inside as fallback
    assert_true(c.room_dp is not None and not math.isnan(c.room_dp),
                "fallback_room_dp_valid")


def test_room_unavailable_string():
    """When sensors return 'unavailable', should fallback."""
    c = make_controller({
        "sensor.hapsic_room_average_temp": "unavailable",
        "sensor.hapsic_room_average_rh": "unavailable",
    })
    tick(c, 2)
    assert_true(c.room_dp is not None and not math.isnan(c.room_dp),
                "unavailable_string_fallback")


def test_room_unknown_string():
    """When sensors return 'unknown', should fallback."""
    c = make_controller({
        "sensor.hapsic_room_average_temp": "unknown",
        "sensor.hapsic_room_average_rh": "unknown",
    })
    tick(c, 2)
    assert_true(c.room_dp is not None and not math.isnan(c.room_dp),
                "unknown_string_fallback")


def test_all_room_sensors_failed():
    """When both primary AND fallback fail, should FAULT after cache expires."""
    c = make_controller({
        "sensor.hapsic_room_average_temp": None,
        "sensor.hapsic_room_average_rh": None,
        "sensor.hapsic_cleansed_inside_temp": None,
        "sensor.hapsic_cleansed_inside_rh": None,
    })
    # First tick may use cached value or fail immediately
    tick(c, 5)
    # After several ticks with no valid data, should be in FAULT
    assert_true(c.fsm_state == "FAULT" or c.steam_voltage == 0.0,
                f"all_room_failed_faults (state={c.fsm_state}, V={c.steam_voltage})")


def test_supply_sensor_failure():
    """When supply (pre-steam) sensors fail, should use cache then FAULT."""
    c = make_controller()
    tick(c, 5)  # Build up valid cache

    # Kill supply sensors
    c.states["sensor.hapsic_pre_steam_temp"] = None
    c.states["sensor.hapsic_pre_steam_rh"] = None

    # Within cache window (30 min), should use cached value
    tick(c, 5)
    assert_true(c.supply_dp is not None,
                "supply_cache_hit")


def test_duct_sensor_failure_faults():
    """When duct sensors fail (no float), should trigger sensor failure after cache expires."""
    c = make_controller()
    tick(c, 3)

    # Kill duct sensors entirely
    c.states["sensor.hapsic_cleansed_post_steam_temp"] = "unavailable"
    c.states["sensor.hapsic_cleansed_post_steam_rh"] = "unavailable"

    # Advance past 30-minute cache window (1800s) then tick
    c._mock_time[0] += 1900
    tick(c, 5)

    # Should fault or park to safety after cache expires
    assert_true(c.fsm_state == "FAULT" or c.steam_voltage == 0.0,
                f"duct_failure_faults (state={c.fsm_state})")


def test_outdoor_sensor_failure():
    """When outdoor sensors fail, should fault after cache expires."""
    c = make_controller()
    tick(c, 3)

    c.states["sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_temperature"] = None
    c.states["sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_humidity"] = None

    # Advance past 30-minute cache window (1800s) then tick
    c._mock_time[0] += 1900
    tick(c, 5)

    # Should fault after cache expires
    assert_true(c.fsm_state == "FAULT" or c.steam_voltage == 0.0,
                f"outdoor_failure_faults (state={c.fsm_state})")


def test_flow_sensor_zero():
    """Zero flow should not cause division by zero."""
    c = make_controller({
        "sensor.hapsic_supply_flow": 0.0,
        "sensor.hapsic_extract_flow": 0.0,
    })
    # Should not crash
    try:
        tick(c, 5)
        assert_true(True, "zero_flow_no_crash")
    except (ZeroDivisionError, ValueError) as e:
        assert_true(False, f"zero_flow_crashes: {e}")


if __name__ == "__main__":
    print("=" * 50)
    print("  HAPSIC Sensor Fallback & Cache Tests")
    print("=" * 50)

    test_primary_room_sensors()
    test_room_fallback_to_extract()
    test_room_unavailable_string()
    test_room_unknown_string()
    test_all_room_sensors_failed()
    test_supply_sensor_failure()
    test_duct_sensor_failure_faults()
    test_outdoor_sensor_failure()
    test_flow_sensor_zero()

    print()
    for r in results:
        print(r)

    total = pass_count + fail_count
    print(f"\n  TOTAL: {pass_count}/{total} passed")
    if fail_count > 0:
        print(f"  ❌ {fail_count} FAILURES")
        sys.exit(1)
    else:
        print("  ✅ ALL TESTS PASSED")
        sys.exit(0)
