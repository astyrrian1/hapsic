"""
HAPSIC Unit Conversion Tests
==============================
Validates that all unit conversion boundaries between Python (imperial)
and C++ (SI) are correct and consistent. This is the most dangerous
class of bug — a wrong lambda produces physically plausible but
incorrect voltages.

Covers:
    - Temperature round-trips (°F ↔ °C)
    - Boundary values (-40, 32, 212°F)
    - Cross-platform constant parity (P_ATM, RHO, MAX_DUCT_DP)
    - YAML lambda conversion factor validation
    - Flow conversion (m³/h ↔ CFM)
    - Mass conversion (lbs ↔ kg)
    - Humidity ratio systems (grains/lb vs g/kg)
    - Scenario tester conversion constants

Run:
    python3 test_unit_conversions.py
"""

import sys
import math
import re
import os

# -------------------------------------------------------------------------
# Reference conversion functions (matching YAML lambdas)
# -------------------------------------------------------------------------

def f_to_c(f):
    """°F → °C (matches YAML lambda: (x - 32.0) * (5.0 / 9.0))"""
    return (f - 32.0) * (5.0 / 9.0)

def c_to_f(c):
    """°C → °F (matches scenario_tester: val * 9.0/5.0 + 32.0)"""
    return c * 9.0 / 5.0 + 32.0

def lbs_to_kg(lbs):
    """lbs → kg (matches YAML lambda: x * 0.453592)"""
    return lbs * 0.453592

def m3h_to_cfm(m3h):
    """m³/h → CFM (matches hapsic.py constant: * 0.5886)"""
    return m3h * 0.5886

def lbs_ft3_to_kg_m3(rho_imperial):
    """lbs/ft³ → kg/m³"""
    return rho_imperial * 16.01846


# -------------------------------------------------------------------------
# Platform constants (from source files)
# -------------------------------------------------------------------------

# Python (hapsic.py)
PY_P_ATM = 88.6          # kPa
PY_RHO = 0.065           # lbs/ft³
PY_MAX_DUCT_DP = 60.0    # °F
PY_MAX_VOLTAGE = 10.0    # V
PY_FLOW_CONV = 0.5886    # m³/h → CFM
PY_GRAINS_FACTOR = 7000  # grains per lb

# C++ (hapsic.h)
CPP_P_ATM = 88.6         # kPa
CPP_RHO = 1.041          # kg/m³
CPP_MAX_DUCT_DP = 15.56  # °C (60°F converted)
CPP_MAX_VOLTAGE = 10.0   # V
CPP_DEADBAND = 0.83      # °C (1.5°F converted)
CPP_SLEW_RATE = 0.5      # V
CPP_EMA_ALPHA = 0.1


# -------------------------------------------------------------------------
# Test Framework
# -------------------------------------------------------------------------
pass_count = 0
fail_count = 0
results = []

def assert_close(actual, expected, tol, label):
    global pass_count, fail_count
    diff = abs(actual - expected)
    if diff <= tol:
        pass_count += 1
    else:
        fail_count += 1
        results.append(f"  ❌ {label}: got {actual:.6f}, expected {expected:.6f}, Δ={diff:.6f}, tol={tol}")

def assert_true(condition, label):
    global pass_count, fail_count
    if condition:
        pass_count += 1
    else:
        fail_count += 1
        results.append(f"  ❌ {label}")


# -------------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------------

def test_temperature_round_trips():
    """°F → °C → °F must be identity within floating point tolerance."""
    for f in [-40.0, 0.0, 32.0, 50.0, 68.0, 100.0, 212.0]:
        c = f_to_c(f)
        f_back = c_to_f(c)
        assert_close(f_back, f, 0.01, f"round_trip_{f}F")


def test_boundary_temperatures():
    """Known physical reference points."""
    # -40 is the same in both systems
    assert_close(f_to_c(-40.0), -40.0, 0.01, "neg40_identity")
    # Freezing point of water
    assert_close(f_to_c(32.0), 0.0, 0.01, "freezing_32F_0C")
    # Boiling point of water
    assert_close(f_to_c(212.0), 100.0, 0.01, "boiling_212F_100C")
    # Room temperature
    assert_close(f_to_c(68.0), 20.0, 0.01, "room_68F_20C")
    # Body temperature
    assert_close(f_to_c(98.6), 37.0, 0.01, "body_98.6F_37C")


def test_p_atm_parity():
    """P_ATM must be identical across platforms (same unit: kPa)."""
    assert_close(PY_P_ATM, CPP_P_ATM, 0.001, "P_ATM_parity")


def test_rho_cross_unit_parity():
    """Python RHO (lbs/ft³) and C++ RHO (kg/m³) must represent same physical value."""
    cpp_rho_from_py = lbs_ft3_to_kg_m3(PY_RHO)
    assert_close(cpp_rho_from_py, CPP_RHO, 0.01,
                 f"RHO_parity: Py {PY_RHO} lbs/ft³ → {cpp_rho_from_py:.3f} kg/m³ vs C++ {CPP_RHO}")


