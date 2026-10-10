"""Deterministic boundary doubles; all control decisions remain in shipped code."""

import copy
import math
import sys
from datetime import datetime, timezone

from conftest import load_controller


class Clock:
    def __init__(self):
        self.elapsed = 1000.0

    def monotonic(self):
        return self.elapsed

    def time(self):
        return 1_700_000_000 + self.elapsed

    def advance(self, seconds):
        assert seconds >= 0
        self.elapsed += seconds


class ControllerRig:
    def __init__(self, monkeypatch, *, initialize=True, args=None):
        self.clock = Clock()
        module = load_controller(
            f"_hapsic_test_{id(self)}", lambda name, value: monkeypatch.setitem(sys.modules, name, value)
        )
        module.time = self.clock
        self.controller = module.HapsicController()
        self.controller.args = dict(args or {})
        self.reports = {}
        self.commands = []
        self.scheduled = []
        self.fail_services = set()
        self.sequence = 0
        self.controller.get_state = self.get_state
        self.controller.call_service = self.call_service
        self.controller.turn_off = lambda entity, **kw: self.call_service("light/turn_off", entity_id=entity, **kw)
        self.controller.turn_on = lambda entity, **kw: self.call_service("light/turn_on", entity_id=entity, **kw)
        self.controller.run_every = lambda *args, **kw: self.scheduled.append((args, kw))
        self.controller.log = lambda *args, **kw: None
        defaults = {
            "input_number.humidifier_max_capacity": 2.7,
            "input_number.target_dew_point": 50,
            "input_number.hapsic_chi_ema": 1,
            self.controller.SHELLY_LIGHT_ENTITY: "off",
            self.controller.SHELLY_DUCT_RH_ENTITY: 35,
            "sensor.shelly0110dimg3_28372f3e866c_temperature_2": 68,
            "sensor.hapsic_cleansed_post_steam_temp": 68,
            "sensor.hapsic_cleansed_post_steam_rh": 35,
            "sensor.hapsic_supply_flow": 400,
            "sensor.hapsic_extract_flow": 400,
            "sensor.hapsic_cleansed_supply_temp": 68,
            "sensor.hapsic_cleansed_supply_rh": 30,
            "sensor.hapsic_room_average_temp": 68,
            "sensor.hapsic_room_average_rh": 30,
            "sensor.hapsic_cleansed_inside_temp": 68,
            "sensor.hapsic_cleansed_inside_rh": 30,
            "sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_temperature": 59,
            "sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_humidity": 50,
            "sensor.zehnder_comfoair_q_a4cb9c_bypass_state": 0,
        }
        for entity, value in defaults.items():
            self.report(entity, value)
        if initialize:
            self.controller.initialize()

    def report(self, entity, value, **attributes):
        self.sequence += 1
        self.reports[entity] = {
            "state": value, "attributes": attributes,
            "last_reported": datetime.fromtimestamp(self.clock.time(), timezone.utc).isoformat(),
            "context": {"id": str(self.sequence)},
        }

    def get_state(self, entity, attribute=None):
        report = self.reports.get(entity)
        if report is None:
            return None
        if attribute == "all":
            return copy.deepcopy(report)
        return report["attributes"].get(attribute) if attribute else report["state"]

    def call_service(self, service, **kwargs):
        self.commands.append({"service": service, "time": self.clock.monotonic(), **kwargs})
        if service in self.fail_services:
            raise RuntimeError(f"injected failure: {service}")
        # Acceptance does not fabricate a device report or physical actuation.
        return None

    def advance(self, seconds=5):
        self.clock.advance(seconds)
        self.controller.master_tick({})

    def snapshot(self):
        c = self.controller
        return {"state": c.fsm_state, "output": c.steam_voltage, "fault": c.fault_reason,
                "room_dp": c.room_dp, "duct_dp": c.duct_dp, "stasis": c.stasis_active,
                "integrator_a": c.integrator_a, "integrator_b": c.integrator_b}


def assert_snapshots_match(actual, expected, tolerances=None):
    assert actual.keys() == expected.keys(), (actual, expected)
    for key, wanted in expected.items():
        value = actual[key]
        if isinstance(wanted, (int, float)) and not isinstance(wanted, bool):
            assert math.isfinite(value) and math.isfinite(wanted), (key, value, wanted)
            assert math.isclose(value, wanted, rel_tol=0, abs_tol=(tolerances or {}).get(key, 1e-6)), (
                key, value, wanted)
        else:
            assert type(value) is type(wanted) and value == wanted, (key, value, wanted)
