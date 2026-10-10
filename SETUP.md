# Development Setup Guide

This guide walks you through setting up your local environment to compile, simulate, and flash the HAPSIC controller onto an M5Stack StamPLC (ESP32).

## 1. System Requirements

Use Python **3.14.3** for tests and Python **3.13.1** for ESPHome, matching CI.
A C++ compiler and a Unix shell (macOS or Linux) are required for native tests.

## 2. Installing the Toolchain

Create two environments from the repository root. ESPHome pins MQTT client 1.x;
the optional live harness uses MQTT client 2.x, so they cannot share an environment.

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
python3.13 -m venv .venv-firmware
.venv-firmware/bin/python -m pip install -r requirements-firmware.txt
git config core.hooksPath .githooks
source .venv/bin/activate
export PATH="$PWD/scripts:$PATH"
```

The checked-in ESPHome wrapper uses `.venv-firmware`. Presubmit selects it
automatically and requires the pre-push hook. Formatter and build-tool versions
are pinned, and production/desk external components use immutable revisions.
The Python controller source is `apps/hapsic-controller/hapsic_controller.py`.

## 3. Configuring the Workstation

### Resolving Secrets
ESPHome requires a `secrets.yaml` file to compile the firmware (specifically for WiFi definitions). We do not commit secrets to GitHub.

1. Make a copy of the example template:
   ```bash
   cp secrets.yaml.example secrets.yaml
   ```
2. Open `secrets.yaml` and enter your WiFi credentials and your Home Assistant API token. 
*(Note: Be aware that `secrets.yaml` is ignored by `.gitignore` safely).*

---

## 4. Hardware: The M5Stack StamPLC

HAPSIC specifically targets the **M5Stack StamPLC**. This industrial ESP32 DIN-rail module contains specific hardware that our configuration (`stamplc.yaml`) expects:
- **AW9523**: I2C IO Expander mapping physical relays.
- **MCP4725**: I2C DAC controllers creating the explicit 0-10V control signal mappings for the Steam and Fan.

### A. Flashing via USB (Initial Setup)

To deploy the production engine to a physical StamPLC module for the absolute first time:

1. Connect the StamPLC to your computer via USB-C.
2. Ensure you have the [CH9102 Serial Drivers](https://docs.m5stack.com/en/core/stamplc) installed if your OS doesn't immediately recognize the device.
3. Use ESPHome to compile and flash the firmware via terminal:
   ```bash
   esphome run stamplc.yaml
   ```
4. Follow the prompt to select the serial port (e.g., `/dev/tty.usbserial-xxx`). ESPHome will natively compile the C++ classes and push the binary to the chip.

### B. Flashing Over-The-Air (WiFi Upgrades)

Once the initial firmware (and `secrets.yaml` WiFi credentials) are successfully flashed via USB, the module will broadcast an OTA endpoint accessible on your local network.

For all subsequent upgrades, you no longer need a physical USB connection:

1. Ensure your laptop/workstation is on the same local network as the StamPLC.
2. Run the exact same compilation command:
   ```bash
   esphome run stamplc.yaml
   ```
3. When prompted, select the **`Over-The-Air`** option instead of the physical USB serial port. ESPHome will compile the payload and seamlessly stream the binary via WiFi to update the controller.

---

## 5. Validating the Installation

Before writing custom code or deploying the device into a mechanical room, verify that your local toolchain is 100% compliant with the Enterprise Regression logic spanning Python and C++:

```bash
# Provide executable permissions to scripts
chmod +x *.sh

# Execute the master test gateway
bash run_presubmit.sh
```

The complete gate runs lint, pytest, production/desk firmware compilation, native
simulation, and the standalone suites. Production and desk builds start with
`esphome clean`, so the gate requires dependency downloads and recompilation.
M5GFX 0.2.19 is pinned to its official Git commit in component code generation;
do not add duplicate library versions in the device YAML files.
Presence-only simulation checks are smoke coverage, not proof of mathematical
or physical safety equivalence.
