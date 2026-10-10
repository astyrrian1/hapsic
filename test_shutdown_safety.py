"""Failures outside the actuator must never prevent a shutdown request."""

import pytest

from safety_test_support import ControllerRig


def fail(*args, **kwargs):
    raise RuntimeError("injected boundary failure")


@pytest.mark.parametrize("boundary", ["ventilation", "logging", "state_read"])
def test_startup_requests_off_before_fallible_dependencies(monkeypatch, boundary):
    rig = ControllerRig(monkeypatch, initialize=False)
    if boundary == "ventilation":
        rig.fail_services.add("button/press")
    elif boundary == "logging":
        rig.controller.log = fail
    else:
        rig.controller.get_state = fail
    rig.controller.initialize()
    assert rig.commands[0]["service"] == "light/turn_off"
    assert rig.controller.steam_voltage == 0
    assert rig.scheduled


@pytest.mark.parametrize("boundary", ["ventilation", "logging", "state_read", "cached_off"])
def test_fault_requests_off_even_when_auxiliary_dependencies_fail(monkeypatch, boundary):
    rig = ControllerRig(monkeypatch)
    c = rig.controller
    c.fsm_state = "ACTIVE_CRUISE"
    c.steam_voltage = 9.5
    rig.commands.clear()
    if boundary == "ventilation":
        rig.fail_services.add("button/press")
    elif boundary == "logging":
        c.log = fail
    elif boundary == "state_read":
        c.get_state = fail
    c.trigger_fault("injected hazard")
    assert rig.commands[0]["service"] == "light/turn_off"
    assert c.steam_voltage == 0
    assert c.fsm_state == "FAULT"


def test_unexpected_control_exception_requests_off(monkeypatch):
    rig = ControllerRig(monkeypatch)
    rig.controller.steam_voltage = 9.5
    rig.controller.read_and_validate_sensors = fail
    rig.commands.clear()
    rig.advance()
    assert rig.commands[0]["service"] == "light/turn_off"
    assert rig.controller.fsm_state == "FAULT"
    assert rig.controller.steam_voltage == 0


def test_cached_off_does_not_suppress_cancellation_of_requested_on(monkeypatch):
    rig = ControllerRig(monkeypatch)
    rig.commands.clear()
    rig.controller.steam_voltage = 9.5
    rig.controller.write_output()
    rig.controller.steam_voltage = 0
    rig.controller.write_output()
    assert [item["service"] for item in rig.commands] == ["light/turn_on", "light/turn_off"]


def test_startup_grace_cannot_retain_steam_after_sensor_failure(monkeypatch):
    rig = ControllerRig(monkeypatch)
    rig.controller.fsm_state = "ACTIVE_CRUISE"
    rig.controller.steam_voltage = 9.5
    rig.controller.read_and_validate_sensors = lambda: False
    rig.commands.clear()
    rig.advance()
    assert rig.controller.steam_voltage == 0
    assert rig.commands[0]["service"] == "light/turn_off"


def test_failed_startup_off_is_retried_without_enabling_on(monkeypatch):
    rig = ControllerRig(monkeypatch, initialize=False)
    rig.fail_services.add("light/turn_off")
    rig.controller.initialize()
    rig.fail_services.clear()
    rig.commands.clear()
    rig.advance()
    assert [item["service"] for item in rig.commands] == ["light/turn_off"]
    assert rig.controller.steam_voltage == 0


def test_fault_off_and_logging_failures_keep_retrying_only_off(monkeypatch):
    rig = ControllerRig(monkeypatch)
    rig.controller.steam_voltage = 9.5
    rig.controller.log = fail
    rig.fail_services.add("light/turn_off")
    rig.controller.trigger_fault("hazard")
    rig.commands.clear()
    for _ in range(3):
        rig.advance()
    rig.fail_services.clear()
    rig.advance()
    assert [item["service"] for item in rig.commands] == ["light/turn_off"] * 4
    assert rig.controller.steam_voltage == 0


def test_partial_initialization_only_retries_off(monkeypatch):
    rig = ControllerRig(monkeypatch, initialize=False)
    rig.controller.get_state = fail
    rig.controller.initialize()
    rig.commands.clear()
    for _ in range(3):
        rig.advance()
    assert [item["service"] for item in rig.commands] == ["light/turn_off"] * 3
    assert rig.controller.fsm_state == "FAULT"
