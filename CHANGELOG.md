# Changelog

All notable changes to the HAPSIC Controller are documented here.
Versions follow [Semantic Versioning](https://semver.org/).

## [v2.5.0] — 2026-04-08

### Added
- **4 new Health Telemetry HA entities**: The C++ ESPHome firmware now publishes the following Home Assistant sensor entities directly, previously only visible via MQTT JSON or the Python Digital Twin:
  - `sensor.hapsic_health_chi_instant` — real-time χ ratio (actual vs theoretical steam output)
  - `sensor.hapsic_health_effective_max` — current effective max capacity after limiter degradation
  - `sensor.hapsic_health_measured_steam` — measured steam output in lbs/hr
  - `text_sensor.hapsic_health_boil_status` — COLD/BOILING boiler state
- **Expanded MQTT health telemetry payload**: The `health` block in the JSON telemetry now includes `boil_status`, `effective_max_capacity`, `measured_steam_lbs_hr`, `boiler_curve`, and `boiler_curve_samples`, bringing C++ firmware MQTT parity with the Python Digital Twin.
- **Mission Control dashboard — Boiler Health & Characterization section**: New section between Canister Health and Physics Engine:
  - Measured Steam / Effective Max / Boiler State tile row with dynamic color coding
  - Boiler Curve (Learned) markdown table with ASCII bar histogram across 4 voltage bins
  - Canister Health (χ Ratio) ApexChart plotting χ instant and 48h EMA on a 12-hour window

### Architecture
- C++ firmware telemetry now matches Python Digital Twin MQTT health schema exactly — all health fields are published by both runtimes.

## [v2.4.0] — 2026-04-07

### Added
- **Online Boiler Characterization Curve**: Runtime learning of the actual voltage→steam-delivery relationship. Four 2V bins (2–4V, 4–6V, 6–8V, 8–10V) accumulate EMA-smoothed measurements of lbs/hr at each voltage level. After 50 samples per bin the learned curve replaces the linear nameplate model for V_FF feedforward computation and max-achievable-DP feasibility calculations.
- **Persistent boiler curve storage**: Curve data persists across restarts via `input_text.hapsic_boiler_curve` (JSON array). Restored bins are pre-marked as trained on startup.
- **`get_effective_max_capacity()`**: Returns the highest learned delivery rate when trained, falling back to CHI-corrected nameplate capacity.
- **`voltage_for_steam_rate()`**: Inverts the learned curve with linear interpolation between trained bins. Replaces the hard-coded `(lbs_hr / MAX_CAPACITY) * 10.0` formula.
- **Boiler curve telemetry**: New MQTT fields — `boiler_curve`, `boiler_curve_samples`, `effective_max_capacity`, `measured_steam_lbs_hr` — exposed in the `health` telemetry block.
- **Infeasibility hysteresis tests**: 10 new tests validating the SET/CLEAR deadband, boundary conditions, oscillation resistance, and multi-cycle drift-free operation.

### Changed
- **Infeasibility hysteresis deadband widened**: SET threshold raised from +0.5°F to +1.0°F above max-achievable DP; CLEAR threshold moved from 0.0°F to −0.5°F below. The 1.5°F deadband eliminates chatter during boundary oscillation without meaningfully delaying SET/CLEAR transitions.
- **Feasibility mass-balance uses learned capacity**: `get_effective_max_capacity()` replaces raw `MAX_CAPACITY` in the mass-balance equation, giving more accurate feasibility horizons as the boiler ages or canister health degrades.

### Architecture
- Python Digital Twin and C++ ESPHome firmware remain mathematically identical — both implement the same 4-bin EMA boiler curve with shared constants and identical interpolation logic.

## [v2.3.3] — 2026-04-07

### Fixed
- **AppDaemon MissingAppClass crash**: Renamed `apps/hapsic_controller/` → `apps/hapsic-controller/` (hyphen). The underscore-named directory collided with the module name `hapsic_controller` — when `__init__.py` was present, Python resolved the *directory* as the module instead of the `.py` file, giving AppDaemon an empty module and crashing with `MissingAppClass`. The hyphenated directory name is un-importable as a Python package, permanently preventing this class of bug.

### Changed
- **Test imports**: All test files and standalone scripts (`scenario_tester.py`, `run_compare.py`) now use `importlib` file-based loading instead of `from apps.hapsic_controller import ...`. A shared `conftest.py` centralizes the import for pytest runs.
- **Removed `__init__.py` files**: No longer needed or shipped — the hyphenated directory makes them structurally impossible to abuse.
- **Cleaned `.gitignore`**: Removed `__init__.py` exclusion rules that are no longer relevant.

## [v2.3.2] — 2026-04-07

### Fixed
- **ACTIVE_CRUISE deadlock**: FSM could get stuck in `ACTIVE_CRUISE` indefinitely when room dew point slightly exceeded the target while Loop A's integrator held voltage positive. The only exit path required `steam_voltage == 0.0`, which the integrator couldn't reach. Added a **Target Met** check (`room_deficit <= 0.0`) that fires unconditionally, matching the C++ firmware's existing behavior.

### Added
- **Deadlock regression tests**: Three new FSM tests covering the exact production deadlock scenario, target-met-with-nonzero-voltage, and satisfaction coasting validation.
- **Dev environment tooling**: `requirements-dev.txt` for reproducible venv setup; auto-activation guards in `run_presubmit.sh` and `run_all_tests.sh`; venv instructions in agent workflows.

### Changed
- Disabled MQTT telemetry in desk unit ESPHome config (`stamplc_desk.yaml`).

## [v2.3.1] — 2026-04-06

### Fixed
- **Notification target DP mismatch**: Maintenance alerts and daily digest now display the user's actual target dew point (`input_number.target_dew_point`) instead of the PID Loop A duct target (`sensor.hapsic_target_duct_dp`). Previously, a user target of 48.0°F would show as ~50.1°F in notifications.

## [v2.3.0] — 2026-04-06

### Added
- **Notification Blueprints**: Three HA Blueprint automations for HAPSIC monitoring, installable with one click:
  - **Critical Fault Alerts** — Immediate notification when HAPSIC enters a fault state (zero flow, sensor failure, clogged filter, defrost, bypass, or watchdog timeout). Includes configurable cooldown to prevent notification spam.
  - **Maintenance & Awareness** — Non-urgent alerts for target dew point infeasibility, fault recovery, and canister health degradation (CHI EMA). Each alert type independently toggleable.
  - **Daily Status Digest** — Once-daily summary of system health at a user-chosen time, including house conditions, steam output, estimated energy consumption (Aprilaire 801 specs), canister health, and weather forecast.
- All blueprints support `persistent_notification`, `notify.notify`, or any custom notification service (e.g., `notify.mobile_app_pixel`).

## [v2.2.5] — 2026-04-06

### Fixed
- **HACS AppDaemon discovery**: Added `apps.yaml` to `apps/hapsic_controller/` so AppDaemon auto-discovers the app when installed via HACS. Previously, HACS installs to `apps/hapsic/` (repo name) which nested the module at `apps/hapsic/apps/hapsic_controller/` — without an `apps.yaml`, AppDaemon couldn't find it, causing users with a prior manual install (`apps/hapsic_controller/`) to silently run stale code.

### Changed
- Updated `AGENTS.md` with HACS deployment section documenting the folder structure, `apps.yaml` requirement, and known manual-install conflict gotcha.

## [v2.2.4] — 2026-04-06

**GOLD MASTER — Initial HACS Release**

### Added
- HACS AppDaemon distribution support (`apps/hapsic_controller/` + `hacs.json`)
- Paradox Resolved: Loop A now evaluates raw User Target directly
- Thermodynamic Memory with 15-minute cooldown tracking
- 9.5V Ignition Strike for cold-start boil detection
- Bumpless Handoff Prime: seeds PID integrator on stasis shatter
- Universal Directional Freezing across all loop phases

### Architecture
- Absolute Dew Point Setpoint Paradigm (replaces RH-based control)
- Real-time Building Physics Mass Balance (1380 CFM50 / 17.0 N-Factor)
- 15-minute rolling buffer for structure velocity tracking
- Loop A Feasibility Clamp & Loop B Sliding Safety Ceiling
- Smart V_FF Cold-Start Jump & Derivative Boil-Detect Release
- State-Based Asymmetric Batch Manager (Fast Down / Slow Up)

### Testing
- 285 automated tests across 12 CI stages
- Python/C++ cross-platform parity validation
- Full ESPHome compilation verification in pre-push hook
