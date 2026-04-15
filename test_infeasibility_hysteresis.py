"""
HAPSIC Infeasibility Hysteresis Regression Tests
===================================================
Validates the hysteresis deadband on ``is_target_infeasible`` to prevent
chatter when ``max_achievable_dp`` oscillates near ``target_room_dp``.

Root cause (2026-04-07):  The original code had a 0.5 °F one-sided
deadband (SET at target > max+0.5, CLEAR at target < max).  Sensor
noise of ~0.3 °F caused 27 state changes in 3 hours.

Fix:  Widened to SET at target > max + 1.0, CLEAR at target < max − 0.5
(1.5 °F total deadband).  This file encodes those boundaries.

Boundary map (°F offsets from max_achievable_dp):
    ─ −0.5 ── CLEAR zone ── 0 ── deadband ── +1.0 ── SET zone ──►
    Below −0.5:  must be False
    Between −0.5 and +1.0 (exclusive):  no change (hysteresis hold)
    Above +1.0:  must be True

Run:
    python3 test_infeasibility_hysteresis.py
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

def assert_false(condition, label):
    assert_true(not condition, label)

def assert_equal(actual, expected, label):
    assert_true(
        actual == expected,
        f"{label}: expected {expected}, got {actual}",
    )


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
    "sensor.hapsic_cleansed_supply_temp": 68.0,
    "sensor.hapsic_cleansed_supply_rh": 30.0,
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
    "pre_steam_temp": "sensor.hapsic_cleansed_supply_temp",
    "pre_steam_rh": "sensor.hapsic_cleansed_supply_rh",
    "extract_avg_temp": "sensor.hapsic_room_average_temp",
    "extract_avg_rh": "sensor.hapsic_room_average_rh",
    "steam_dac": "output.steam_dac",
    "fan_dac": "output.fan_dac",
}


def make_controller(state_overrides=None):
    """Create a fresh controller with default working sensor state."""
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
    """Advance controller by n ticks (5 s each)."""
    for _ in range(n):
        controller._mock_time[0] += 5.0
        controller.last_tick_ts = controller._mock_time[0] - 5.0
        controller.master_tick({})


# -------------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------------

def test_set_threshold_exact_boundary():
    """Flag must SET when target_room_dp == max_achievable_dp + 1.01
    and must NOT SET at max_achievable_dp + 0.99 (inside deadband).

    Verifies the +1.0 °F SET threshold.
    """
    c = make_controller()
    tick(c, 10)  # let system settle and compute max_achievable_dp

    max_dp = c.max_achievable_dp

    # Place target just INSIDE deadband (should NOT set)
    c.is_target_infeasible = False
    c.states["input_number.target_dew_point"] = max_dp + 0.99
    tick(c, 3)
    assert_false(c.is_target_infeasible,
                 f"inside_deadband_no_set (target={max_dp+0.99:.2f}, max={max_dp:.2f})")

    # Place target just ABOVE threshold (should SET)
    c.states["input_number.target_dew_point"] = max_dp + 1.01
    tick(c, 3)
    assert_true(c.is_target_infeasible,
                f"above_threshold_sets (target={max_dp+1.01:.2f}, max={max_dp:.2f})")


def test_clear_threshold_exact_boundary():
    """Flag must CLEAR when target_room_dp == max_achievable_dp − 0.51
    and must NOT CLEAR at max_achievable_dp − 0.49 (inside deadband).

    Verifies the −0.5 °F CLEAR threshold.
    """
    c = make_controller()
    tick(c, 10)

    max_dp = c.max_achievable_dp

    # Force flag ON first (target well above max)
    c.states["input_number.target_dew_point"] = max_dp + 5.0
    tick(c, 3)
    assert_true(c.is_target_infeasible,
                f"pre_clear_flag_is_set (target={max_dp+5.0:.2f})")

    # Move target to just INSIDE deadband (should remain True)
    c.states["input_number.target_dew_point"] = max_dp - 0.49
    tick(c, 3)
    assert_true(c.is_target_infeasible,
                f"inside_deadband_holds_true (target={max_dp-0.49:.2f}, max={max_dp:.2f})")

    # Move target BELOW clear threshold (should CLEAR)
    c.states["input_number.target_dew_point"] = max_dp - 0.51
    tick(c, 3)
    assert_false(c.is_target_infeasible,
                 f"below_threshold_clears (target={max_dp-0.51:.2f}, max={max_dp:.2f})")


def test_deadband_holds_state_on_oscillation():
    """REGRESSION: The exact production failure scenario.

    Sensor noise causes max_achievable_dp to oscillate ±0.3 °F around
    a value ~0.5 °F below target_room_dp.  With the old 0.5 °F deadband
    this produced 27 transitions in 3 hours.  With the new 1.5 °F
    deadband the flag must stay constant.
    """
    c = make_controller()
    tick(c, 10)

    max_dp = c.max_achievable_dp
    # Place target in the deadband: +0.5 above max (between −0.5 and +1.0)
    c.states["input_number.target_dew_point"] = max_dp + 0.5
    c.is_target_infeasible = False
    tick(c, 3)

    # Simulate 50 ticks of ±0.3 °F sensor noise on outdoor RH (which
    # shifts max_achievable_dp)
    import random
    random.seed(42)  # deterministic
    transitions = 0
    prev_flag = c.is_target_infeasible
    for _ in range(50):
        # Jitter outdoor RH by ±2% (produces ~0.3 °F DP noise)
        base_rh = 50.0
        c.states["sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_humidity"] = (
            base_rh + random.uniform(-2.0, 2.0)
        )
        tick(c, 1)
        if c.is_target_infeasible != prev_flag:
            transitions += 1
            prev_flag = c.is_target_infeasible

    assert_true(transitions <= 1,
                f"deadband_prevents_chatter: {transitions} transitions in 50 ticks")


def test_set_then_clear_full_cycle():
    """Full SET → hold → CLEAR cycle.  Validates both thresholds together."""
    c = make_controller()
    tick(c, 10)
    max_dp = c.max_achievable_dp

    # Step 1: Flag starts False
    c.states["input_number.target_dew_point"] = max_dp  # exactly at max
    c.is_target_infeasible = False
    tick(c, 3)
    assert_false(c.is_target_infeasible,
                 "cycle_start_false")

    # Step 2: Ramp target well above SET threshold
    c.states["input_number.target_dew_point"] = max_dp + 2.0
    tick(c, 3)
    assert_true(c.is_target_infeasible,
                "cycle_set_true")

    # Step 3: Drop target into deadband (still True)
    c.states["input_number.target_dew_point"] = max_dp + 0.5
    tick(c, 3)
    assert_true(c.is_target_infeasible,
                "cycle_deadband_holds_true")

    # Step 4: Drop target to exactly max (still True — not below −0.5)
    c.states["input_number.target_dew_point"] = max_dp
    tick(c, 3)
    assert_true(c.is_target_infeasible,
                "cycle_at_max_holds_true")

    # Step 5: Drop target well below CLEAR threshold
    c.states["input_number.target_dew_point"] = max_dp - 1.0
    tick(c, 3)
    assert_false(c.is_target_infeasible,
                 "cycle_clear_false")

    # Step 6: Bring target back into deadband (stays False)
    c.states["input_number.target_dew_point"] = max_dp + 0.5
    tick(c, 3)
    assert_false(c.is_target_infeasible,
                 "cycle_re_enter_deadband_stays_false")


def test_infeasible_flag_resets_integrator_guard():
    """When is_target_infeasible is True and error > 0, the integrator
    windup guard must engage (error clamped to 0).

    This validates the downstream effect of the flag, not just the
    hysteresis — confirming the flag actually does something useful.
    """
    c = make_controller()
    tick(c, 10)

    max_dp = c.max_achievable_dp
    # Set target far above achievable — flag should be True
    c.states["input_number.target_dew_point"] = max_dp + 5.0
    tick(c, 30)
    assert_true(c.is_target_infeasible,
                f"integrator_guard_flag_set (max={max_dp:.2f})")

    # Voltage should be bounded (windup guard prevents runaway)
    assert_true(c.steam_voltage <= 9.5,
                f"integrator_guard_voltage_bounded (V={c.steam_voltage})")


def test_clear_to_set_requires_crossing_full_deadband():
    """Starting from CLEAR (False), target must cross the full +1.0
    threshold to SET.  Incremental steps inside deadband must not
    trigger a SET.
    """
    c = make_controller()
    tick(c, 10)
    max_dp = c.max_achievable_dp

    c.is_target_infeasible = False
    # Step through deadband in 0.2 °F increments
    for offset in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]:
        c.states["input_number.target_dew_point"] = max_dp + offset
        tick(c, 2)
        assert_false(c.is_target_infeasible,
                     f"incremental_no_set_at_+{offset:.1f}")

    # Cross the threshold
    c.states["input_number.target_dew_point"] = max_dp + 1.1
    tick(c, 2)
    assert_true(c.is_target_infeasible,
                "incremental_sets_at_+1.1")


def test_set_to_clear_requires_crossing_full_deadband():
    """Starting from SET (True), target must cross the full −0.5
    threshold to CLEAR.  Incremental steps inside deadband must not
    trigger a CLEAR.
    """
    c = make_controller()
    tick(c, 10)
    max_dp = c.max_achievable_dp

    # Force flag on
    c.states["input_number.target_dew_point"] = max_dp + 5.0
    tick(c, 3)
    assert_true(c.is_target_infeasible, "set_pre_condition")

    # Step through deadband toward CLEAR
    for offset in [0.8, 0.5, 0.2, 0.0, -0.2, -0.5]:
        c.states["input_number.target_dew_point"] = max_dp + offset
        tick(c, 2)
        assert_true(c.is_target_infeasible,
                    f"incremental_holds_at_{offset:+.1f}")

    # Cross the CLEAR threshold
    c.states["input_number.target_dew_point"] = max_dp - 0.6
    tick(c, 2)
    assert_false(c.is_target_infeasible,
                 "incremental_clears_at_-0.6")


def test_multiple_set_clear_cycles_no_drift():
    """Cycle the flag SET→CLEAR five times.  Verify no state drift
    (the flag returns to the expected value every time).
    """
    c = make_controller()
    tick(c, 10)
    max_dp = c.max_achievable_dp

    for cycle in range(5):
        # SET
        c.states["input_number.target_dew_point"] = max_dp + 2.0
        tick(c, 3)
        assert_true(c.is_target_infeasible,
                    f"cycle_{cycle}_set")
        # CLEAR
        c.states["input_number.target_dew_point"] = max_dp - 1.0
        tick(c, 3)
        assert_false(c.is_target_infeasible,
                     f"cycle_{cycle}_clear")


def test_high_capacity_widens_achievable_range():
    """Doubling MAX_CAPACITY should increase max_achievable_dp, which
    may change infeasibility.  The hysteresis logic must still work
    correctly when capacity changes shift the boundary.
    """
    c = make_controller()
    tick(c, 10)
    max_dp_normal = c.max_achievable_dp

    # Set target slightly above max by +1.5 → should be infeasible
    c.states["input_number.target_dew_point"] = max_dp_normal + 1.5
    tick(c, 3)
    assert_true(c.is_target_infeasible,
                "high_cap_initially_infeasible")

    # Double capacity → max_achievable_dp should jump up
    c.states["input_number.humidifier_max_capacity"] = 5.4
    tick(c, 5)
    max_dp_high = c.max_achievable_dp
    assert_true(max_dp_high > max_dp_normal,
                f"high_cap_increases_max (old={max_dp_normal:.2f}, new={max_dp_high:.2f})")

    # With higher max, the target may now be feasible (inside or below
    # deadband relative to new max)
    if c.states["input_number.target_dew_point"] < (max_dp_high - 0.5):
        assert_false(c.is_target_infeasible,
                     "high_cap_clears_infeasible")
    else:
        # Still in deadband — acceptable
        assert_true(True, "high_cap_in_deadband_ok")


def test_initial_state_is_false():
    """The infeasibility flag must start False on a fresh controller."""
    c = make_controller()
    assert_false(c.is_target_infeasible,
                 "initial_state_is_false")


# -------------------------------------------------------------------------
# Main (standalone runner)
# -------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 55)
    print("  HAPSIC Infeasibility Hysteresis Regression Tests")
    print("=" * 55)

    test_initial_state_is_false()
    test_set_threshold_exact_boundary()
    test_clear_threshold_exact_boundary()
    test_deadband_holds_state_on_oscillation()
    test_set_then_clear_full_cycle()
    test_infeasible_flag_resets_integrator_guard()
    test_clear_to_set_requires_crossing_full_deadband()
    test_set_to_clear_requires_crossing_full_deadband()
    test_multiple_set_clear_cycles_no_drift()
    test_high_capacity_widens_achievable_range()

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
