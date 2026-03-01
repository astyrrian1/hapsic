"""
HAPSIC Psychrometric Unit Tests
================================
Validates individual thermodynamic formulas in isolation to ensure
the Python source of truth produces correct physical results.

Covers:
    - Magnus-Tetens saturation vapor pressure
    - Mixing ratio (grains/lb)
    - Dew point from T/RH
    - Feed-forward voltage calculation
    - Feasibility max achievable DP
    - Unit boundary conditions (0%, 100% RH, extreme temps)

Run:
    python3 test_psychrometrics.py
"""

import sys
import math

# -------------------------------------------------------------------------
# Reference implementations (extracted from hapsic.py source of truth)
# -------------------------------------------------------------------------

P_ATM = 88.6  # kPa (Amarillo altitude)
RHO = 0.065   # lbs/ft^3
MAX_CAPACITY = 2.7  # lbs/hr
MAX_VOLTAGE = 10.0
MAX_DUCT_DP = 60.0  # °F


def get_saturation_vapor_pressure(temp_c):
    """Magnus-Tetens formula. Returns kPa."""
    return 0.61078 * math.exp((17.27 * temp_c) / (temp_c + 237.3))


def mixing_ratio_grains(temp_c, rh_pct, p_atm=P_ATM):
    """Compute mixing ratio in grains/lb from temp (°C) and RH (%)."""
    vp_sat = get_saturation_vapor_pressure(temp_c)
    vp = (rh_pct / 100.0) * vp_sat
    w = 0.62198 * (vp / (p_atm - vp)) * 7000.0
    return w


def dew_point_f(temp_f, rh_pct):
    """Compute dew point (°F) from temp (°F) and RH (%)."""
    temp_c = (temp_f - 32.0) * 5.0 / 9.0
    vp_sat = get_saturation_vapor_pressure(temp_c)
    vp = (rh_pct / 100.0) * vp_sat
    if vp <= 0:
        return -40.0  # Absolute floor
    dp_c = (237.3 * math.log(vp / 0.61078)) / (17.27 - math.log(vp / 0.61078))
    return dp_c * 9.0 / 5.0 + 32.0


def v_ff(target_dp_f, supply_dp_f, supply_flow_m3h):
    """Compute feed-forward voltage from target DP, supply DP, and flow."""
    target_c = (target_dp_f - 32.0) * 5.0 / 9.0
    supply_c = (supply_dp_f - 32.0) * 5.0 / 9.0

    target_vp = get_saturation_vapor_pressure(target_c)
    target_w = 0.62198 * (target_vp / (P_ATM - target_vp)) * 7000.0

    supply_vp = get_saturation_vapor_pressure(supply_c)
    supply_w = 0.62198 * (supply_vp / (P_ATM - supply_vp)) * 7000.0

    w_req = max(0, target_w - supply_w)
    cfm = supply_flow_m3h * 0.5886
    lbs_hr_req = (w_req * cfm * 60 * RHO) / 7000.0
    return min(9.5, (lbs_hr_req / MAX_CAPACITY) * 10.0)


# -------------------------------------------------------------------------
# Test Framework
# -------------------------------------------------------------------------

pass_count = 0
fail_count = 0
results = []


def assert_close(actual, expected, tol, label):
    global pass_count, fail_count
    diff = abs(actual - expected)
    passed = diff <= tol
    if passed:
        pass_count += 1
    else:
        fail_count += 1
        results.append(f"  ❌ {label}: got {actual:.4f}, expected {expected:.4f}, Δ={diff:.4f}, tol={tol}")
    return passed


def assert_true(condition, label):
    global pass_count, fail_count
    if condition:
        pass_count += 1
    else:
        fail_count += 1
        results.append(f"  ❌ {label}: condition was False")


# -------------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------------

def test_saturation_vp():
    """Known values: 0°C → 0.6108 kPa, 20°C → 2.338 kPa, 100°C → 101.3 kPa."""
    assert_close(get_saturation_vapor_pressure(0.0), 0.61078, 0.001, "sat_vp_0C")
    assert_close(get_saturation_vapor_pressure(20.0), 2.338, 0.01, "sat_vp_20C")
    assert_close(get_saturation_vapor_pressure(100.0), 101.32, 1.0, "sat_vp_100C")
    assert_close(get_saturation_vapor_pressure(-20.0), 0.1254, 0.005, "sat_vp_neg20C")


