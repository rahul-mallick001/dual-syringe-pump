# Dual Axis Syringe Pump Controller

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/tests-122%20passed-brightgreen.svg)]()
[![Hardware](https://img.shields.io/badge/hardware-Holmarc%20Dual%20Axis-orange.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A modern, high-precision, web-based control application and serial protocol driver for dual-axis microfluidic syringe pumps (specifically engineered for Holmarc Dual Syringe Pump hardware).

This system replaces legacy proprietary Windows software with a cross-platform, asynchronous Python architecture featuring simultaneous dual-rack dispensing, multi-step sequential programming, interactive mathematical curve/waveform presets, real-time hardware limit-switch monitoring, sub-second continuous velocity ramping, and a responsive web dashboard.

---

## Table of Contents

- [Key Features](#key-features)
- [System Architecture](#system-architecture)
- [Mathematical Model & Protocol Fidelity](#mathematical-model--protocol-fidelity)
  - [Flow Rate & Prescaler Calculations](#flow-rate--prescaler-calculations)
  - [Hardware Protocol & Wire Framing](#hardware-protocol--wire-framing)
  - [Centisecond Sub-Second Timing & Backlash Elimination](#centisecond-sub-second-timing--backlash-elimination)
- [Hardware Setup & Wiring](#hardware-setup--wiring)
- [Installation & Quickstart](#installation--quickstart)
- [Web Interface Overview](#web-interface-overview)
  - [1. Dual Pump Control (Simultaneous)](#1-dual-pump-control-simultaneous)
  - [2. Multi-Step Program (Sequential)](#2-multi-step-program-sequential)
  - [3. Curve & Waveform Presets (Dynamic Profiling)](#3-curve--waveform-presets-dynamic-profiling)
- [REST API Reference](#rest-api-reference)
- [Test Suite & Verification](#test-suite--verification)
- [Project Directory Structure](#project-directory-structure)
- [License & Authors](#license--authors)

---

## Key Features

- **Simultaneous Dual-Pump Control**: Control Channel A (Connector 1 / Rack 1) and Channel B (Connector 2 / Rack 2) independently with dedicated syringe inner diameters, flow rates, and infusion targets.
- **Multi-Step Sequential Programs**: Configure and chain complex infusion/withdrawal sequences across multiple steps with custom flow rates, durations, and directional toggles. Compatible with standard `.txt` program scripts.
- **Interactive Curve & Waveform Presets**:
  - **8 Parametric Presets**: Constant, Linear Ramp, Shifted Sine, Shifted Cosine, Sawtooth, Triangle, Pulse / Step, and Custom Equations.
  - **Custom Bézier Splines**: Interactive piecewise cubic Bézier curves with draggable knots and tangent handles on real-time HTML5 canvases (double-click to add knot, right-click to remove).
  - **Safe AST Math Expressions**: Safely evaluate user-defined mathematical equations like `1.0 + 0.5 * sin(2 * pi * t / T)` using an AST-whitelisted evaluator.
  - **Dual-Channel Linking**: Support for Independent channels, Push-Pull sinusoidal oscillation, and linear scaling ratios ($B = k \cdot A$).
  - **Sub-Second Discretization ($10\text{ ms}$ Hardware Resolution)**: User-controlled time slicing ($0.05\text{ s}$–$10.0\text{ s}$) with quick-select pills (`0.1s`, `0.25s`, `0.5s`, `1.0s`, `2.0s`) for smooth speed ramping without staircase velocity jumps.
  - **Zero-Backlash Motion Pipeline**: Pre-buffered serial frame compilation and low-latency completion polling ($5\text{ ms}$) prevent motor stopping between slices, eliminating mechanical lead-screw de-tensioning and fluid pressure dips.
- **Physical Limit Switch Detection**: Real-time reading of microswitch limit sensors (`0x32`, `0x23`, `0x24`, `0x46`, `0x50`) for home position tracking and stall prevention.
- **Hardware Safety Interlocks**:
  - Independent homing for Pump 1 (`0x1A`), Pump 2 (`0x1B`), or both synchronously (`0x19`).
  - Latching Emergency Stop (`ESTOP`) with instantaneous serial motor halt (`0x14`).
  - Directional safety guards (blocks reverse withdrawal when parked at home limit switches).
  - Syringe capacity and swept volume limit guards.
- **Bit-for-Bit Protocol Fidelity**: Reverse-engineered microcontroller timer registers, 16-bit prescaler ladders (1, 8, 64, 256, 1024), and mechanism scaling verified against native firmware disassembly.
- **Modern Responsive Web UI**: Clean dashboard built with Vanilla JavaScript and semantic HTML5/CSS3. High-contrast themes, real-time countdown clocks, dynamic preview synchronizer, and zero framework bloat.
- **RESTful API**: Comprehensive API endpoints for laboratory automation, external robotics, and headless microfluidic workflows.

---

## System Architecture

```
+-----------------------------------------------------------------------------------+
|                                Web Browser UI                                     |
|  (Dual-Axis Dashboard, Multi-Step Editor, Curve & Waveform Presets, ESTOP Panel)  |
+-----------------------------------------------------------------------------------+
                                          |
                                    HTTP / JSON
                                (Polling & REST API)
                                          v
+-----------------------------------------------------------------------------------+
|                               FastAPI Server (app.py)                             |
|     Endpoints: /api/state, /api/run, /api/home, /api/curve/preview, /api/stop     |
+-----------------------------------------------------------------------------------+
                                          |
+-----------------------------------------------------------------------------------+
|                         Pump Engine State Machine (engine.py)                     |
|      - Threaded Execution Loop               - State & Limit Switch Cache         |
|      - Channel Coordination (A & B)          - Pre-buffered Slice Chaining        |
|      - Emergency Stop Latch                  - Curve Pipeline & Interpolator      |
+-----------------------------------------------------------------------------------+
                                          |
+-----------------------------------------------------------------------------------+
|                        Protocol Driver (device.py / protocol.py)                  |
|      - Binary Frame Construction             - Low-Latency Serial Polling (5ms)   |
|      - Channel-Specific Opcodes              - 0x32 Limit Sensor Decoding         |
|      - Centisecond (10ms) Duration Encoding  - Full Duplex Packet Handshake       |
+-----------------------------------------------------------------------------------+
                                          |
                                     RS-232 / USB
                                  (19200 Baud, 8N1)
                                          v
+-----------------------------------------------------------------------------------+
|                          Holmarc Microcontroller Hardware                         |
|      - Stepper Driver Axis 1 (A / Rack 1)    - Limit Microswitch Axis 1 (A)       |
|      - Stepper Driver Axis 2 (B / Rack 2)    - Limit Microswitch Axis 2 (B)       |
+-----------------------------------------------------------------------------------+
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
| `0x0A`   | `OPENER_PUMP_A` / `CMD_ACK_OK` | Packet start opener for Pump A & affirmative handshake response |
| `0x28`   | `OPENER_PUMP_B` / `CMD_LIMIT`  | Packet start opener for Pump B parameter frames |
| `0x05`   | `CMD_DEVICE`                   | Connection handshake probe |
| `0x0F`   | `TRIGGER_FORWARD`              | Start Forward (Infusion) motion for queued frames |
| `0x37`   | `TRIGGER_REVERSE`              | Start Reverse (Withdrawal) motion for queued frames |
| `0x14`   | `CMD_STOP`                     | Immediate emergency stop / halt for all active axes |
| `0x1A`   | `CMD_HOME_A`                   | Home Pump 1 / Rack A |
| `0x1B`   | `CMD_HOME_B`                   | Home Pump 2 / Rack B |
| `0x19`   | `CMD_HOME_BOTH`                | Home both pumps simultaneously |
| `0x1E`   | `ACK_ALL_DONE`                 | All active programs / motion steps completed |
| `0x1F`   | `ACK_PUMP_A_DONE_FWD`          | Pump A forward infusion motion complete |
| `0x20`   | `ACK_PUMP_B_DONE_FWD`          | Pump B forward infusion motion complete |
| `0x3C`   | `ACK_PUMP_A_DONE_REV`          | Pump A reverse withdrawal motion complete |
| `0x4B`   | `ACK_PUMP_B_DONE_REV`          | Pump B reverse withdrawal motion complete |
| `0x32`   | `CMD_HOME_ACK` / `LIMIT`       | Microswitch contact closed (axis at home limit) |
| `0x23`   | `ACK_FAR_LIMIT_A`              | Pump A far limit sensor reached |
| `0x24`   | `ACK_FAR_LIMIT_B`              | Pump B far limit sensor reached |

#### 9-Byte Parameter Frame Structure

Each motion frame sends 9 bytes:

```
[OPENER] [TDSP_HI] [TDSP_LO] [DUR_3] [DUR_2] [DUR_1] [DUR_0] [PRESC_HI] [PRESC_LO]
```

- Byte 0: Opener (`0x0A` for Pump A, `0x28` for Pump B)
- Bytes 1-2: Timer counter value `tdsp` (16-bit big-endian unsigned short)
- Bytes 3-6: Duration in hundredths of a second ($t \times 100$, 32-bit big-endian unsigned int)
- Bytes 7-8: Hardware prescaler index (16-bit big-endian unsigned short)

### Centisecond Sub-Second Timing & Backlash Elimination

Because duration is encoded as centiseconds ($10\text{ ms}$ units), the controller natively accepts sub-second durations ($0.1\text{ s} \to 10$, $0.25\text{ s} \to 25$). 

During curve execution:
1. **Pre-buffered Frame Pipeline**: While slice $N$ is executing on the hardware, the software pre-builds the 9-byte packet for slice $N+1$.
2. **Low-Latency Polling ($5\text{ ms}$)**: Completion monitoring uses $5\text{ ms}$ non-blocking reads instead of $50\text{ ms}$, reacting instantly when `ACK_ALL_DONE` arrives.
3. **Continuous Preload Preservation**: Because inter-slice serial transmission completes in $<5\text{ ms}$, the stepper motor does not stop and settle to zero velocity. Fluid backpressure cannot de-tension the lead-screw nut, eliminating the mechanical backlash bump and pressure drop seen in coarse discretization.

---

## Hardware Setup & Wiring

1. Connect the Dual Syringe Pump to your host computer via USB-B or FTDI RS-232 serial cable.
2. Ensure both actuator stepper cable connectors are securely latched:
   - **Connector 1**: Rack 1 / Pump 1 (Channel A)
   - **Connector 2**: Rack 2 / Pump 2 (Channel B)
3. Connect the 12V / 24V DC power supply to the pump base unit.
4. Verify that the FTDI driver assigns a virtual COM port (e.g., `COM6` on Windows or `/dev/ttyUSB0` on Linux). If needed, install drivers from the `drivers/` directory.

---

## Installation & Quickstart

### Prerequisites

- Python 3.10, 3.11, 3.12, 3.13, or 3.14
- FTDI Virtual COM Port Driver

### Windows One-Click Launcher

Double-click `start.bat` in the repository root. The launcher verifies the environment, starts the FastAPI server, and launches your browser.

### Manual Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/rahul-mallick001/dual-syringe-pump.git
   cd dual-syringe-pump
   ```

2. **Create and activate a virtual environment:**
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
   Navigate to [http://localhost:8077](http://localhost:8077) in any web browser.

---

## Web Interface Overview

The web dashboard is organized into three dedicated operating modes:

### 1. Dual Pump Control (Simultaneous)
- Independent parameter cards for **PUMP 1** and **PUMP 2**.
- Inner diameter ($\text{mm}$), Flow Rate, and Units ($\mu\text{L/hr}$, $\mu\text{L/min}$, $\text{mL/hr}$, $\text{mL/min}$).
- Target Mode selection by **Duration** (seconds, minutes, hours) or **Target Volume** ($\mu\text{L}$, $\text{mL}$).
- Synchronous countdown timers and live volume displays.

### 2. Multi-Step Program (Sequential)
- Up to 5 programmable steps executed sequentially.
- Per-step configuration of diameter, flow rate, duration, and individual infusion/withdrawal direction.
- Load and Save `.txt` program scripts compatible with legacy Holmarc files.

### 3. Curve & Waveform Presets (Dynamic Profiling)
- **Presets**: Constant, Linear Ramp, Shifted Sine, Shifted Cosine, Sawtooth, Triangle, Pulse/Step, or Custom Equation.
- **Interactive Canvas**: Drag-and-drop Bézier control knots, edit tangent handles, or convert any preset directly into Bézier knots with `→ Bézier`.
- **Time Discretization Control**: Numerical step input (`min="0.05" step="0.05"`) with quick-select pills (`0.1s`, `0.25s`, `0.5s`, `1.0s`, `2.0s`) and live resolution telemetry badges (e.g. `240 slices (4.0 Hz • 0.25s)`).
- **Syringe Volume & Stroke Metrics**: Real-time integration of total infused volume, total withdrawn volume, required initial fill, required headspace, and peak linear stroke ($\text{mm}$).

---

## REST API Reference

### Device State & Connection
- `GET /api/state` — Full telemetry: connection status, current COM port, active mode, channel parameters, homing states, and limit switch statuses.
- `POST /api/connect` — Connects to a serial port: `{"port": "COM6"}`.
- `POST /api/disconnect` — Closes active serial connection safely.
- `GET /api/ports` — Lists all available hardware COM ports.

### Motion Control
- `POST /api/mode` — Switches mode: `{"mode": "dual"}`, `{"mode": "steps"}`, or `{"mode": "curve"}`.
- `POST /api/pump` — Configures parameters for an individual pump rack.
- `POST /api/run` — Begins motion execution for active mode.
- `POST /api/stop` — Halts running motion cleanly.
- `POST /api/emergency-stop` — Triggers immediate emergency halt and latches safety state.
- `POST /api/home` — Runs homing routine: `{"target": "both"}` (`"A"` or `"B"`).
- `POST /api/withdrawal` — Sets direction mode: `{"value": false}` (infuse) or `{"value": true}` (withdraw).

### Curve & Waveform Presets
- `POST /api/curve/preview` — Evaluates and compiles dual-channel curve configuration without committing to run state. Returns high-resolution points, per-channel metrics, warnings, and compiled hardware slices.
- `POST /api/curve/config` — Saves curve configuration and recomputes preview.

### Multi-Step Programming
- `POST /api/step` — Updates a specific step in the sequential program.
- `POST /api/selected` — Selects which steps are executed: `{"selected": [true, true, false, false, false]}`.

---

## Test Suite & Verification

The project includes an automated test suite with **122 unit and integration tests** verifying protocol framing, speed formula arithmetic, safety guards, fake serial hardware simulation, curve math, and REST endpoints.

Run the test suite:

```bash
pytest tests -v
```

### Test Modules

- `tests/test_curves.py`: Mathematical curve evaluation, AST equation validation, Bézier evaluation, sub-second slicing, and centisecond hardware packet verification.
- `tests/test_preset_parameters.py`: Exhaustive validation of all 8 presets across diverse amplitude, frequency, phase, offset, duty cycle, push-pull, and unit configurations.
- `tests/test_dual.py`: Simultaneous dual-pump framing, dual homing, independent channel limits, and channel coordination.
- `tests/test_engine.py`: Threading, state machine transitions, timeout handling, and sub-second curve worker execution.
- `tests/test_fidelity.py`: Verification of prescaler ladders and mathematical models against original .NET IL disassembly.
- `tests/test_framing.py`: Binary packet encoding, sub-second centisecond duration conversion, and delimiter checks.
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
│   ├── curves.py           # Curve compilation, Bézier math, and AST equation evaluator
│   ├── device.py           # Low-latency serial driver, framing, and limit decoding
│   ├── engine.py           # State machine, threading, and zero-backlash curve pipeline
│   ├── program.py          # Math formulas, prescalers, and program step parser
│   ├── protocol.py         # Hardware opcode constants and 9-byte frame builders
│   ├── safety.py           # Input validation, bounds checks, and interlocks
│   └── probe.py            # Diagnostic protocol verification harness
├── static/                 # Frontend web dashboard
│   ├── index.html          # Semantic HTML5 dashboard layout
│   ├── style.css           # Custom responsive styling and theme variables
│   ├── app.js              # State polling, UI binding, and REST client
│   └── curves.js           # Interactive Bézier canvas visualizer & preset manager
├── examples/               # Sample multi-step program files
│   ├── sample_program.txt  # Standard 5-step infusion test
│   ├── dual_program.txt    # Dual-channel program example
│   └── alternating_program.txt # Infusion/withdrawal alternating steps
├── drivers/                # USB-to-Serial converter driver installers
│   ├── CDM212364_Setup_Win_11.exe # Windows 10 & 11 driver setup
│   └── CDM v2.12.00 WHQL Certified.exe # WHQL certified driver setup
└── tests/                  # Automated test suite (122 tests)
    ├── conftest.py / fake_pump.py  # Mock serial port hardware simulation
    ├── test_curves.py      # Mathematical curve and sub-second slice tests
    ├── test_preset_parameters.py # Parameter tests for all presets
    ├── test_dual.py        # Dual pump execution tests
    ├── test_engine.py      # State machine and curve worker tests
    ├── test_fidelity.py    # Firmware formula verification tests
    ├── test_framing.py     # Binary packet encoding and timing tests
    ├── test_probe.py       # Handshake probe tests
    ├── test_program.py     # Program file parsing and math tests
    ├── test_safety.py      # Safety interlocks and boundary tests
    └── test_webapp_api.py  # REST API integration tests
```

---

## License & Authors

Developed and maintained by **Rahul Mallick** ([@rahul-mallick001](https://github.com/rahul-mallick001)).

This project is licensed under the [MIT License](LICENSE).