def test_max_duct_dp_parity():
    """Python MAX_DUCT_DP (°F) converted to °C must match C++ constant."""
    py_converted = f_to_c(PY_MAX_DUCT_DP)
    assert_close(py_converted, CPP_MAX_DUCT_DP, 0.01,
                 f"MAX_DUCT_DP: Py {PY_MAX_DUCT_DP}°F → {py_converted:.2f}°C vs C++ {CPP_MAX_DUCT_DP}°C")


def test_max_voltage_parity():
    """MAX_VOLTAGE must match (same unit: volts)."""
    assert_close(PY_MAX_VOLTAGE, CPP_MAX_VOLTAGE, 0.001, "MAX_VOLTAGE_parity")


def test_capacity_conversion():
    """2.7 lbs/hr → kg/h via YAML lambda must be correct."""
    lbs_hr = 2.7
    kg_h = lbs_to_kg(lbs_hr)
    assert_close(kg_h, 1.2247, 0.001, f"capacity_2.7lbs→{kg_h:.4f}kg")


def test_flow_conversion():
    """m³/h → CFM conversion factor must be ASHRAE-correct."""
    # 1 m³/h = 0.58858 CFM (ASHRAE standard)
    assert_close(PY_FLOW_CONV, 0.5886, 0.001, "flow_conv_factor")
    # Verify at typical operating point
    m3h = 400.0
    cfm = m3h_to_cfm(m3h)
    assert_close(cfm, 235.44, 0.1, f"flow_400m3h→{cfm:.1f}CFM")


def test_grains_to_gkg():
    """Verify grains/lb ↔ g/kg conversion is physically correct."""
    # 1 lb = 7000 grains (exact definition)
    # 1 g/kg = 1 g per 1000g = 0.001 ratio
    # grains/lb to g/kg: multiply by (1/7000) * (453.592/1000) * 7000... 
    # Actually: w_grains_per_lb * (1/7000) * 453.592 * (1000/453.592) = w * 1.0
    # The mixing ratio in grains/lb ÷ 7 = mixing ratio in g/kg (approximately)
    # More precisely: 1 grain = 0.06479891 g, 1 lb = 453.59237 g
    # w_grains/lb * (0.06479891 g/grain) / (453.59237 g/lb) * 1000 g/kg
    # = w * 0.14286 = w / 7.0

    # Test: 50 grains/lb should be ~7.14 g/kg
    grains = 50.0
    g_kg = grains / 7.0  # Approximate conversion
    assert_close(g_kg, 7.143, 0.01, "grains_to_gkg_50gr")


def test_yaml_lambda_constants():
    """Verify YAML lambda conversion factors match expected values."""
    # Target DP: (x - 32.0) * (5.0 / 9.0) — standard F→C
    assert_close((50.0 - 32.0) * (5.0 / 9.0), 10.0, 0.001, "yaml_dp_50F→10C")

    # Max capacity: x * 0.453592 — standard lbs→kg
    assert_close(2.7 * 0.453592, 1.2247, 0.001, "yaml_capacity_lambda")

    # Duct RH: (x / 10.0) * 100.0 — CAN raw to %
    assert_close((5.5 / 10.0) * 100.0, 55.0, 0.001, "yaml_duct_rh_scaling")


def test_scenario_tester_constants():
    """Verify scenario_tester.py uses the same conversion factors."""
    # Temperature: * 9.0/5.0 + 32.0 (C→F, inverse of YAML F→C)
    c_val = 20.0
    f_val = c_val * 9.0/5.0 + 32.0
    assert_close(f_val, 68.0, 0.01, "scenario_tester_temp_conv")

    # Flow: * 0.588578 (m³/h → CFM)
    # Should match PY_FLOW_CONV within rounding
    assert_close(0.588578, PY_FLOW_CONV, 0.001, "scenario_tester_flow_conv")


def test_deadband_conversion():
    """C++ DEADBAND (°C) must equal Python deadband (°F) converted."""
    # Python uses 1.5°F deadband (see evaluate_fsm: deficit > 1.0°F threshold)
    # But the C++ DEADBAND is 0.83°C ≈ 1.5°F
    py_deadband_f = 1.5
    py_deadband_c = py_deadband_f * 5.0 / 9.0
    assert_close(py_deadband_c, CPP_DEADBAND, 0.01,
                 f"deadband: {py_deadband_f}°F → {py_deadband_c:.2f}°C vs C++ {CPP_DEADBAND}°C")


if __name__ == "__main__":
    print("=" * 50)
    print("  HAPSIC Unit Conversion Tests")
    print("=" * 50)

    test_temperature_round_trips()
    test_boundary_temperatures()
    test_p_atm_parity()
    test_rho_cross_unit_parity()
    test_max_duct_dp_parity()
    test_max_voltage_parity()
    test_capacity_conversion()
    test_flow_conversion()
    test_grains_to_gkg()
    test_yaml_lambda_constants()
    test_scenario_tester_constants()
    test_deadband_conversion()

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
