import json
import os
import time

import paho.mqtt.client as mqtt
import yaml


# Load MQTT configuration from secrets.yaml
def load_secrets():
    secrets_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'secrets.yaml')
    with open(secrets_path, 'r') as f:
        return yaml.safe_load(f)

secrets = load_secrets()
MQTT_BROKER = secrets.get('mqtt_broker', 'homeassistant.local')
MQTT_USERNAME = secrets.get('mqtt_username', '')
MQTT_PASSWORD = secrets.get('mqtt_password', '')

TOPIC_PROD = "hapsic/telemetry/state"
TOPIC_DESK = "hapsic-desk/telemetry/state"

last_prod = None
last_desk = None

def on_connect(client, userdata, flags, reason_code, properties):
    print(f"Connected to MQTT Broker {MQTT_BROKER} with code {reason_code}")
    client.subscribe([(TOPIC_PROD, 0), (TOPIC_DESK, 0)])
    print(f"Subscribed to {TOPIC_PROD} and {TOPIC_DESK}")
    print("\n" + "="*120)
    print(f"{'PRODUCTION (Python/Fahrenheit)':^58} || {'DESK UNIT (C++/Celsius)':^58}")
    print("="*120)

def on_message(client, userdata, msg):
    global last_prod, last_desk

    try:
        payload = json.loads(msg.payload.decode('utf-8'))
    except json.JSONDecodeError:
        return

    if msg.topic == TOPIC_PROD:
        last_prod = payload
    elif msg.topic == TOPIC_DESK:
        last_desk = payload

    if last_prod and last_desk:
        compare_and_print(last_prod, last_desk)

def format_prod_line(d):
    try:
        fsm = d.get('fsm', {}).get('state', 'N/A')
        room_f = d.get('psychrometrics', {}).get('room_dp', 0.0)
        sp_f = d.get('process', {}).get('user_target', 0.0)
        duct_f = d.get('psychrometrics', {}).get('post_steam_dp', 0.0)
        max_ach = d.get('process', {}).get('max_achievable_dp', 0.0)
        out = d.get('io', {}).get('steam_volts', 0.0)
        return f"{fsm:^14} | R:{room_f:4.1f}F | S:{sp_f:4.1f}F | D:{duct_f:4.1f}F | Max:{max_ach:4.1f}F | O:{out:4.1f}V"
    except Exception:
        return "Parsing Error"

def format_desk_line(d):
    try:
        fsm = d.get('fsm', {}).get('state', 'N/A')
        room_c = d.get('loop_a', {}).get('pv_room_dp', 0.0)
        sp_c = d.get('loop_a', {}).get('sp_user_target', 0.0)
        duct_c = d.get('loop_b', {}).get('pv_duct_dp', 0.0)
        max_c = d.get('feasibility', {}).get('max_achievable_dp', 0.0)
        out = d.get('io', {}).get('volts_out', 0.0)
        vff = d.get('loop_b', {}).get('v_ff', 0.0)
        ideal = d.get('loop_b', {}).get('ideal_voltage', 0.0)

        # Add conversions for apples to apples math verification visually
        r_f = (room_c * 9/5) + 32
        sp_f = (sp_c * 9/5) + 32
        max_f = (max_c * 9/5) + 32

        return (
            f"{fsm:^14} | R:{r_f:4.1f}F | S:{sp_f:4.1f}F | D:{duct_c:4.1f}C"
            f" | Max:{max_f:4.1f}F | O:{out:4.1f}V (Id:{ideal:3.1f} FF:{vff:3.1f})"
        )
    except Exception:
        return "Parsing Error"

def compare_and_print(prod_data, desk_data):
    str_prod = format_prod_line(prod_data)
    str_desk = format_desk_line(desk_data)
    print(f"{str_prod:<58} || {str_desk:<58}")

    alerts = []
    try:
        # FSM State Comparison
        fsm_p = prod_data.get('fsm', {}).get('state', 'N/A')
        fsm_d = desk_data.get('fsm', {}).get('state', 'N/A')
        if fsm_p != fsm_d and fsm_p != 'N/A' and fsm_d != 'N/A':
            alerts.append(f"  [!] STATE DISCREPANCY: Prod > {fsm_p} | Desk > {fsm_d}")

        # Steam output comparison (should be exactly identical <0.1V bounds)
        out_p = prod_data.get('io', {}).get('steam_volts', 0.0)
        out_d = desk_data.get('io', {}).get('volts_out', 0.0)
        if abs(out_p - out_d) > 0.1 and out_d > 0.0:
            alerts.append(f"  [!] VOLTAGE MISMATCH: Prod is requesting {out_p:.1f}V | Desk is requesting {out_d:.1f}V")

        # DP conversion and tolerance comparison
        room_p = prod_data.get('psychrometrics', {}).get('room_dp', 0.0)
        room_d_c = desk_data.get('loop_a', {}).get('pv_room_dp', 0.0)
        if room_d_c != 0.0: # Skip if uninitialized
            room_d_f = (room_d_c * 9/5) + 32
            if abs(room_p - room_d_f) > 0.5:
                alerts.append(
                    f"  [!] SENSOR DEVIATION: Room DP discrepancy."
                    f" Prod {room_p:.1f}F vs Desk {room_d_f:.1f}F."
                )

        duct_p = prod_data.get('psychrometrics', {}).get('post_steam_dp', 0.0)
        duct_d_c = desk_data.get('loop_b', {}).get('pv_duct_dp', 0.0)
        if duct_d_c != 0.0:
            duct_d_f = (duct_d_c * 9/5) + 32
            if abs(duct_p - duct_d_f) > 1.0:
                alerts.append(
                    f"  [!] SENSOR DEVIATION: Duct DP discrepancy"
                    f" (Wait for sensor filters to align)."
                    f" Prod {duct_p:.1f}F vs Desk {duct_d_f:.1f}F."
                )

    except Exception as e:
        alerts.append(f"  [!] Analysis Error: {e}")

    for alert in alerts:
        # ANSII Bright Red
        print(f"\033[91m{alert}\033[0m")
from paho.mqtt.client import CallbackAPIVersion

client = mqtt.Client(CallbackAPIVersion.VERSION2, "hapsic_auditor_script")
if MQTT_USERNAME:
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

client.on_connect = on_connect
client.on_message = on_message

try:
    client.connect(MQTT_BROKER, 1883, 60)
    client.loop_forever()
except KeyboardInterrupt:
    print("Exiting...")
except Exception as e:
    print(f"MQTT Connection failed: {e}")
