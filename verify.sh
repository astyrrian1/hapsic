#!/bin/bash
set -e

echo "Validating ESPHome configuration..."
esphome config stamplc.yaml

echo "Compiling ESPHome firmware..."
esphome clean stamplc.yaml
esphome compile stamplc.yaml

echo "Verification complete!"
