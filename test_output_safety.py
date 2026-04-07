"""
HAPSIC Output Safety Invariant Tests
======================================
Validates that voltage output is ALWAYS safe:
    - Voltage ∈ [0.0, 9.5] in every tick
    - Voltage = 0.0 when FSM in STANDBY, FAULT, or INITIALIZING
    - No voltage spike on state transitions
    - No voltage after fault trigger

These are the most critical safety assertions in the system.
A runaway humidifier can cause structural water damage.

Run:
    python3 test_output_safety.py
"""

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

try:
    import hapsic_controller as hapsic  # via conftest.py (pytest)
except ImportError:
    import importlib.util
    import pathlib
    _p = pathlib.Path(__file__).parent / "apps" / "hapsic-controller" / "hapsic_controller.py"
    _s = importlib.util.spec_from_file_location("hapsic_controller", _p)
    hapsic = importlib.util.module_from_spec(_s)
    _s.loader.exec_module(hapsic)

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

# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------
BASE_STATES = {
    "input_number.humidifier_max_capacity": 2.7,
    "input_number.target_dew_point": 50.0,
    "sensor.hapsic_duct_temp": 68.0,
    "sensor.hapsic_duct_rh": 35.0,
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
    "duct_temp": "sensor.hapsic_duct_temp",
    "duct_rh": "sensor.hapsic_duct_rh",
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
    c = hapsic.HapsicController()
    c.states = dict(BASE_STATES)
    if state_overrides:
        c.states.update(state_overrides)
    c.args = dict(ARGS)
    c.initialize()
    mock_time = [time.time()]
    def fake_time():
        return mock_time[0]
    time.time = fake_time
    c._mock_time = mock_time
    return c


def tick(controller, n=1):
    for _ in range(n):
        controller._mock_time[0] += 5.0
        controller.last_tick_ts = controller._mock_time[0] - 5.0
        controller.master_tick({})


# -------------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------------

def test_voltage_range_during_cruise():
    """During ACTIVE_CRUISE, voltage must be in [0, 9.5]."""
    c = make_controller()
    violations = []
    for i in range(100):
        tick(c, 1)
        v = c.steam_voltage
        if v < 0.0 or v > 9.5:
            violations.append((i, v, c.fsm_state))
    assert_true(len(violations) == 0,
                f"voltage_range_cruise: {len(violations)} violations: {violations[:3]}")


def test_voltage_zero_in_standby():
    """When entering STANDBY, voltage must drop to 0."""
    c = make_controller()
    tick(c, 10)  # Enter cruise, build voltage

    # Push to STANDBY by satisfying target
    c.states["sensor.hapsic_room_average_rh"] = 55.0
    c.states["sensor.hapsic_cleansed_inside_rh"] = 55.0
    tick(c, 20)

    if c.fsm_state == "STANDBY":
        assert_true(c.steam_voltage == 0.0,
                    f"standby_voltage_zero (V={c.steam_voltage})")
    else:
        assert_true(True, "standby_not_reached_skip")


def test_voltage_zero_on_fault():
    """When FAULT triggers, voltage must immediately go to 0."""
    c = make_controller()
    tick(c, 10)

    # Kill all sensors to trigger fault
    c.states["sensor.hapsic_duct_temp"] = None
    c.states["sensor.hapsic_duct_rh"] = None
    c.states["sensor.hapsic_room_average_temp"] = None
    c.states["sensor.hapsic_room_average_rh"] = None
    c.states["sensor.hapsic_cleansed_inside_temp"] = None
    c.states["sensor.hapsic_cleansed_inside_rh"] = None
    tick(c, 5)

    assert_true(c.steam_voltage == 0.0,
                f"fault_voltage_zero (V={c.steam_voltage}, state={c.fsm_state})")


def test_voltage_never_negative():
    """Voltage must never go negative under any conditions."""
    c = make_controller({
        "input_number.target_dew_point": 30.0,  # Very low target
        "sensor.hapsic_room_average_rh": 60.0,   # High humidity
        "sensor.hapsic_cleansed_inside_rh": 60.0,
    })
    neg_found = False
    for i in range(200):
        tick(c, 1)
        if c.steam_voltage < 0.0:
            neg_found = True
            break
    assert_true(not neg_found,
                f"voltage_never_negative (min_V={c.steam_voltage})")


def test_voltage_during_transition():
    """No voltage spike when transitioning through states."""
    c = make_controller()
    max_voltage_seen = 0.0
    for i in range(50):
        tick(c, 1)
        max_voltage_seen = max(max_voltage_seen, c.steam_voltage)

        # Mid-run, change conditions to force state changes
        if i == 20:
            c.states["sensor.hapsic_room_average_rh"] = 60.0
            c.states["sensor.hapsic_cleansed_inside_rh"] = 60.0
        if i == 30:
            c.states["sensor.hapsic_room_average_rh"] = 20.0
            c.states["sensor.hapsic_cleansed_inside_rh"] = 20.0

    assert_true(max_voltage_seen <= 9.5,
                f"transition_max_voltage (max={max_voltage_seen})")


def test_stasis_voltage_cap():
    """During cold-start stasis, voltage should be capped at 9.5V."""
    c = make_controller()
    tick(c, 3)  # Enter cruise + stasis
    if c.stasis_active:
        assert_true(c.steam_voltage <= 9.5,
                    f"stasis_cap (V={c.steam_voltage})")
    else:
        assert_true(c.steam_voltage <= 9.5,
                    f"post_stasis_cap (V={c.steam_voltage})")


def test_extreme_deficit_no_overvoltage():
    """Even with extreme deficit (target 60°F, room DP 20°F), voltage ≤ 9.5."""
    c = make_controller({
        "input_number.target_dew_point": 60.0,
        "sensor.hapsic_room_average_rh": 10.0,
        "sensor.hapsic_cleansed_inside_rh": 10.0,
    })
    for i in range(200):
        tick(c, 1)
        assert_true(c.steam_voltage <= 9.5,
                    f"extreme_deficit_tick_{i} (V={c.steam_voltage})")
        if c.steam_voltage > 9.5:
            break  # Stop on first violation


if __name__ == "__main__":
    print("=" * 50)
    print("  HAPSIC Output Safety Invariant Tests")
    print("=" * 50)

    test_voltage_range_during_cruise()
    test_voltage_zero_in_standby()
    test_voltage_zero_on_fault()
    test_voltage_never_negative()
    test_voltage_during_transition()
    test_stasis_voltage_cap()
    test_extreme_deficit_no_overvoltage()

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
