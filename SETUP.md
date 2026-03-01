# Development Setup Guide

This guide walks you through setting up your local environment to compile, simulate, and flash the HAPSIC controller onto an M5Stack StamPLC (ESP32).

## 1. System Requirements

HAPSIC is developed identically across C++ and Python. You will need:
- **Python 3.11** or **3.12**
- A Unix-based terminal (macOS or Linux) is strongly recommended for executing the `.sh` test scripts.

## 2. Installing the Toolchain

### A. Python & Simulation Dependencies
The HAPSIC testing suite natively executes the `hapsic.py` physics simulator. 
We strongly recommend using a virtual environment.

```bash
# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install the Python code style Linter 
pip install ruff
```

### B. ESPHome (Native Compilation)
ESPHome acts as our C++ compiler and firmware packager. It takes our YAML configurations and `.cpp` logic and translates them into an ESP32 executable.

```bash
pip install esphome
```

### C. C++ Static Analysis (Optional but Recommended)
To contribute to the core `hapsic.cpp` engine, you must format your C++ code.
- **macOS (Homebrew)**: `brew install clang-format`
- **Ubuntu/Debian**: `sudo apt-get install clang-format`

---

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
./run_all_tests.sh
```

If the console outputs `✅ [SUCCESS] Native C++ and Python Simulator Traces completely match!`, your development environment is fully operational.
