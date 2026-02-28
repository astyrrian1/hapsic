Here is the definitive, hardware-focused Product Requirements Document (PRD) for the HAPSIC 2.2 Gold Master platform. All software logic, finite state machines, and PI loops have been removed. This document serves exclusively as the physical build, electrical integration, and firmware addressing specification.

---

# **HARDWARE PLATFORM SPECIFICATION: HAPSIC 2.2 Gold Master**

**Target Base:** M5Stack StamPLC (ESP32-S3, K141). *Note: The AC Expansion Module is strictly excluded.*
**Base Physical Units:** SI Native (Celsius, m³/h, kPa, %RH). *Note: Home Assistant (HA) presentation layer will handle all Imperial conversions.*
**Atmospheric Constant:** 88.6 kPa.

---

### **1. CORE HARDWARE BILL OF MATERIALS (BOM)**

* **Logic Controller:** 1x M5Stack StamPLC (ESP32-S3 Base Unit).
* **Power Supply:** 1x Mean Well HDR-30-24 (24V DC, 1.5A, 36W, DIN-Rail).
* **I2C Multiplexer:** 1x M5Stack Unit PaHUB (TCA9548A).
* **Digital Sensor:** 1x M5Stack SHT3x Unit (with sintered/PTFE mesh cap).
* **Analog Sensor:** 1x BAPI BA/H200-D-BB (Industrial Duct Humidity Transmitter, 0-10V Output).
* **Analog-to-Digital Converter:** 1x M5Stack Voltmeter Unit (ADS1115-based).
* **Digital-to-Analog Converters:** 2x M5Stack DAC Units (MCP4725).
* **Distribution:** DIN-rail terminal blocks (24V Red, GND Black) and bootlace wire ferrules.

---

### **2. POWER DISTRIBUTION & GROUNDING**

**Objective:** Establish a unified, single-power domain to prevent ground loops and ensure analog 0-10V signal integrity without galvanic isolation.

* **AC Mains Input:** 120V/240V AC Line and Neutral terminate directly and exclusively at the bottom `L` and `N` terminals of the Mean Well HDR-30-24.
* **DC Distribution (24V Bus):**
* Mean Well `+V` connects to a dedicated +24V DIN-rail terminal block bus.
* Mean Well `-V` connects to a dedicated GND DIN-rail terminal block bus.


* **Device Power Runs:**
* **StamPLC:** Draws 24V power directly from the terminal block bus to its primary DC input terminals.
* **BAPI Sensor:** Draws 24V excitation power directly from the terminal block bus (BAPI `V+` to 24V bus, BAPI `GND` to GND bus).
* *Result:* The BAPI analog signal ground and the StamPLC logic ground are physically identical.



---

### **3. I2C BUS TOPOLOGY & ADDRESS MAP**

**Objective:** Provide physical routing and address definitions to isolate duplicate DAC hardware.

* **I2C Trunk:** StamPLC Port A (Red Grove connector).
* **SDA:** GPIO13
* **SCL:** GPIO15
* **Bus Frequency:** 100kHz


* **Multiplexer (PaHUB):** Connected directly to Port A.
* **Hardware Address:** `0x70`



| Physical Port | Hardware Connected | Device Chip | I2C Address | Signal Type / Range |
| --- | --- | --- | --- | --- |
| **PaHUB Chan 0** | Steam DAC Output | MCP4725 | `0x60` | Output: 0.0V to 10.0V DC |
| **PaHUB Chan 1** | Fan DAC Output | MCP4725 | `0x60` | Output: 0.0V to 10.0V DC |
| **PaHUB Chan 2** | Digital Duct Sensor | SHT3x | `0x44` | Input: Native Temp / %RH |
| **PaHUB Chan 3** | Analog Voltmeter | ADS1115 | `0x49` | Input: 0.0V to 10.0V DC |

---

### **4. INPUTS (SENSORS & TELEMETRY)**

#### **4.1 Digital Duct Sensor (SHT3x)**

* **Role:** Primary Duct Temperature, Standby Duct RH.
* **Routing:** PaHUB Channel 2.
* **Data Structure:** Native I2C float values.

#### **4.2 Analog Duct Sensor (BAPI via Voltmeter)**

* **Role:** Primary Duct RH (Master Process Variable for Safety Ceiling).
* **Routing:** PaHUB Channel 3.
* **Wiring:** BAPI 0-10V Humidity output terminal connected to Voltmeter positive input. Voltmeter negative input connected to the shared DIN-rail GND bus.
* **Unused Signal:** BAPI 0-10V Temperature output is capped and isolated.
* **Firmware Scaling Requirement:** The raw voltage read by the ADS1115 (`A0_GND`) must be scaled in firmware such that **0.0V DC = 0.0% RH** and **10.0V DC = 100.0% RH**.

#### **4.3 Zehnder ERV Telemetry (CAN Bus)**

* **Role:** Mass airflow data ingress for feed-forward calculations and economizer interlock.
* **Physical Port:** StamPLC PWR-CAN connector.
* **Topology:** Direct, point-to-point connection.
* **Wiring:** Twisted pair. StamPLC `CAN_H` directly to Zehnder `CAN_H`. StamPLC `CAN_L` directly to Zehnder `CAN_L`.
* **Termination Requirement:** The 120Ω hardware termination resistor *must* be physically engaged on the StamPLC PCB (via internal DIP switch or solder bridge) to prevent signal reflection.
* **Bus Parameters:** 50kbps Baud Rate, CAN ID: 4.
* **Required Endpoints:** `supply_air_flow` (m³/h), `extract_air_flow` (m³/h), `bypass_state` (%).

#### **4.4 Control Setpoint Ingress (Network API)**

* **Protocol:** ESPHome Native API.
* **Entity Type:** Template Number (allows HA to push float values to the PLC).
* **Variable:** Target Room Dew Point (Range: 30.0 to 60.0).

---

### **5. OUTPUTS (ACTUATORS)**

#### **5.1 Steam Humidifier Command (DAC 1)**

* **Role:** Proportional 0-10V demand signal to Aprilaire 801 modulating input.
* **Routing:** PaHUB Channel 0.
* **Wiring:** DAC 1 `VOUT` to Aprilaire 0-10V In (+). DAC 1 `GND` to Aprilaire 0-10V In (-).
* **Zero-State Interlock:** The physical safety relay has been removed from the design. The firmware must command exactly **0.0V** to this DAC to enforce a steam shutdown/safety cut.

#### **5.2 Zehnder Fan Override Command (DAC 2)**

* **Role:** Proportional 0-10V fan speed override signal to Zehnder Option Box.
* **Routing:** PaHUB Channel 1.
* **Wiring:** DAC 2 `VOUT` to Zehnder Option Box Analog In (+). DAC 2 `GND` to Zehnder Option Box Analog In (-).
* **Zero-State Protocol:** Commanding **0.0V** releases the token back to the Zehnder for its native auto-schedule.

---

Would you like me to map out a DIN-rail panel spacing guide based on the physical footprint of these specific M5Stack and Mean Well components?