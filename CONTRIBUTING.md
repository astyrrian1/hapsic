# Contributing to HAPSIC

Welcome! We appreciate your interest in contributing to the Home Assistant Psychrometric Source Integrated Controller. 

Because HAPSIC controls physical hardware involving steam, condensation limits, and mechanical relays, we enforce strict, automated testing parity between our C++ physical engine and our Python simulation twin.

## Development Prerequisites

To contribute locally, you will need the following tools installed on your development machine (macOS/Linux):

1. **Python 3.11+**
2. **ESPHome** (`pip install esphome`)
3. **Ruff** (`pip install ruff`) - Enforces Python linting/formatting.
4. **Clang-Format & Clang-Tidy** - Enforces strict C++ styles (Google style).

## The Enterprise Regression Suite

The core philosophy of HAPSIC development is: **"If you touch the C++ engine, you must touch the Python engine. If you touch the Python engine, they both must output the exact same results."**

We enforce this through our master test script:

```bash
./run_all_tests.sh
```

### What does `run_all_tests.sh` do?
1. **Linting**: Executes `ruff check` and `clang-format` to guarantee code style compliance across the repository.
2. **Configuration Validation**: Runs `esphome config` against our payload YAMLs to ensure Home Assistant/ESPHome syntax is valid.
3. **Python Scenario Simulation**: 
    - Executes `scenario_builder.py` to generate thousands of points of mocked CSV sensor data (simulating sensor failures, extreme temperatures, target spikes).
    - Runs `scenario_tester.py` to funnel that CSV data into the Python model (`hapsic.py`) and records the exact FSM boundaries and DAC voltage decisions continuously over time.
4. **C++ Native Simulation**:
    - Compiles the actual `hapsic.cpp` engine using ESPHome natively on your machine (via `tests_scenarios.yaml`).
    - Executes the built C++ binary, injecting the exact same CSV data points.
5. **Trace Parity Comparison**:
    - Executes `run_compare.py` to assert that the Python JSON trace and the C++ JSON trace are 100% identical. 

If a change introduces a deviation and a mismatch between Python and C++, or breaks logic, the script exits with code `1`.

## Adding New Features / Bug Fixes

1. **Branch**: Create a new branch `git checkout -b feature/your-feature`.
2. **Write the Logic**: Implement your psychrometric change in **both** `components/hapsic/hapsic.cpp` and `hapsic.py`.
3. **Write the Test**: Always add a test simulation boundary.
    - Open `scenario_builder.py` and write a new function (e.g., `add_your_edge_case(data)`).
    - Append ticks defining the sensor inputs, time delta, and expected output voltage behavior.
4. **Verify**: Run `./run_all_tests.sh` locally. 
5. **Commit**: Push your code. Our GitHub Actions pipeline will automatically spin up Ubuntu runners, matrix build across Python versions, execute the entire test suite, and upload the trace logs as artifacts to your Pull Request. 

## Code Quality Standards
- **C++**: Follow the `.clang-format` rules. Do not introduce raw `#include` paths that break ESP32/Host agnostic compilations.
- **Python**: Ensure `ruff format .` completes without modifying your files before pushing.
- **Units**: Absolutely NO imperial values. All psychrometric engines calculate natively in degrees Celsius (`C`), Grams per Kilogram (`g/kg`), and Kilopascals (`kPa`).
