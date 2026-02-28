#!/bin/bash
set -e

echo "Compiling Native C++ Simulation for the runner..."
esphome compile tests_scenarios.yaml

echo "Executing Native Binary..."
rm -f cpp_scenarios.txt
./.esphome/build/hapsic-scenarios/.pioenvs/hapsic-scenarios/program > cpp_scenarios.txt 2>&1
