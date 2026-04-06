# HAPSIC Agent Guide

## Project Overview

HAPSIC (Home Assistant Psychrometric Source Integrated Controller) is a physics-based steam humidifier controller. It runs natively on ESP32 via ESPHome with a mathematically identical Python Digital Twin for testing.

## Repository Layout

```
hapsic/
├── apps/hapsic_controller/
│   ├── apps.yaml                  # AppDaemon app discovery (required for HACS)
│   └── hapsic_controller.py       # Python Digital Twin (SINGLE SOURCE OF TRUTH)
├── components/hapsic/             # C++ ESPHome firmware
├── dashboards/
│   └── mission-control.yaml       # HA monitoring dashboard
├── test_*.py / scenario_*.py      # Test suites and simulators
├── stamplc.yaml                   # Production ESPHome config
├── stamplc_desk.yaml              # Desk unit ESPHome config
├── hacs.json                      # HACS manifest
├── CHANGELOG.md                   # Release notes (update with every release)
└── README.md                      # User-facing documentation
```

## Critical Rules

### Source of Truth
- `apps/hapsic_controller/hapsic_controller.py` is the **only** Python controller file. There is no root-level `hapsic.py`.
- All test files import from `apps.hapsic_controller.hapsic_controller`.
- The C++ firmware in `components/hapsic/` must remain mathematically identical to the Python twin.

### Versioning & Releases
- Versions follow semver: `vMAJOR.MINOR.PATCH`
- Every new version **must** have a `CHANGELOG.md` entry explaining what changed
- Releases require a **GitHub Release** (not just a git tag) — HACS only tracks GitHub Releases
- Use the `/hacs-release` workflow for the full release process

### Testing
- Pre-push hook runs 12-stage CI automatically (lint, compile, 285+ tests)
- Use `/presubmit` to run validation manually before committing
- Never skip or bypass the CI pipeline

### HACS Deployment
- HACS installs the **entire repo** to `/addon_configs/a0d7b954_appdaemon/apps/hapsic/` (folder named after repo, not the app)
- The Python module ends up at `apps/hapsic/apps/hapsic_controller/hapsic_controller.py`
- `apps/hapsic_controller/apps.yaml` **must** ship with the repo — AppDaemon auto-discovers apps by recursively scanning for `apps.yaml` files
- **Known gotcha**: If a user previously installed manually to `apps/hapsic_controller/`, that old folder takes precedence over the HACS-installed copy. The old folder must be deleted after switching to HACS.
- After HACS updates, **AppDaemon must be restarted** to load the new code

### Dashboard
- `dashboards/mission-control.yaml` is the HA monitoring dashboard
- Requires: Mushroom Cards, ApexCharts Card, Power Flow Card Plus, card-mod
- Users install by pasting YAML into the HA raw configuration editor

## Available Workflows

| Command | Purpose |
|---------|---------|
| `/presubmit` | Run local CI validation before pushing |
| `/flash-desk` | Compile, flash, and validate desk unit firmware |
| `/live-audit` | Run live MQTT audit comparing production and desk telemetry |
| `/hacs-release` | Release a new version to HACS with changelog and GitHub Release |
