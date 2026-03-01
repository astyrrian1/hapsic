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
| 4 | `run_all_tests.sh` | Full offline CI (scenarios, simulations, telemetry) |
| 5 | `test_component_parity.py` | *(live only)* Mode D component math validation |
| 6 | `test_shadow_integrator.py` | *(live only)* Mode C voltage convergence validation |
