import sys


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

class MockHassapi:
    Hass = MockHass

import types

appdaemon = types.ModuleType('appdaemon')
sys.modules['appdaemon'] = appdaemon

plugins = types.ModuleType('plugins')
appdaemon.plugins = plugins
sys.modules['appdaemon.plugins'] = plugins

hass = types.ModuleType('hass')
plugins.hass = hass
sys.modules['appdaemon.plugins.hass'] = hass

hassapi = types.ModuleType('hassapi')
hass.hassapi = hassapi
sys.modules['appdaemon.plugins.hass.hassapi'] = hassapi

hassapi.Hass = MockHass

import importlib.util
import pathlib
import time

_p = pathlib.Path(__file__).parent / "apps" / "hapsic-controller" / "hapsic_controller.py"
_s = importlib.util.spec_from_file_location("hapsic_controller", _p)
hapsic = importlib.util.module_from_spec(_s)
_s.loader.exec_module(hapsic)


def run_comparison():
    controller = hapsic.HapsicController()

    # Initialize states matching tests.yaml
    controller.states = {
        "input_number.humidifier_max_capacity": 2.7,
        "input_number.target_dew_point": 50.0,

        # Mapped from CSV
        "sensor.hapsic_pre_steam_temp": 68.0,
        "sensor.hapsic_pre_steam_rh": 30.0,
        "sensor.hapsic_room_average_temp": 68.0,
        "sensor.hapsic_room_average_rh": 30.0,
        "sensor.hapsic_cleansed_inside_temp": 68.0,
        "sensor.hapsic_cleansed_inside_rh": 30.0,

        "sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_temperature": 41.0,
        "sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_humidity": 80.0,

        "sensor.hapsic_cleansed_post_steam_temp": 68.0,
        "sensor.hapsic_cleansed_post_steam_rh": 35.0,

        "sensor.hapsic_supply_flow": 340.0,
        "sensor.hapsic_extract_flow": 340.0,
        "sensor.zehnder_comfoair_q_a4cb9c_bypass_state": 0.0,

        "input_number.hapsic_chi_ema": 1.0,
    }

    controller.initialize()

    # Force dt to 5.0 seconds
    controller.last_tick_ts = time.time() - 5.0

    print("--- PYTHON SIMULATION STARTS ---")
    for tick in range(1, 61):
        controller.last_tick_ts = time.time() - 5.0


        if getattr(controller, 'steam_voltage', 0.0) > 1.0:
            controller.states["sensor.hapsic_cleansed_post_steam_rh"] += 2.0
        else:
            if controller.states["sensor.hapsic_cleansed_post_steam_rh"] > 30.0:
                 controller.states["sensor.hapsic_cleansed_post_steam_rh"] -= 0.5

        controller.master_tick({})

        if tick % 2 == 0:
            print(f"Tick {tick:02d} | State: {controller.fsm_state:<13} | "
                  f"R_DP: {controller.room_dp:.2f}F | D_DP: {controller.duct_dp:.2f}F | "
                  f"SP: {controller.target_duct_dp:.2f}F | "
                  f"Out: {controller.steam_voltage:.2f}V | "
                  f"IntA {controller.integrator_a:.2f} | "
                  f"IntB {controller.integrator_b:.2f} | "
                  f"Der: {controller.duct_derivative:.2f}")

if __name__ == "__main__":
    try:
        run_comparison()
        print("Python comparison completed successfully.")
        sys.exit(0)
    except Exception as e:
        print(f"Comparison trace failed with error: {e}")
        sys.exit(1)
