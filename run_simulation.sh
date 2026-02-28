#!/bin/bash
set -e

echo "Starting Hapsic Simulation on Host..."
echo "Press Ctrl+C to stop."

# Run ESPHome with the host configuration
esphome run tests.yaml
