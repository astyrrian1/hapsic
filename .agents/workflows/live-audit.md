---
description: Run live MQTT audit comparing production and desk telemetry
---

# Live MQTT Audit Workflow

Monitor real-time telemetry from both production (Python) and desk (C++) units side-by-side.

## Steps

1. Start the MQTT diff auditor in the background:
```bash
python3 read_mqtt_diff.py
```

2. Let it run for at least 15 seconds to accumulate data.

3. Monitor the output for red `[!]` alerts:
   - **STATE DISCREPANCY**: FSM state mismatch — investigate immediately.
   - **VOLTAGE MISMATCH**: Check if shadow integrator is active. If not in shadow mode, voltage divergence is expected (see Mode D documentation).
   - **SENSOR DEVIATION**: Check sensor fallback chains and HA entity availability.

4. To investigate a discrepancy:
   - Run `python3 test_component_parity.py` to isolate which component diverges.
   - Check C++ source in `components/hapsic/hapsic.cpp` for the specific computation.

5. To terminate the auditor, press `Ctrl+C`.

## Quick Reference: MQTT Topics

| Topic | Source | Format |
|-------|--------|--------|
| `hapsic/telemetry/state` | Production Python | JSON (Fahrenheit, lbs) |
| `hapsic-desk/telemetry/state` | Desk C++ | JSON (Celsius, kg) |
