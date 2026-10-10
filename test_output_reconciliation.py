"""Real AppDaemon controller: transport uncertainty must stop further ONs."""

import pytest

from safety_test_support import ControllerRig


def test_dropped_on_faults_at_original_deadline_and_retries_off(monkeypatch):
    rig = ControllerRig(monkeypatch)
    c = rig.controller
    c.steam_voltage = 6
    c.write_output()
    for voltage in (7, 8):
        rig.clock.advance(5)
        c.steam_voltage = voltage
        c.write_output()
    rig.clock.advance(5)
    c.master_tick({})
    assert c.fsm_state == "FAULT"
    assert c.steam_voltage == 0
    assert [item for item in rig.commands if item["service"].startswith("light/")][-1]["service"] == "light/turn_off"
    rig.commands.clear()
    c.steam_voltage = 9
    c.write_output()
    assert [command["service"] for command in rig.commands] == ["light/turn_off"]


def test_ha_success_callback_is_not_device_completion(monkeypatch):
    rig = ControllerRig(monkeypatch)
    c = rig.controller
    c.steam_voltage = 6
    c.write_output()
    command = rig.commands[-1]
    assert callable(command.get("callback"))
    command["callback"]({"success": True})
    rig.clock.advance(15)
    c.master_tick({})
    assert c.fsm_state == "FAULT"
    assert c.output_commands.pending_on
    assert not c.output_commands.completed_on


def test_callback_failure_is_drained_before_control_can_enable_output(monkeypatch):
    rig = ControllerRig(monkeypatch)
    c = rig.controller
    c.steam_voltage = 6
    c.write_output()
    command = rig.commands[-1]
    assert callable(command.get("callback"))
    command["callback"]({"success": False})
    calls = []
    c._control_tick = lambda kwargs: calls.append("control")
    c.master_tick({})
    assert not calls
    assert c.fsm_state == "FAULT"
    assert [item for item in rig.commands if item["service"].startswith("light/")][-1]["service"] == "light/turn_off"


def test_reinitialization_cannot_erase_unresolved_command(monkeypatch):
    rig = ControllerRig(monkeypatch)
    c = rig.controller
    c.steam_voltage = 6
    c.write_output()
    old = rig.commands[-1]
    assert callable(old.get("callback"))
    c.initialize()
    old["callback"]({"success": True})
    c._control_tick = lambda kwargs: None
    c.master_tick({})
    assert c.output_commands.uncertain
    assert c.output_commands.pending_on


def test_late_reported_on_invalidates_off_qualification(monkeypatch):
    rig = ControllerRig(monkeypatch)
    c = rig.controller
    c._control_tick = lambda kwargs: None
    rig.clock.advance(1)
    rig.report(c.SHELLY_LIGHT_ENTITY, "off")
    c.master_tick({})
    assert c.output_commands.off_since == rig.clock.monotonic()
    rig.clock.advance(1)
    rig.report(c.SHELLY_LIGHT_ENTITY, "on", brightness=150)
    c.master_tick({})
    assert c.output_commands.off_since is None
    assert c.output_commands.uncertain
    assert c.fsm_state == "FAULT"


def test_polls_do_not_refresh_original_output_report(monkeypatch):
    rig = ControllerRig(monkeypatch)
    c = rig.controller
    c._control_tick = lambda kwargs: None
    rig.clock.advance(1)
    rig.report(c.SHELLY_LIGHT_ENTITY, "off")
    c.master_tick({})
    original = c.output_commands.report_at
    assert original is not None
    rig.clock.advance(61)
    c.master_tick({})
    assert c.output_commands.report_at == original
    assert c.output_commands.off_since is None


@pytest.mark.parametrize("error", [RuntimeError, OSError, AttributeError])
def test_report_read_failure_cannot_prevent_deadline_shutdown(monkeypatch, error):
    rig = ControllerRig(monkeypatch)
    c = rig.controller
    c.steam_voltage = 6
    c.write_output()
    c.get_state = lambda *args, **kwargs: (_ for _ in ()).throw(error("offline"))
    rig.clock.advance(15)
    c.master_tick({})
    assert c.fsm_state == "FAULT"
    assert c.steam_voltage == 0


def test_reinitialization_waits_for_current_bookkeeping_owner(monkeypatch):
    import threading

    rig = ControllerRig(monkeypatch)
    c = rig.controller
    c.steam_voltage = 6
    c.write_output()
    started = threading.Event()
    finished = threading.Event()

    def initialize():
        started.set()
        try:
            c.initialize()
        finally:
            finished.set()

    with c._output_lock:
        worker = threading.Thread(target=initialize)
        worker.start()
        assert started.wait(1)
        completed_while_locked = finished.wait(0.1)
        uncertain_while_locked = c.output_commands.uncertain
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert not completed_while_locked
    assert not uncertain_while_locked
    assert c.output_commands.uncertain


def test_callback_thread_enqueues_without_mutating_locked_bookkeeping(monkeypatch):
    import threading

    rig = ControllerRig(monkeypatch)
    c = rig.controller
    c.steam_voltage = 6
    c.write_output()
    callback = rig.commands[-1]["callback"]
    with c._output_lock:
        worker = threading.Thread(target=callback, args=({"success": False},))
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert not c.output_commands.uncertain
    c.master_tick({})
    assert c.output_commands.uncertain
    assert c.fsm_state == "FAULT"


@pytest.mark.parametrize("brightness", [0, None, True])
def test_reported_on_never_qualifies_as_off_even_with_bad_brightness(monkeypatch, brightness):
    rig = ControllerRig(monkeypatch)
    c = rig.controller
    c._control_tick = lambda kwargs: None
    rig.clock.advance(1)
    rig.report(c.SHELLY_LIGHT_ENTITY, "on", brightness=brightness)
    c.master_tick({})
    assert c.output_commands.off_since is None
    assert c.output_commands.uncertain
    assert c.fsm_state == "FAULT"
