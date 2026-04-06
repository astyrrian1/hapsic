import csv
import sys
import time
import types


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

hass = types.ModuleType('hass')
plugins.hass = hass
sys.modules['appdaemon.plugins.hass'] = hass

hassapi = types.ModuleType('hassapi')
hass.hassapi = hassapi
sys.modules['appdaemon.plugins.hass.hassapi'] = hassapi

hassapi.Hass = MockHass

import hapsic


def run_tests():
    controller = hapsic.HapsicController()

    controller.states = {
        "input_number.humidifier_max_capacity": 2.7, # lbs/hr
        "input_number.target_dew_point": 50.0, # 10C
        "sensor.hapsic_cleansed_post_steam_temp": 68.0,
        "sensor.hapsic_supply_flow": 400.0, # Zehnder CAN native
        "sensor.hapsic_extract_flow": 400.0,
        "sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_temperature": 59.0,
        "sensor.zehnder_comfoair_q_a4cb9c_outdoor_air_humidity": 50.0,

        # Mapped from CSV
        "sensor.hapsic_pre_steam_temp": 68.0,
        "sensor.hapsic_pre_steam_rh": 30.0,
        "sensor.hapsic_room_average_temp": 68.0,
        "sensor.hapsic_room_average_rh": 30.0,
        "sensor.hapsic_cleansed_inside_temp": 68.0,
        "sensor.hapsic_cleansed_inside_rh": 30.0,

        "input_number.hapsic_chi_ema": 1.0,
    }

    controller.args = {
        "target_dew_point": "input_number.target_dew_point",
        "max_capacity": "input_number.humidifier_max_capacity",
        "manual_reset": "input_boolean.manual_reset",
        "duct_temp": "sensor.hapsic_cleansed_post_steam_temp",
        "duct_rh": "sensor.hapsic_cleansed_post_steam_rh",
        "supply_flow": "sensor.hapsic_supply_flow",
        "extract_flow": "sensor.hapsic_extract_flow",
        "bypass": "sensor.zehnder_comfoair_q_a4cb9c_bypass_state",
        "outdoor_temp": "sensor.outdoor_temp",
        "outdoor_rh": "sensor.outdoor_humidity",
        "pre_steam_temp": "sensor.hapsic_pre_steam_temp",
        "pre_steam_rh": "sensor.hapsic_pre_steam_rh",
        "extract_avg_temp": "sensor.hapsic_round_room_temp",
        "extract_avg_rh": "sensor.hapsic_round_room_rh",
        "steam_dac": "output.steam_dac",
        "fan_dac": "output.fan_dac"
    }

    controller.initialize()
    controller.last_tick_ts = time.time() - 5.0

    # Read CSV and group by timestamp
    timeline = {}
    with open('scenario_data.csv', 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = row['last_changed']
            if ts not in timeline:
                timeline[ts] = {}
            val = float(row['state'])
            if 'temp' in row['entity_id']:
                val = val * 9.0/5.0 + 32.0 # SI to Imperial for python PRD
            elif 'flow' in row['entity_id']:
                val = val * 0.588578 # m3/h to CFM
            timeline[ts][row['entity_id']] = val

    sorted_times = sorted(list(timeline.keys()))

    print("--- PYTHON SCENARIO STARTS ---")

    last_state = ""

    # Mock time.time() to simulate perfectly advancing 5 seconds per tick
    mock_current_time = [time.time()]

    def fake_time():
        return mock_current_time[0]

    time.time = fake_time

    for idx, ts in enumerate(sorted_times):
        updates = timeline[ts]
        for k, v in updates.items():
            if k == 'sim_extract_temp': controller.states['sensor.hapsic_pre_steam_temp'] = v
            if k == 'sim_extract_rh': controller.states['sensor.hapsic_pre_steam_rh'] = v
            if k == 'sim_avg_temp':
                controller.states['sensor.hapsic_room_average_temp'] = v
                controller.states['sensor.hapsic_cleansed_inside_temp'] = v
            if k == 'sim_avg_rh':
                controller.states['sensor.hapsic_room_average_rh'] = v
                controller.states['sensor.hapsic_cleansed_inside_rh'] = v
            if k == 'sim_duct_temp': controller.states['sensor.hapsic_cleansed_post_steam_temp'] = v
            if k == 'sim_duct_rh': controller.states['sensor.hapsic_cleansed_post_steam_rh'] = v
            if k == 'sim_supply_flow': controller.states['sensor.hapsic_supply_flow'] = v

        # Advance mock time by exactly 5 seconds
        mock_current_time[0] += 5.0

        controller.master_tick({})

        current_state = controller.fsm_state
        if current_state != last_state:
            deficit = controller.states["input_number.target_dew_point"] - controller.room_dp
            reason = getattr(controller, 'fault_reason', 'None')
            print(f"[{ts}] State Changed to {current_state} (Reason: {reason}) | Deficit: {deficit:.2f}F")
            last_state = current_state

if __name__ == "__main__":
    try:
        run_tests()
        print("Scenario passed gracefully.")
        sys.exit(0)
    except Exception as e:
        print(f"Scenario failed with error: {e}")
        sys.exit(1)
