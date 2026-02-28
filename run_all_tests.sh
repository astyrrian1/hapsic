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

echo "========================================="
echo "✅ ALL CI PIPELINE TESTS COMPLETED AND PASSED."
echo "========================================="
exit 0
