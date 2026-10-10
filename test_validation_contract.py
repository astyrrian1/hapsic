"""Prove the repository's assertion and runner paths cannot report false success."""

import importlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent


@pytest.mark.parametrize("module,helper,args", [
    ("test_unit_conversions", "assert_close", (1, 2, 0, "injected mismatch")),
    ("test_unit_conversions", "assert_true", (False, "injected mismatch")),
    ("test_psychrometrics", "assert_close", (1, 2, 0, "injected mismatch")),
    ("test_psychrometrics", "assert_true", (False, "injected mismatch")),
    ("test_sensor_fallback", "assert_close", (1, 2, 0, "injected mismatch")),
    ("test_sensor_fallback", "assert_true", (False, "injected mismatch")),
    ("test_output_safety", "assert_true", (False, "injected mismatch")),
    ("test_fsm_transitions", "assert_true", (False, "injected mismatch")),
    ("test_fsm_transitions", "assert_state", (SimpleNamespace(fsm_state="FAULT"), "STANDBY", "mismatch")),
    ("test_fsm_transitions", "assert_not_state", (SimpleNamespace(fsm_state="FAULT"), "FAULT", "mismatch")),
    ("test_infeasibility_hysteresis", "assert_true", (False, "injected mismatch")),
    ("test_offline_parity", "assert_condition", (False, "injected mismatch")),
])
def test_assertion_helpers_raise(module, helper, args):
    suite = importlib.import_module(module)
    saved = suite.pass_count, suite.fail_count, list(suite.results)
    try:
        with pytest.raises(AssertionError):
            getattr(suite, helper)(*args)
    finally:
        suite.pass_count, suite.fail_count, suite.results = saved


@pytest.mark.parametrize("runner", ["script", "pytest"])
def test_wrong_constant_fails_both_runners(tmp_path, runner):
    source = (ROOT / "test_unit_conversions.py").read_text()
    assert "PY_P_ATM = 88.6" in source
    mutant = tmp_path / "test_mutated_units.py"
    mutant.write_text(source.replace("PY_P_ATM = 88.6", "PY_P_ATM = 1.0"))
    command = [sys.executable]
    if runner == "pytest":
        command += ["-m", "pytest", "-q", "-p", "no:cacheprovider"]
    result = subprocess.run(command + [str(mutant)], capture_output=True, text=True, timeout=30)
    assert result.returncode != 0, result.stdout
    assert "P_ATM_parity" in result.stdout + result.stderr


def test_telemetry_script_executes_passive_import_test(tmp_path):
    source = (ROOT / "test_telemetry_integrity.py").read_text()
    assert "def test_passive_import_advisory_math():" in source
    mutant = tmp_path / "test_mutated_telemetry.py"
    mutant.write_text(source.replace(
        "def test_passive_import_advisory_math():",
        'def test_passive_import_advisory_math():\n    raise AssertionError("injected passive failure")',
    ))
    import os
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    result = subprocess.run([sys.executable, str(mutant)], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0, result.stdout
    assert "injected passive failure" in result.stderr


def test_test_imports_preserve_shared_appdaemon_adapter():
    import conftest
    for name in ("test_sensor_fallback", "test_output_safety", "test_fsm_transitions",
                 "test_infeasibility_hysteresis", "test_telemetry_integrity"):
        importlib.reload(importlib.import_module(name))
    assert sys.modules["appdaemon.plugins.hass.hassapi"].Hass is conftest._MockHass


@pytest.mark.parametrize("name,state,voltage", [
    ("test_no_airflow_fault", "ACTIVE_CRUISE", 9.5),
    ("test_cold_start_stasis", "ACTIVE_CRUISE", 0.0),
    ("test_voltage_zero_in_standby", "STANDBY", 9.5),
])
def test_inline_fsm_failures_raise(monkeypatch, name, state, voltage):
    import test_fsm_transitions as suite
    controller = SimpleNamespace(states={}, fsm_state=state, steam_voltage=voltage,
                                 stasis_active=False, boil_achieved=False)
    monkeypatch.setattr(suite, "make_controller", lambda: controller)
    monkeypatch.setattr(suite, "tick", lambda *args: None)
    saved = suite.pass_count, suite.fail_count, list(suite.results)
    try:
        with pytest.raises(AssertionError):
            getattr(suite, name)()
    finally:
        suite.pass_count, suite.fail_count, suite.results = saved


@pytest.mark.parametrize("suite", [
    "sensor_fallback", "output_safety", "fsm_transitions", "infeasibility_hysteresis", "telemetry_integrity",
])
def test_consolidated_suites_run_standalone(suite):
    result = subprocess.run([sys.executable, str(ROOT / f"test_{suite}.py")], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
