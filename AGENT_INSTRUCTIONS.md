# AI Coding Agent Guidelines
This document outlines explicit rules, practices, and expectations for an autonomous or semi-autonomous AI coding agent operating within this codebase. To be successful, you must rigorously adhere to these instructions.

## 1. Safety and Assumptions
- **Never Auto-Run Destructive Commands**: Never automatically run commands that mutate state or cause destructive side effects (e.g., `rm -rf`, `git push --force`, `drop table`, flashing production hardware) without explicit user approval.
- **Do Not Guess Missing Details**: If a prompt is ambiguous or lacks crucial implementation details (e.g., "add authentication"), do not blindly implement a complex sub-system. Stop, propose a plan, and ask for clarification.
- **Read Before Writing**: Always read existing configuration files, architecture documentation, or relevant source files before suggesting changes. Do not assume the structure of a project.

## 2. Code Quality and Style
- **Match Existing Conventions**: Adopt the existing code style. If the project uses 2 spaces for indentation, do not use 4. If the project uses `camelCase`, do not use `snake_case`. Observe and match existing linting rules.
- **Do Not Remove Unrelated Code**: When instructed to modify a function, do not delete or alter unrelated functions, comments, or imports in that file unless specifically requested. 
- **Leave Code Better Than You Found It**: Fix localized, glaring issues (like syntax errors or deprecated simple calls) if you touch the surrounding lines, but do not embark on a massive rewrite without permission.
- **Comment Why, Not What**: When adding comments, explain the *reasoning* behind complex logic rather than just translating the code to English.

## 3. Tool Usage
- **Prefer Specific Tools**: Use dedicated file-reading or file-writing tools instead of executing shell commands (like `cat` or `echo`). This ensures reliability and cleaner output parsing.
- **Use Regex and Grep Wisely**: When searching large codebases, rely on structured search tools or `grep` rather than attempting to read massive files top-to-bottom.
- **Verify Tools Succeeded**: If you run a build or test command, do not assume it passed. Read the terminal output and confirm the exit code before proceeding to the next step.

## 4. Communication
- **Be Concise**: Keep conversational responses brief. The user is actively working and does not need generalized summaries if a direct answer suffices.
- **Acknowledge Mistakes**: If a test fails after your edit, acknowledge the mistake clearly and state your targeted plan to fix it. Do not attempt a brute-force fix loop without analysis.
- **Provide Actionable Next Steps**: Once a task is completed, succinctly inform the user of what was done and what (if anything) is required next (e.g., "Tests passed. Ready to flash firmware?"). 

## 5. Domain-Specific (HAPSIC / ESPHome)
- **Unit Enforcement**: This project strictly adheres to SI units for all thermodynamics. Do not introduce Fahrenheit or Imperial conversions into the C++ or Python logic engines.
- **Testing is Mandatory**: Never skip running the complete test suite (`./run_all_tests.sh`) before flashing physical hardware.
- **Configuration Awareness**: Be deeply aware of the differences between the test environments (`stamplc_desk.yaml`, `stamplc_hil.yaml`) and the production environment (`stamplc.yaml`). Do not cross-pollinate their specific sensor structures lazily.
- **Python is Source of Truth**: The Python implementation (`hapsic.py`) is the authoritative reference for all control logic, PID parameters, psychrometric formulas, and state machine transitions. Do not modify it unless there are serious bugs — always bring changes to the user's attention first.

## 6. Validation Modes
Two live validation modes exist for verifying C++/Python parity on the desk unit:

- **Mode C (Shadow Integrator)**: The desk unit subscribes to production MQTT and mirrors the PID integrator to track production voltage. Run `python3 test_shadow_integrator.py`. Use for pre-release validation, PID/batch logic changes.
- **Mode D (Component Parity)**: Validates 10 mathematical components individually (room_dp, target_duct_dp, supply_dp, v_ff, max_achievable, fsm_state, ceiling_volts, duct_derivative, etc.). Run `python3 test_component_parity.py`. Use for formula changes, sensor fallback debugging.

## 7. Presubmit Workflow
- **Always run `bash run_presubmit.sh` before pushing to GitHub.** This validates lint, YAML config, compilation, and offline tests.
- **For live validation**, run `bash run_presubmit.sh --live` which adds Mode C and Mode D tests.
- **After flashing desk firmware**, follow the `/flash-desk` workflow: compile → flash → wait 3 min → run Mode D → run Mode C.
- **Agent workflows** are defined in `.agents/workflows/`. Use `/presubmit`, `/flash-desk`, and `/live-audit` slash commands.

## 8. Sensor Entity Hierarchy
Production sensors with designated primary/fallback roles:

| Measurement | Primary | Fallback |
|---|---|---|
| Room Temp | `sensor.hapsic_room_average_temp` (°F) | Zehnder Extract CAN |
| Room RH | `sensor.hapsic_room_average_rh` | Zehnder Extract CAN |
| Pre-Steam Temp | `sensor.hapsic_pre_steam_temp` (°F) | *(backup only)* |
| Pre-Steam RH | `sensor.hapsic_pre_steam_rh` | *(backup only)* |
| Target DP | `input_number.target_dew_point` (°F) | — |
| Max Capacity | `input_number.humidifier_max_capacity` (lbs/hr, default 2.7) | — |
| Supply Flow | `sensor.hapsic_supply_flow` (m³/h, fallback) | — |
| Room DP Goal | `sensor.hapsic_room_dew_point` (°F) | — |
