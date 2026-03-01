#!/bin/bash
set -e

# ==========================================
# HAPSIC LOCAL PRESUBMIT VALIDATION
# ==========================================
# Run this before pushing to GitHub. It executes all offline tests
# and optionally runs live MQTT tests if both units are online.
#
# Usage:
#     bash run_presubmit.sh          # Offline only
#     bash run_presubmit.sh --live   # Offline + live MQTT tests

LIVE_MODE=false
if [[ "$1" == "--live" ]]; then
  LIVE_MODE=true
fi

echo "========================================="
echo "  HAPSIC LOCAL PRESUBMIT VALIDATION"
echo "========================================="

# 1. Lint: Python (ruff)
echo ""
echo "=== 1. Python Lint (ruff) ==="
if command -v ruff &> /dev/null; then
  ruff check hapsic.py test_harness.py test_component_parity.py test_shadow_integrator.py scenario_tester.py run_compare.py || true
  echo "✓ Ruff lint completed."
else
  echo "⚠ ruff not installed, skipping Python lint."
fi

# 2. ESPHome YAML validation
echo ""
echo "=== 2. ESPHome Configuration Validation ==="
esphome config stamplc.yaml > /dev/null
echo "✓ Production YAML valid."
esphome config stamplc_desk.yaml > /dev/null
echo "✓ Desk YAML valid."

# 3. ESPHome desk compilation
echo ""
echo "=== 3. ESPHome Desk Firmware Compilation ==="
esphome compile stamplc_desk.yaml
echo "✓ Desk firmware compiled successfully."

# 4. Full offline CI suite
echo ""
echo "=== 4. Offline CI Test Suite ==="
bash run_all_tests.sh
echo "✓ Offline CI tests passed."

# 5. Live MQTT tests (optional)
if $LIVE_MODE; then
  echo ""
  echo "=== 5. Live Mode D: Component Parity ==="
  python3 test_component_parity.py
  echo "✓ Component parity tests passed."

  echo ""
  echo "=== 6. Live Mode C: Shadow Integrator ==="
  python3 test_shadow_integrator.py
  echo "✓ Shadow integrator tests passed."
fi

echo ""
echo "========================================="
echo "✅ PRESUBMIT VALIDATION COMPLETE"
echo "========================================="
exit 0
