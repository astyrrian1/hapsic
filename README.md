# HAPSIC (Home Assistant Psychrometric Source Integrated Controller)

HAPSIC is an advanced, physics-based controller designed to run natively on the ESP32 (via ESPHome). It actively calculates complex thermodynamic states (e.g., Dew Point, Mixing Ratios) in real-time to precisely control a steam humidifier via a 0-10V DAC, ensuring optimal target humidity in an HVAC duct without risking condensation or mold.

## The Problem
Standard humidistats simply turn on when Relative Humidity drops below a setpoint. This is dangerous for steam injection into HVAC ducts; if the duct temperature isn't hot enough, or the mass flow rate isn't high enough, injecting steam blindly will exceed the saturation point, causing the duct to rain condensation inside your walls.

HAPSIC solves this with math.

## Installation

### Option 1: HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to **Automation** → **⋮** (three dots) → **Custom repositories**
3. Add `astyrrian1/hapsic` with category **AppDaemon**
4. Click **Install**
5. Restart AppDaemon

### Option 2: Manual

1. Copy `apps/hapsic_controller/hapsic_controller.py` into your AppDaemon `apps/hapsic_controller/` directory
2. Configure your `apps.yaml` with the appropriate entity IDs
3. Restart AppDaemon

## Dashboard

HAPSIC ships with a **Mission Control** dashboard for monitoring the physics engine, duct safety, and moisture balance in real time.

**Prerequisites** (install via HACS → Frontend):
- [Mushroom Cards](https://github.com/piitaya/lovelace-mushroom)
- [ApexCharts Card](https://github.com/RomRider/apexcharts-card)
- [Power Flow Card Plus](https://github.com/flixlix/power-flow-card-plus)
- [card-mod](https://github.com/thomasloven/lovelace-card-mod)

**To install:**
1. In Home Assistant, go to **Settings → Dashboards → Add Dashboard**
2. Open the new dashboard, click **⋮** → **Edit Dashboard** → **⋮** → **Raw configuration editor**
3. Paste the contents of [`dashboards/mission-control.yaml`](dashboards/mission-control.yaml)
4. Save

## Architecture & Components

HAPSIC is uniquely designed with a strict **Local-First, Edge-Computed** philosophy. The core intelligence lives directly on the microcontroller, ensuring physics boundaries are maintained safely, regardless of WiFi connectivity or Home Assistant crashes. 

However, we maintain a mathematically identical "Digital Twin" in Python for state-of-the-art testing.

### 1. The C++ Psychrometric Engine (`components/hapsic/`)
The native firmware compiled by ESPHome:
- Handles floating-point Magnus-Tetens formula calculations for vapor pressure and dew point.
- Evaluates the mass balance (translating Target Dewpoint into sensible Grams of Steam per Kg of Air).
- Utilizes a complex Finite State Machine (FSM) governing anti-short-cycling, proportional bounding, safety relay triggers, and graceful 30-minute sensor-failure fallback mechanics.

### 2. The Python Digital Twin (`apps/hapsic_controller/hapsic_controller.py`)
A mathematically identical, 1:1 replica of the C++ thermodynamic engine and FSM, executed entirely natively in Python.
- Instead of taking 20 minutes to flash a microcontroller to test a new proportional threshold, DevOps/engineers can run weeks of simulated time through the Python twin in **milliseconds**.

### 3. Home Assistant & Zehnder Integration (`stamplc.yaml`)
HAPSIC binds gracefully into Home Assistant payloads. 
- It reads data off a Zehnder ComfoAir Q ERV (Supply Temp, Extract Humidity, air flows) alongside physical Duct Sensors to compute real-time safety thresholds over Modbus/CAN bus.
- Prioritizes a **3-Tier fallback logic**: House HA Variables -> Zehnder CAN variables -> 30-minute Cached Lock -> Safe Hardware Shutdown.

## Hardware Support
Designed specifically for the M5Stack StamPLC (ESP32), driving:
- `MCP4725` I2C DACs for 0-10V Steam and Fan modulation.
- `AW9523` I2C Expander for physical safety relay switching.

## Development & Testing
HAPSIC enforces a strict Enterprise-Grade CI/CD pipeline. No Pull Request can be merged unless both the C++ firmware and the Python simulator traces match their calculations **flawlessly**.

- See **[SETUP.md](SETUP.md)** to configure your local toolchain spanning Python, ESPHome, Clang-format, and preparing your M5Stack StamPLC device via USB.
- See **[CONTRIBUTING.md](CONTRIBUTING.md)** for a comprehensive guide on running the test suites, generating simulations, and contributing to the engine.
- See **[CHANGELOG.md](CHANGELOG.md)** for release history.
