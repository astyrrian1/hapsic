# Changelog

All notable changes to the HAPSIC Controller are documented here.
Versions follow [Semantic Versioning](https://semver.org/).

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
