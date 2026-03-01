#!/bin/bash
set -e # Exit immediately if any command returns a non-zero status

# ==========================================
# HAPSIC ENTERPRISE TEST SUITE (CI RUNNER)
# ==========================================
echo "=== 1. Starting Production ESPHome Configuration Verification ==="
bash verify.sh

echo "=== 2. Generating Deterministic Test CSV Database ==="
python3 scenario_builder.py

echo "=== 3. Executing Python Native PRD Reference Traces ==="
python3 scenario_tester.py > python_scenarios.txt
echo "✓ Python Hapsic PRD state machine validated cleanly."

echo "=== 4. Executing Python Standard Simulation Compare ==="
python3 run_compare.py > python_sim_output.txt
echo "✓ Python Hapsic PID / FSM loops ran without trace exceptions."

echo "=== 5. Building & Executing C++ Physical ESPHome Simulation ==="
bash run_cpp.sh
echo "✓ C++ Simulation Binary Execution Successful."

# Verification check of the output
if grep -q "SCENARIO SIMULATION STARTED" cpp_scenarios.txt; then
  echo "✓ C++ output logs successfully recorded."
else
  echo "❌ C++ Firmware Simulator failed to output properly. Log dump:"
  cat cpp_scenarios.txt
  exit 1
fi

echo "=== 6. Validating Native Telemetry Emplacements ==="
if grep -q "Sim Total CFM Loss" cpp_scenarios.txt; then
  echo "✓ Native ESP32 API Telemetry successfully published to dummy sensor (Sim Total CFM Loss)."
else
  echo "❌ ESP32 telemetry failed to publish. Check mapped sensor or hapsic.cpp mappings."
  cat cpp_scenarios.txt | grep "Sending state" || true
  exit 1
fi

echo "=== 7. Psychrometric Unit Tests ==="
python3 test_psychrometrics.py
echo "✓ All psychrometric formulas validated."

echo "=== 8. FSM Transition Matrix Tests ==="
python3 test_fsm_transitions.py
echo "✓ All FSM state transitions validated."

echo "=== 9. Offline Cross-Platform Parity ==="
python3 test_offline_parity.py
echo "✓ Python/C++ offline parity validated."

echo "========================================="
echo "✅ ALL CI PIPELINE TESTS COMPLETED AND PASSED."
echo "========================================="
exit 0
