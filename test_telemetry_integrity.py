import json
import sys
import types
from unittest.mock import MagicMock

# -------------------------------------------------------------------------
# Mock AppDaemon before importing controller
# -------------------------------------------------------------------------
_appdaemon = types.ModuleType("appdaemon")
sys.modules["appdaemon"] = _appdaemon

_plugins = types.ModuleType("appdaemon.plugins")
_appdaemon.plugins = _plugins
sys.modules["appdaemon.plugins"] = _plugins

_hass = types.ModuleType("appdaemon.plugins.hass")
_plugins.hass = _hass
sys.modules["appdaemon.plugins.hass"] = _hass

_hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
_hass.hassapi = _hassapi
sys.modules["appdaemon.plugins.hass.hassapi"] = _hassapi


class MockHass:
    def __init__(self):
        self.args = {}

    def initialize(self):
        pass

    def get_state(self, entity_id, attribute=None):
        return 0.0

    def call_service(self, service, **kwargs):
        pass

    def turn_on(self, entity_id, **kwargs):
        pass

    def turn_off(self, entity_id, **kwargs):
        pass

    def log(self, msg, level="INFO"):
        pass

    def listen_state(self, cb, entity_id):
        pass

    def run_every(self, cb, start, interval):
        pass


_hassapi.Hass = MockHass

# Import the controller using the shared harness logic
import importlib.util


