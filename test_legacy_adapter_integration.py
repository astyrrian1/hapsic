"""Exercise AppDaemon scheduling and MQTT boundaries beside the real controller."""

import copy
import json

import hapsic_controller as controller
import pytest

from test_output_safety import ARGS, BASE_STATES


class _Clock:
    utc = 1_700_000_000.0
    mono = 1000.0

    def advance(self):
        self.utc += 5
        self.mono += 5


class _AppDaemonHarness:
    """Only the host APIs are faked; app callbacks execute their actual code."""

    def __init__(self, clock):
        self.clock = clock
        self.scheduled = []
        self.listeners = []
        self.actuators = []
        self.control_packets = []
        self.diagnostic_packets = []
        self.publish_failures = 0
        self.fail_publish = False
        self.logs = []

    def schedule(self, callback, start, interval):
        assert start == "now" and interval == 5
        self.scheduled.append(callback)
        return len(self.scheduled)

    def listen(self, callback, event, **filters):
        assert event == "MQTT_MESSAGE"
        self.listeners.append((callback, filters))

    def deliver(self, topic, payload):
        data = dict(topic=topic, payload=json.dumps(payload), retain=False)
        for callback, filters in self.listeners:
            if filters.get("topic", topic) == topic:
                callback("MQTT_MESSAGE", data, {})

    def service(self, app_kind, service, **kwargs):
        if service == "mqtt/subscribe":
            return
        if service == "mqtt/publish":
            payload = json.loads(kwargs["payload"])
            if app_kind == "diagnostic":
                if self.fail_publish:
                    self.publish_failures += 1
                    raise RuntimeError("injected diagnostic MQTT outage")
                self.diagnostic_packets.append((kwargs["topic"], payload))
            else:
                self.control_packets.append(payload)
                self.deliver(kwargs["topic"], payload)
            return
        assert app_kind == "control", "The diagnostic app must never invoke actuator/helper services"
        self.actuators.append((service, copy.deepcopy(kwargs)))

    def attach_controller(self):
        app = controller.HapsicController()
        app.states = dict(BASE_STATES)
        app.states["input_text.hapsic_boiler_curve"] = json.dumps(
            dict(v=2, curve=[0, 0, 0, 0], counts=[0, 0, 0, 0])
        )
        app.args = dict(ARGS)
        app.run_every = self.schedule
        app.call_service = lambda service, **kwargs: self.service("control", service, **kwargs)
        app.turn_on = lambda entity_id, **kwargs: self.actuators.append(
            ("turn_on", dict(entity_id=entity_id, **kwargs)))
        app.turn_off = lambda entity_id, **kwargs: self.actuators.append(
            ("turn_off", dict(entity_id=entity_id, **kwargs)))
        app.log = lambda message, **kwargs: self.logs.append(message)
        app.initialize()
        return app

    def attach_diagnostics(self, path):
        app = controller.HapsicDiagnostics()
        app.args = dict(data_path=str(path), clock_verified=True)
        app.run_every = self.schedule
        app.listen_event = self.listen
        app.call_service = lambda service, **kwargs: self.service("diagnostic", service, **kwargs)
        app.log = lambda message, **kwargs: self.logs.append(message)
        raw = {
            "upstream_t": (20, "°C"), "upstream_rh": (30, "%"),
            "downstream_t": (20, "°C"), "downstream_rh": (35, "%"),
            "flow": (400, "m³/h"), "pressure": (88600, "Pa"),
        }
        entities = {entity: raw[key] for key, entity in app.SOURCES.items()}

        def get_state(entity, *, attribute):
            assert attribute == "all"
            value, unit = entities[entity]
            return dict(state=str(value), last_reported=self.clock.utc,
                        attributes=dict(unit_of_measurement=unit))

        app.get_state = get_state
        app.initialize()
        return app


def _owned_state(app):
    fields = (
        "fsm_state", "fault_reason", "steam_voltage", "integrator_a", "integrator_b",
        "chi_ema", "chi_instant", "boiler_curve", "boiler_curve_counts", "stasis_active",
        "stasis_timer", "boil_achieved", "zero_volt_ticks", "upward_rate_ticks",
        "downward_rate_ticks", "room_dp_buffer", "duct_dp_buffer", "sensor_value_cache",
    )
    return {field: copy.deepcopy(getattr(app, field)) for field in fields}


