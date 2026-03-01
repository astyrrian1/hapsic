---
description: Compile, flash, and validate the desk unit firmware
---

# Flash Desk Unit Workflow

Compile the desk firmware with shadow integrator enabled, flash it to the ESP32, and run validation tests.

## Steps

// turbo-all

1. Compile the desk firmware:
```bash
esphome compile stamplc_desk.yaml
```

2. Flash the desk firmware over USB:
```bash
esphome run stamplc_desk.yaml --device /dev/cu.usbmodem101
```
> **Note:** The device path may vary. Use `ls /dev/cu.usb*` to find the correct port.

3. Wait ~3 minutes for the desk unit to boot, initialize sensors, and complete cold-start stasis.

4. Run Mode D (Component Parity) to validate math before testing convergence:
```bash
python3 test_component_parity.py
```

5. Run Mode C (Shadow Integrator) to validate voltage convergence:
```bash
python3 test_shadow_integrator.py
```

6. For continuous monitoring, run the MQTT diff auditor:
```bash
python3 read_mqtt_diff.py
```

## Troubleshooting

- **"Not enough paired frames"**: Check both production and desk units are online and publishing MQTT.
- **Voltage stuck at 9.5V**: Stasis may not have shattered yet. Wait 3 minutes after flash.
- **Compilation fails**: Run `esphome config stamplc_desk.yaml` first to validate YAML.