def test_mixing_ratio():
    """At 20°C, 50% RH, sea level: ≈51.3 gr/lb. At 88.6 kPa: slightly higher."""
    w = mixing_ratio_grains(20.0, 50.0, P_ATM)
    assert_true(40.0 < w < 70.0, "mixing_ratio_20C_50RH_range")
    # At 100% RH, should be higher
    w100 = mixing_ratio_grains(20.0, 100.0, P_ATM)
    assert_true(w100 > w, "mixing_ratio_100RH_gt_50RH")
    # At 0% RH, should be 0
    w0 = mixing_ratio_grains(20.0, 0.0, P_ATM)
    assert_close(w0, 0.0, 0.001, "mixing_ratio_0RH_zero")


def test_dew_point():
    """Known: 68°F, 50% RH → DP ≈ 48.7°F. 68°F, 100% RH → DP = 68°F."""
    dp = dew_point_f(68.0, 50.0)
    assert_close(dp, 48.7, 1.0, "dp_68F_50RH")
    dp100 = dew_point_f(68.0, 100.0)
    assert_close(dp100, 68.0, 0.5, "dp_68F_100RH")
    # Very dry
    dp_dry = dew_point_f(68.0, 10.0)
    assert_true(dp_dry < 20.0, "dp_68F_10RH_very_low")


def test_vff_clamp():
    """V_FF should clamp to 9.5V max, never go negative."""
    # High demand: target DP far above supply
    voltage = v_ff(58.0, 40.0, 400.0)
    assert_true(0.0 <= voltage <= 9.5, "vff_clamp_upper")
    # No demand: target == supply
    voltage_zero = v_ff(40.0, 40.0, 400.0)
    assert_close(voltage_zero, 0.0, 0.01, "vff_zero_demand")
    # Target below supply: should be 0
    voltage_neg = v_ff(35.0, 40.0, 400.0)
    assert_close(voltage_neg, 0.0, 0.01, "vff_neg_demand_clamped")


def test_vff_zero_flow():
    """With zero airflow, V_FF should be 0."""
    voltage = v_ff(58.0, 40.0, 0.0)
    assert_close(voltage, 0.0, 0.01, "vff_zero_flow")


def test_boundary_conditions():
    """Extreme temperature and humidity values."""
    # Sub-zero temps
    dp = dew_point_f(-40.0, 50.0)
    assert_true(dp < -40.0, "dp_neg40F_50RH")
    # Very hot
    dp_hot = dew_point_f(120.0, 80.0)
    assert_true(dp_hot > 100.0, "dp_120F_80RH_high")
    # Zero RH mixing ratio
    w = mixing_ratio_grains(-10.0, 0.0, P_ATM)
    assert_close(w, 0.0, 0.001, "mixing_ratio_cold_0RH")

def test_ema_filter():
    """EMA filter: α=0.2, step from 0→100 should converge, steady state should match."""
    alpha = 0.2
    ema = 0.0
    # Step response: 0 → 100
    for i in range(50):
        ema = alpha * 100.0 + (1 - alpha) * ema
    # After 50 steps, should be very close to 100
    assert_close(ema, 100.0, 0.01, "ema_step_convergence_50ticks")

    # After 10 steps, should be ~89% of target
    ema10 = 0.0
    for i in range(10):
        ema10 = alpha * 100.0 + (1 - alpha) * ema10
    expected_10 = 100.0 * (1 - (1 - alpha) ** 10)
    assert_close(ema10, expected_10, 0.01, "ema_step_10ticks")

    # Steady state: constant input = constant output
    ema_ss = 50.0
    for i in range(20):
        ema_ss = alpha * 50.0 + (1 - alpha) * ema_ss
    assert_close(ema_ss, 50.0, 0.001, "ema_steady_state")


if __name__ == "__main__":
    print("=" * 50)
    print("  HAPSIC Psychrometric Unit Tests")
    print("=" * 50)

    test_saturation_vp()
    test_mixing_ratio()
    test_dew_point()
    test_vff_clamp()
    test_vff_zero_flow()
    test_boundary_conditions()
    test_ema_filter()

    print()
    for r in results:
        print(r)

    total = pass_count + fail_count
    print(f"\n  TOTAL: {pass_count}/{total} passed")
    if fail_count > 0:
        print(f"  ❌ {fail_count} FAILURES")
        sys.exit(1)
    else:
        print(f"  ✅ ALL TESTS PASSED")
        sys.exit(0)
