# Changelog

All notable changes to the HAPSIC Controller are documented here.
Versions follow [Semantic Versioning](https://semver.org/).

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
