---
description: Run local presubmit validation before pushing to GitHub
---

# Presubmit Workflow

Run all local validations before pushing code to GitHub. This catches issues **before** CI runs.

## Steps

// turbo-all

1. Run the local presubmit script (offline tests only):
```bash
bash run_presubmit.sh
```

2. If both production and desk units are online and you want to validate live MQTT parity:
```bash
bash run_presubmit.sh --live
```

3. If all tests pass, the code is safe to commit and push:
```bash
git add -A
git commit -m "your commit message"
git push origin main
```

## What Presubmit Validates

| Step | Tool | Validates |
|------|------|-----------|
| 1 | `ruff` | Python linting |
| 2 | `esphome config` | YAML syntax for production and desk configs |
| 3 | `esphome compile` | C++ firmware compilation |
| 4 | `run_all_tests.sh` | Full 12-step offline CI (see below) |
| 5 | `test_component_parity.py` | *(live only)* Mode D component math validation |
| 6 | `test_shadow_integrator.py` | *(live only)* Mode C voltage convergence validation |

### Offline CI Steps (via `run_all_tests.sh`)

| # | Test | Assertions |
|---|------|-----------|
| 1–6 | Config, scenarios, PID compare, C++ sim, telemetry | baseline |
| 7 | `test_unit_conversions.py` | 26 (imperial/SI boundary) |
| 8 | `test_psychrometrics.py` | 20 (formulas + EMA) |
| 9 | `test_sensor_fallback.py` | 10 (fallback chains, cache) |
| 10 | `test_output_safety.py` | 206 (voltage invariants) |
| 11 | `test_fsm_transitions.py` | 14 (state machine + safety) |
| 12 | `test_offline_parity.py` | 9 (Py/C++ parity) |
