# Dual Axis Syringe Pump Controller

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/tests-93%20passed-brightgreen.svg)]()
[![Hardware](https://img.shields.io/badge/hardware-Holmarc%20Dual%20Axis-orange.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A modern, high-precision, web-based control application and serial protocol driver for dual-axis microfluidic syringe pumps (specifically engineered for Holmarc Dual Syringe Pump hardware).

This system replaces legacy proprietary Windows software with a cross-platform, asynchronous Python architecture featuring simultaneous dual-rack dispensing, multi-step sequential programming, real-time hardware limit-switch monitoring, and a responsive web dashboard.

---

## Table of Contents

- [Key Features](#key-features)
- [System Architecture](#system-architecture)
- [Mathematical Model & Protocol Fidelity](#mathematical-model--protocol-fidelity)
  - [Flow Rate & Prescaler Calculations](#flow-rate--prescaler-calculations)
  - [Hardware Protocol & Wire Framing](#hardware-protocol--wire-framing)
- [Hardware Setup & Wiring](#hardware-setup--wiring)
- [Installation & Quickstart](#installation--quickstart)
- [Web Interface Overview](#web-interface-overview)
- [REST API Reference](#rest-api-reference)
- [Test Suite & Verification](#test-suite--verification)
- [Project Directory Structure](#project-directory-structure)
- [License & Authors](#license--authors)

---

## Key Features

- **Simultaneous Dual-Pump Control**: Control Channel A (Connector 1 / Rack 1) and Channel B (Connector 2 / Rack 2) independently with dedicated syringe inner diameters, flow rates, and infusion targets.
- **Multi-Step Sequential Programs**: Configure and chain complex infusion/withdrawal sequences across multiple steps with custom flow rates and step durations.
- **Save & Load Programs**: Compatible with standard multi-step program text files (`.txt`).
- **Physical Limit Switch Detection**: Real-time reading of microswitch limit sensors (`0x32` status responses) for home position tracking and stall prevention.
- **Hardware Safety Interlocks**:
  - Independent homing for Pump 1, Pump 2, or both synchronously.
  - Latching Emergency Stop (`ESTOP`) with instant serial motor halt.
  - Directional safety guards (prevents withdrawal when parked at home limit switches).
  - Validation bounds for syringe dimensions, volume targets, and flow rate limits.
- **Bit-for-Bit Protocol Fidelity**: Reverse-engineered microcontroller timer registers, 16-bit prescaler ladders (1, 8, 64, 256, 1024), and mechanism scaling verified against native firmware disassembly.
- **Modern Responsive Web UI**: Clean dashboard built with Vanilla JavaScript and semantic HTML5/CSS3. Zero clunky frameworks, no margin clipping, with real-time countdown timers and hardware status badges.
- **RESTful API**: Comprehensive API endpoints for laboratory automation, external robotics, and headless microfluidic workflows.

---

## System Architecture

```
+-----------------------------------------------------------------------+
|                         Web Browser UI                                |
|        (Dual-Axis Dashboard, Multi-Step Editor, Emergency Stop)       |
+-----------------------------------------------------------------------+
                                   |
                             HTTP / JSON
                         (Polling & REST API)
                                   v
+-----------------------------------------------------------------------+
|                         FastAPI Server (app.py)                       |
|           Endpoints: /api/state, /api/run, /api/home, /api/stop       |
+-----------------------------------------------------------------------+
                                   |
+-----------------------------------------------------------------------+
|                    Pump Engine State Machine (engine.py)               |
|      - Threaded Execution Loop         - State & Limit Switch Cache   |
|      - Channel Coordination (A & B)    - Emergency Stop Latch         |
+-----------------------------------------------------------------------+
                                   |
+-----------------------------------------------------------------------+
|                     Protocol Driver (device.py / protocol.py)         |
|      - Binary Frame Construction       - Bounded Read Timeouts        |
|      - Channel-Specific Opcodes        - 0x32 Limit Sensor Decoding   |
+-----------------------------------------------------------------------+
                                   |
                              RS-232 / USB
                          (19200 Baud, 8N1)
                                   v
+-----------------------------------------------------------------------+
|                   Holmarc Microcontroller Hardware                    |
|      - Stepper Driver Axis 1 (A)       - Limit Microswitch Axis 1     |
|      - Stepper Driver Axis 2 (B)       - Limit Microswitch Axis 2     |
+-----------------------------------------------------------------------+
```

---

## Mathematical Model & Protocol Fidelity

### Flow Rate & Prescaler Calculations

The on-board microcontroller converts target volumetric flow rates into a timer period count and a prescaler index based on physical lead-screw pitch, gear reduction, and syringe barrel cross-section:

$$\text{tdsp}_{\text{raw}} = \frac{8143 \times D^2}{Q_{\text{scaled}}}$$

Where:
- $D$ is the inner diameter of the syringe barrel in millimeters ($\text{mm}$).
- $Q$ is the desired flow rate, normalized by unit factors:
  - $\mu\text{L/hr}$: $Q \times 100$
  - $\mu\text{L/min}$: $Q \times \frac{100}{60}$
  - $\text{mL/hr}$: $\frac{Q}{10}$
  - $\text{mL/min}$: $\frac{Q}{600}$
- Mechanism calibration scale factor: $6.18 \times \text{prescaler}$

#### Microcontroller Prescaler Ladder

The microcontroller timer register is 16-bit ($0 \le \text{tdsp} \le 65535$). The software iterates through available hardware prescalers until the integer fits:

| Prescaler Value | Hardware Register Index | Range / Condition |
|:---------------:|:-----------------------:|:------------------|
| **1**           | `0x0001`                | $\text{tdsp} \le 65535$ |
| **8**           | `0x0002`                | $\text{tdsp} / 8 \le 65535$ |
| **64**          | `0x0003`                | $\text{tdsp} / 64 \le 65535$ |
| **256**         | `0x0004`                | $\text{tdsp} / 256 \le 65535$ |
| **1024**        | `0x0005`                | High-volume / low-speed fallback |

### Hardware Protocol & Wire Framing

Communication operates at **19200 baud, 8 data bits, no parity, 1 stop bit (8N1)** over a standard FTDI USB-to-Serial interface.

#### Protocol Byte Map

| Hex Byte | Symbol | Description |
|:--------:|:-------|:------------|
| `0x0A`   | `CMD_ACK_OK` / `HEADER` | Packet start delimiter and affirmative response |
| `0x05`   | `CMD_DEVICE`            | Connection handshake probe |
| `0x28`   | `CMD_PROG_START`        | Arms controller for parameter frame ingestion |
| `0x0F`   | `CMD_PROG_END`          | Frame end delimiter |
| `0x14`   | `CMD_STOP`              | Immediate halt for all active axes |
| `0x1A`   | `CMD_HOME_A`            | Command Rack 1 (Pump 1) to reverse to home switch |
| `0x1B`   | `CMD_HOME_B`            | Command Rack 2 (Pump 2) to reverse to home switch |
| `0x37`   | `CMD_RUN_A`             | Execute motion for Rack 1 (Pump 1) |
| `0x19`   | `CMD_RUN_B`             | Execute motion for Rack 2 (Pump 2) |
| `0x32`   | `STATUS_HOME_REACHED`   | Microswitch contact closed (axis at home limit) |
| `0x16`   | `MODE_INFUSION`         | Forward motion (infusion direction) |
| `0x17`   | `MODE_WITHDRAWAL`       | Reverse motion (withdrawal direction) |

#### 10-Byte Parameter Frame Structure

Each motion sequence sends a 10-byte parameter packet:

```
[0x0A] [0x83] [T_HI] [T_LO] [P_HI] [P_LO] [D_B3] [D_B2] [D_B1] [0x0F]
```

- Byte 0: Header (`0x0A`)
- Byte 1: Channel/Config identifier (`0x83`)
- Bytes 2-3: Timer counter value `tdsp` (16-bit big-endian unsigned short)
- Bytes 4-5: Hardware prescaler index (16-bit big-endian unsigned short)
- Bytes 6-8: Total step duration in hundredths of a second ($t \times 100$, 24-bit big-endian)
- Byte 9: Footer (`0x0F`)

---

## Hardware Setup & Wiring

1. Connect the Dual Syringe Pump to your host computer via the USB-B or FTDI RS-232 serial cable.
2. Ensure both actuator stepper cable connectors are securely latched:
   - **Connector 1**: Rack 1 / Pump 1 (Channel A)
   - **Connector 2**: Rack 2 / Pump 2 (Channel B)
3. Connect the 12V / 24V DC power supply to the pump base unit.
4. Verify that the FTDI driver assigns a virtual COM port (e.g., `COM6` on Windows or `/dev/ttyUSB0` on Linux). If the device is not detected automatically, install the driver using `drivers/CDM212364_Setup_Win_11.exe`.

---

## Installation & Quickstart

### Prerequisites

- Python 3.10, 3.11, 3.12, 3.13, or 3.14
- FTDI Virtual COM Port Driver (usually installed automatically by Windows Update)

### Automated Windows Launcher

Double-click `start.bat` in the repository root. The script checks your environment, launches the web application server, and displays the local URL.

### Manual Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/rahul-mallick001/dual-syringe-pump.git
   cd dual-syringe-pump
   ```

2. **Create and activate a virtual environment (optional but recommended):**
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # Linux / macOS:
   source .venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the server:**
   ```bash
   python app.py
   ```

5. **Open the Web UI:**
   Navigate to [http://localhost:8077](http://localhost:8077) in any modern web browser.

---

## Web Interface Overview

The web dashboard is organized into intuitive, high-visibility control surfaces:

- **Top Navigation Bar**: Live COM port selector, dynamic refresh/rescan button, and Connect/Disconnect toggle.
- **Dual Pump Control (Simultaneous)**:
  - Separate parameter cards for **PUMP 1** and **PUMP 2**.
  - Checkboxes to toggle individual pump activation.
  - Inputs for Syringe Diameter ($\text{mm}$), Flow Rate, and Units ($\mu\text{L/hr}$, $\mu\text{L/min}$, $\text{mL/hr}$, $\text{mL/min}$).
  - Target Mode selection by **Duration** (seconds, minutes, hours) or **Target Volume** ($\mu\text{L}$, $\text{mL}$).
  - Live calculated duration and countdown timer.
- **Multi-Step Program (Sequential)**:
  - Step table editor supporting up to 5 programmable steps.
  - Per-step configuration of diameter, flow rate, duration, and direction.
  - File toolbar to **Load** and **Save** `.txt` program scripts.
- **Global Control Toolbar**:
  - `EMERGENCY STOP`: High-priority red button for instantaneous software and hardware halt.
  - `INFUSION` / `WITHDRAWAL`: Direction toggle with safety interlocks.
  - `Home Both`, `Home 1`, `Home 2`: Dedicated homing routines with microswitch feedback.
  - `Run` and `Stop`: Primary motion triggers.
- **Live Hardware Telemetry**: Real-time status badges displaying home switch states (`0x32`), active channel execution, and communication link status.

---

## REST API Reference

The FastAPI backend provides full programmatic control for automated laboratory workflows.

### Device State & Connection
- `GET /api/state`  
  Returns full telemetry: connection status, current COM port, active mode, channel parameters, homing states, and limit switch statuses.
- `POST /api/connect`  
  Connects to a specified serial port: `{"port": "COM6"}`.
- `POST /api/disconnect`  
  Closes the active serial connection safely.
- `GET /api/ports`  
  Lists all available hardware COM ports detected on the system.

### Motion Control
- `POST /api/pump`  
  Configures parameters for an individual pump rack:
  ```json
  {
    "axis": "A",
    "diameter": 20.0,
    "flowRate": 10.0,
    "flowRateUnit": 3,
    "durationType": 0,
    "duration": 60.0,
    "durationUnit": 0,
    "enabled": true
  }
  ```
- `POST /api/mode`  
  Switches controller between simultaneous dual-pump mode and sequential multi-step mode:
  ```json
  {"mode": "dual"}  // or "steps"
  ```
- `POST /api/run`  
  Begins motion execution for active configuration:
  ```json
  {"mode": "dual"}
  ```
- `POST /api/stop`  
  Halts running motion cleanly.
- `POST /api/emergency-stop`  
  Triggers immediate emergency halt and latches the safety state until cleared.
- `POST /api/home`  
  Runs homing routine for specified axis:
  ```json
  {"target": "both"}  // or "A", "B"
  ```
- `POST /api/withdrawal`  
  Sets direction mode: `{"value": false}` for infusion, `{"value": true}` for withdrawal.

### Multi-Step Programming
- `POST /api/step`  
  Updates a specific step in the sequential program:
  ```json
  {
    "index": 0,
    "diameter": 20.0,
    "flowRate": 5.0,
    "flowRateUnit": 3,
    "duration": 30.0,
    "withdrawal": false
  }
  ```
- `POST /api/selected`  
  Selects which program steps are executed: `{"selected": [true, true, false, false, false]}`.

---

## Test Suite & Verification

The project includes an extensive test suite with **93 automated unit and integration tests** verifying protocol framing, speed formula arithmetic, safety guards, fake serial hardware simulation, and REST endpoints.

To run the complete test suite:

```bash
pytest tests -v
```

### Test Modules

- `tests/test_dual.py`: Simultaneous dual-pump framing, dual homing, and channel coordination.
- `tests/test_engine.py`: Threading, state machine transitions, timeout handling, and stop execution.
- `tests/test_fidelity.py`: Verification of prescaler ladders and mathematical models against original .NET IL disassembly.
- `tests/test_framing.py`: Packet encoding, 16-bit/32-bit big-endian conversions, and delimiter checks.
- `tests/test_safety.py`: Parameter bounds, volume thresholds, emergency stop guards, and home switch interlocks.
- `tests/test_webapp_api.py`: FastAPI endpoint tests using `TestClient` and simulated serial hardware.
- `tests/fake_pump.py`: High-fidelity software mock of the Holmarc microcontroller serial firmware.

---

## Project Directory Structure

```
dual-syringe-pump/
├── app.py                  # FastAPI web server and REST API application
├── requirements.txt        # Python package dependencies
├── start.bat               # Windows one-click startup batch script
├── .gitignore              # Git ignore rules for Python, caches, and IDEs
├── README.md               # Complete project documentation
├── pump/                   # Core Python control package
│   ├── __init__.py         # Package initialization
│   ├── device.py           # Serial port driver and low-level framing
│   ├── engine.py           # State machine, threading, and pump orchestration
│   ├── program.py          # Math formulas, prescalers, and step parsing
│   ├── protocol.py         # Hardware opcode constants and frame builders
│   ├── safety.py           # Input validation, bounds checks, and interlocks
│   └── probe.py            # Diagnostic protocol verification harness
├── static/                 # Frontend web application
│   ├── index.html          # Semantic HTML5 dashboard layout
│   ├── style.css           # Custom responsive styling (no framework bloat)
│   └── app.js              # State polling, UI binding, and REST client
├── examples/               # Sample multi-step program files
│   ├── sample_program.txt  # Standard 5-step infusion test
│   ├── dual_program.txt    # Dual-channel program example
│   └── alternating_program.txt # Infusion/withdrawal alternating steps
├── drivers/                # USB-to-Serial converter driver installers
│   ├── CDM212364_Setup_Win_11.exe # Windows 10 & 11 driver setup
│   └── CDM v2.12.00 WHQL Certified.exe # WHQL certified driver setup
└── tests/                  # Automated test suite (93 tests)
    ├── conftest.py / fake_pump.py  # Mock serial port hardware simulation
    ├── test_dual.py        # Dual pump execution tests
    ├── test_engine.py      # State machine tests
    ├── test_fidelity.py    # Firmware formula verification tests
    ├── test_framing.py     # Binary packet encoding tests
    ├── test_probe.py       # Handshake probe tests
    ├── test_program.py     # Program file parsing and math tests
    ├── test_safety.py      # Safety interlocks and boundary tests
    └── test_webapp_api.py  # REST API integration tests
```

---

## License & Authors

Developed and maintained by **Rahul Mallick** ([@rahul-mallick001](https://github.com/rahul-mallick001)).

This project is licensed under the [MIT License](LICENSE).