def test_diagnostic_mqtt_configuration_preserves_home_assistant_namespace(tmp_path):
    clock = _Clock()
    harness = _AppDaemonHarness(clock)
    app = controller.HapsicDiagnostics()
    app.namespace = "default"
    app.args = dict(data_path=str(tmp_path / "diagnostics.json"), mqtt_namespace="mqtt")
    app.run_every = harness.schedule
    app.listen_event = harness.listen
    app.log = lambda *args, **kwargs: None
    app.call_service = lambda service, **kwargs: harness.service("diagnostic", service, **kwargs)

    def get_state(entity, *, attribute):
        assert attribute == "all"
        # AppDaemon resolves omitted namespace arguments from the app namespace.
        if app.namespace != "default":
            return None
        return dict(state="20", last_reported=clock.utc,
                    attributes=dict(unit_of_measurement="°C"))

    app.get_state = get_state
    app.initialize()
    try:
        assert set(app._snapshot()) == set(app.SOURCES)
        assert app.namespace == "default"
    finally:
        app.terminate()


@pytest.mark.parametrize("diagnostics_first", [False, True], ids=["control-first", "diagnostics-first"])
def test_scheduled_diagnostic_adapter_failures_preserve_control_and_learning(
    monkeypatch, tmp_path, diagnostics_first
):
    """Real adapter events and worker failures cannot alter the paired controller."""
    clock = _Clock()
    monkeypatch.setattr(controller.time, "time", lambda: clock.utc)
    monkeypatch.setattr(controller.time, "monotonic", lambda: clock.mono)
    monkeypatch.setattr(controller.time, "monotonic_ns", lambda: int(clock.mono * 1e9))
    baseline, combined = _AppDaemonHarness(clock), _AppDaemonHarness(clock)
    plain, observed = baseline.attach_controller(), combined.attach_controller()
    diagnostic = combined.attach_diagnostics(tmp_path / "diagnostics.json")
    assert [callback.__name__ for callback in baseline.scheduled] == ["master_tick"]
    assert [callback.__name__ for callback in combined.scheduled] == ["master_tick", "diagnostic_tick"]
    storage_attempts = []
    original_save = diagnostic.store.save

    def save(state):
        storage_attempts.append(clock.mono)
        if clock.mono >= 1400:
            raise OSError("injected diagnostic storage failure")
        original_save(state)

    monkeypatch.setattr(diagnostic.store, "save", save)
    saw_queue_full = saw_storage_failure = saw_positive_command = False
    saw_fault = saw_command_event = False
    try:
        for step in range(420):
            clock.advance()
            # Both control instances receive the same live state transitions.
            for app in (plain, observed):
                app.states[ARGS["supply_flow"]] = 0 if 300 <= step < 310 else 400
            combined.fail_publish = 60 <= step < 70
            if step == 40:
                for request in range(17):
                    combined.deliver(diagnostic.topic + "/command", dict(
                        schema_version=1, producer_id=diagnostic.producer,
                        request_id=f"saturating-{request}", issued_at=clock.utc,
                        action="unsupported-action",
                    ))
                saw_queue_full = diagnostic.commands.full()
                assert diagnostic.observer.reason == "MESSAGE_REJECTED"
            baseline.scheduled[0]({})
            callbacks = reversed(combined.scheduled) if diagnostics_first else combined.scheduled
            for callback in callbacks:
                callback({})
            # Await only host I/O completion; the app consumes its result on its next scheduled tick.
            if diagnostic.pending_save:
                try:
                    diagnostic.pending_save[0].result(timeout=2)
                except OSError:
                    pass
            saw_storage_failure |= diagnostic.storage_error == "OSError"
            saw_positive_command |= observed.steam_voltage > 0
            saw_fault |= observed.fsm_state == "FAULT"
            saw_command_event |= diagnostic.control is not None
            assert _owned_state(plain) == _owned_state(observed), f"control state changed at tick {step}"
            assert baseline.actuators == combined.actuators, f"actuator calls changed at tick {step}"
            assert baseline.control_packets == combined.control_packets, f"control MQTT changed at tick {step}"
        assert saw_queue_full and combined.publish_failures > 0 and saw_storage_failure
        assert storage_attempts and saw_command_event and saw_positive_command and saw_fault
        assert max(observed.boiler_curve_counts) > 0, "The isolation run must exercise actual learning"
        source_packets = [payload for topic, payload in combined.diagnostic_packets if topic.endswith("/inputs")]
        assert source_packets and "command" in source_packets[-1]["samples"]
        assert source_packets[-1]["samples"]["flow"]["unit"] == "m³/h"
        assert source_packets[-1]["samples"]["flow"]["verified"] is False
    finally:
        diagnostic.worker.shutdown(wait=True, cancel_futures=True)
