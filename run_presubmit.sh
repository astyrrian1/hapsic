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
  ruff check .
  echo "✓ Python lint clean."
else
  echo "⚠ ruff not installed. Install: brew install ruff"
  exit 1
fi

# 2. Lint: C++ (clang-format)
CLANG_FMT="/opt/homebrew/opt/llvm/bin/clang-format"
echo ""
echo "=== 2. C++ Format Check (clang-format) ==="
if [[ -x "$CLANG_FMT" ]]; then
  $CLANG_FMT --dry-run --Werror components/hapsic/*.cpp components/hapsic/*.h
  echo "✓ C++ formatting clean."
else
  echo "⚠ clang-format not found. Install: brew install llvm"
  exit 1
fi

# 3. ESPHome YAML validation
echo ""
echo "=== 3. ESPHome Configuration Validation ==="
esphome config stamplc.yaml > /dev/null
echo "✓ Production YAML valid."
esphome config stamplc_desk.yaml > /dev/null
echo "✓ Desk YAML valid."

# 4. ESPHome desk compilation
echo ""
echo "=== 4. ESPHome Desk Firmware Compilation ==="
esphome compile stamplc_desk.yaml
echo "✓ Desk firmware compiled successfully."

# 5. Full offline CI suite
echo ""
echo "=== 5. Offline CI Test Suite ==="
bash run_all_tests.sh
echo "✓ Offline CI tests passed."

# 6. Live MQTT tests (optional)
if $LIVE_MODE; then
  echo ""
  echo "=== 6. Live Mode D: Component Parity ==="
  python3 test_component_parity.py
  echo "✓ Component parity tests passed."

  echo ""
  echo "=== 7. Live Mode C: Shadow Integrator ==="
  python3 test_shadow_integrator.py
  echo "✓ Shadow integrator tests passed."
fi

echo ""
echo "========================================="
echo "✅ PRESUBMIT VALIDATION COMPLETE"
echo "========================================="
exit 0