def load_controller():
    # Find hapsic_controller.py
    path = "apps/hapsic-controller/hapsic_controller.py"
    spec = importlib.util.spec_from_file_location("hapsic_controller", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_telemetry_schema_integrity():
    """
    Rigorously validates that the publish_telemetry method correctly maps
    internal state attributes to the MQTT JSON payload without silent fallbacks.
    """
    module = load_controller()
    controller = module.HapsicController()

    # Mock the AppDaemon environment
    controller.call_service = MagicMock()

    # Mock get_state to return valid persistent data during initialize()
    def side_effect(entity_id, attribute=None):
        if entity_id == "input_number.hapsic_chi_ema":
            return "0.94"
        if entity_id == "input_text.hapsic_boiler_curve":
            return "[0.1, 0.5, 0.8, 1.2]"
        return "0.0"

    controller.get_state = MagicMock(side_effect=side_effect)

    # Fully initialize the controller
    controller.initialize()

    # Overwrite with test-specific values to ensure WE set them, not defaults
    controller.supply_dp = 45.12
    controller.duct_dp = 55.34
    controller.room_dp = 50.55
    controller.room_rh_avg = 35.2
    controller.room_temp_avg = 68.1
    controller.target_room_dp = 50.0
    controller.target_duct_dp = 58.0
    controller.outdoor_dp = 42.0
    controller.steam_voltage = 6.5
    controller.calc_steam_mass = 2.1
    controller._last_measured_steam = 1.89
    controller.calc_flux = 0.45
    controller.calc_loss_vent = 1.12
    controller.is_target_infeasible = False
    controller.max_achievable_dp = 70.0
    controller.boil_achieved = True
    controller.stasis_active = False
    controller.duct_derivative = 0.05
    controller.chi_instant = 0.95
    controller.chi_ema = 0.94
    controller.boil_status = "BOILING"
    controller.bypass_state = 0.0
    controller.fsm_state = "ACTIVE_CRUISE"
    controller.room_dp_buffer = [39.0, 39.5, 40.0]

    # Mock some state dependency methods
    controller.get_effective_max_capacity = MagicMock(return_value=2.7)

    # Trigger Serialization
    controller.publish_telemetry()

    # Intercept Payload
    if not controller.call_service.called:
        print("❌ FAIL: call_service was never called.")
        return False

    args, kwargs = controller.call_service.call_args
    if kwargs.get("topic") != "hapsic/telemetry/state":
        print(f"❌ FAIL: Incorrect MQTT topic: {kwargs.get('topic')}")
        return False

    payload = json.loads(kwargs["payload"])

    print("\n[Telemetry Integrity] Validating JSON payload...")

    errors = []

    # 1. PSYCHROMETRICS (Primary failure point)
    psych = payload.get("psychrometrics", {})
    checks = {
        "pre_steam_dp": 45.12,
        "post_steam_dp": 55.34,
        "room_dp": 50.55,
        "room_avg_rh": 35.2,
        "room_avg_temp": 68.1,
        "outdoor_dp": 42.0,
    }
    for key, expected in checks.items():
        actual = psych.get(key)
        if actual != expected:
            errors.append(f"  ❌ psychrometrics.{key}: Expected {expected}, got {actual} (Silent fallback or typo?)")

    # 2. PROCESS
    proc = payload.get("process", {})
    proc_checks = {"user_target": 50.0, "duct_target": 58.0, "max_achievable": 70.0, "is_boiling": True}
    for key, expected in proc_checks.items():
        actual = proc.get(key)
        if actual != expected:
            errors.append(f"  ❌ process.{key}: Expected {expected}, got {actual}")

    # 3. HEALTH
    health = payload.get("health", {})
    health_checks = {
        "boil_status": "BOILING",
        "chi_ratio": 0.95,
        "chi_ema": 0.94,
        "measured_steam_lbs_hr": 1.89,
        "production_efficiency": 90.0,
    }
    for key, expected in health_checks.items():
        actual = health.get(key)
        if actual != expected:
            errors.append(f"  ❌ health.{key}: Expected {expected}, got {actual}")

    # 4. PHYSICS & IO
    if payload.get("io", {}).get("steam_volts") != 6.5:
        errors.append(f"  ❌ io.steam_volts: Expected 6.5, got {payload.get('io', {}).get('steam_volts')}")
    if payload.get("physics", {}).get("flux_net") != 0.45:
        errors.append(f"  ❌ physics.flux_net: Expected 0.45, got {payload.get('physics', {}).get('flux_net')}")

    # 5. ADVISORY (observe-only economy path)
    advisory = payload.get("advisory", {})
    advisory_checks = {
        "economy_active": False,
        "economy_severe": False,
        "steaming_active": False,
        "useful_demand": False,
        "passive_import_candidate": False,
        "passive_import_lbs_hr": 0.0,
        "passive_export_lbs_hr": 0.0,
        "economy_reason": "CLEAR",
        "suggested_target_delta": 0.0,
    }
    for key, expected in advisory_checks.items():
        actual = advisory.get(key)
        if actual != expected:
            errors.append(f"  ❌ advisory.{key}: Expected {expected}, got {actual}")

    if errors:
        for err in errors:
            print(err)
        return False

    print("✅ ALL TELEMETRY MAPPINGS VERIFIED. NO SILENT FALLBACKS DETECTED.")
    return True


def test_passive_import_advisory_math():
    """Validates observe-only outdoor moisture import telemetry."""
    module = load_controller()
    controller = module.HapsicController()

    controller.supply_flow = 150.0
    controller.RHO = 0.065
    controller.outdoor_w = 45.0
    controller.room_w = 35.0
    controller.target_room_dp = 48.0
    controller.room_dp = 47.0
    controller.outdoor_dp = 48.2
    controller.fsm_state = "STANDBY"
    controller.steam_voltage = 0.0
    controller.calc_steam_mass = 0.0
    controller.calc_flux = 0.0
    controller.dt = 5.0

    controller.update_economy_advisory()

    expected = (150.0 * 0.5886) * 60.0 * 0.065 * 10.0 / 7000.0
    assert round(controller.economy_passive_import_lbs_hr, 3) == round(expected, 3)
    assert controller.economy_passive_export_lbs_hr == 0.0
    assert controller.economy_passive_import_candidate is True


if __name__ == "__main__":
    print("=" * 60)
    print("  HAPSIC Telemetry Integrity Tests")
    print("=" * 60)

    success = test_telemetry_schema_integrity()

    print("\n  TOTAL: 1/1 passed" if success else "\n  TOTAL: 0/1 passed")
    if not success:
        print("  ❌ FAILURE: Telemetry schema contains broken mappings.")
        sys.exit(1)
    else:
        print("  ✅ SUCCESS: Reporting layer is robust.")
        sys.exit(0)
