"""Systematic test suite for all Curve & Waveform Presets with different parameters."""

from __future__ import annotations

import math
import time
import pytest

from pump import curves
from pump.device import PumpDevice
from pump.engine import PumpController
try:
    from fake_pump import FakeFactory
except ImportError:
    from tests.fake_pump import FakeFactory


# ---------------------------------------------------------------------------
# 1. Linear Ramp Preset
# ---------------------------------------------------------------------------
def test_linear_ramp_parameters():
    # Accelerating infusion: 0.5 -> 2.5 mL/min over 60s (avg 1.5 mL/min = 1.5 mL)
    cfg_pos = {
        "yMode": "velocity",
        "unit": "mL/min",
        "timeUnit": "s",
        "duration": 60,
        "sliceDurationS": 5,
        "channelA": {
            "enabled": True,
            "diameter": 14.5,
            "source": "preset",
            "preset": "linear",
            "amplitude": 2.0,
            "offset": 0.5,
        },
        "channelB": {"enabled": False},
    }
    res = curves.compile_curve_config(cfg_pos)
    assert res["sliceCount"] == 12
    # At t=0, flow = 0.5; at t=60, flow = 2.5
    assert math.isclose(res["valuesA"][0], 0.5, abs_tol=1e-3)
    assert math.isclose(res["valuesA"][-1], 2.5, abs_tol=1e-3)
    assert math.isclose(res["metricsA"]["totalInfusedMl"], 1.5, rel_tol=1e-2)
    assert math.isclose(res["metricsA"]["peakFlowMlMin"], 2.5, abs_tol=1e-2)

    # Decelerating / descending ramp: 3.0 -> 0.0 mL/min over 30s
    cfg_neg = {
        "yMode": "velocity",
        "unit": "mL/min",
        "timeUnit": "s",
        "duration": 30,
        "sliceDurationS": 2,
        "channelA": {
            "enabled": True,
            "diameter": 14.5,
            "source": "preset",
            "preset": "linear",
            "amplitude": -3.0,
            "offset": 3.0,
        },
        "channelB": {"enabled": False},
    }
    res_neg = curves.compile_curve_config(cfg_neg)
    assert math.isclose(res_neg["valuesA"][0], 3.0, abs_tol=1e-3)
    assert math.isclose(res_neg["valuesA"][-1], 0.0, abs_tol=1e-3)
    # Average flow 1.5 mL/min for 0.5 min = 0.75 mL
    assert math.isclose(res_neg["metricsA"]["totalInfusedMl"], 0.75, rel_tol=1e-2)


# ---------------------------------------------------------------------------
# 2. Shifted Sine Preset
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cycles,phase_deg,expected_endpoints", [
    (1.0, 0, (1.0, 1.0)),       # 1 full cycle sin(0) -> sin(2pi) = 0 -> y = 1.0
    (1.0, 90, (2.0, 2.0)),      # sin(90°) = 1.0 -> y = 2.0
    (2.5, 0, (1.0, 1.0)),       # 2.5 cycles: at t=0 is 1.0, at t=T is 1.0
    (0.5, 0, (1.0, 1.0)),       # Half cycle, peak at u=0.5 -> y=2.0
])
def test_shifted_sine_parameters(cycles, phase_deg, expected_endpoints):
    cfg = {
        "yMode": "velocity",
        "unit": "mL/min",
        "timeUnit": "s",
        "duration": 60,
        "sliceDurationS": 2,
        "channelA": {
            "enabled": True,
            "diameter": 14.5,
            "source": "preset",
            "preset": "shifted_sine",
            "amplitude": 1.0,
            "cycles": cycles,
            "phaseDeg": phase_deg,
            "offset": 1.0,
        },
        "channelB": {"enabled": False},
    }
    res = curves.compile_curve_config(cfg)
    assert math.isclose(res["valuesA"][0], expected_endpoints[0], abs_tol=1e-2)
    assert math.isclose(res["valuesA"][-1], expected_endpoints[1], abs_tol=1e-2)
    # For full cycles with offset=1.0, total infused over 1 min = 1.0 mL
    if cycles in (1.0, 2.0):
        assert math.isclose(res["metricsA"]["totalInfusedMl"], 1.0, rel_tol=1e-2)


# ---------------------------------------------------------------------------
# 3. Shifted Cosine Preset
# ---------------------------------------------------------------------------
def test_shifted_cosine_parameters():
    cfg = {
        "yMode": "velocity",
        "unit": "mL/min",
        "timeUnit": "s",
        "duration": 60,
        "sliceDurationS": 2,
        "channelA": {
            "enabled": True,
            "diameter": 14.5,
            "source": "preset",
            "preset": "shifted_cosine",
            "amplitude": 1.5,
            "cycles": 2.0,
            "phaseDeg": 0,
            "offset": 2.0,
        },
        "channelB": {"enabled": False},
    }
    res = curves.compile_curve_config(cfg)
    # cos(0) = 1 -> 2.0 + 1.5 = 3.5; min = 2.0 - 1.5 = 0.5
    assert math.isclose(res["valuesA"][0], 3.5, abs_tol=1e-3)
    assert math.isclose(max(res["valuesA"]), 3.5, abs_tol=1e-3)
    assert math.isclose(min(res["valuesA"]), 0.5, abs_tol=1e-3)


# ---------------------------------------------------------------------------
# 4. Sawtooth Preset
# ---------------------------------------------------------------------------
def test_sawtooth_parameters():
    cfg = {
        "yMode": "velocity",
        "unit": "mL/min",
        "timeUnit": "s",
        "duration": 60,
        "sliceDurationS": 2,
        "channelA": {
            "enabled": True,
            "diameter": 14.5,
            "source": "preset",
            "preset": "sawtooth",
            "amplitude": 2.0,
            "cycles": 3.0,
            "offset": 0.0,
        },
        "channelB": {"enabled": False},
    }
    res = curves.compile_curve_config(cfg)
    assert math.isclose(res["valuesA"][0], 0.0, abs_tol=1e-3)
    assert max(res["valuesA"]) > 1.8
    # Average flow of sawtooth amp=2 from 0 is 1.0 mL/min -> 1.0 mL total
    assert math.isclose(res["metricsA"]["totalInfusedMl"], 1.0, rel_tol=0.08)


# ---------------------------------------------------------------------------
# 5. Triangle Preset
# ---------------------------------------------------------------------------
def test_triangle_parameters():
    cfg = {
        "yMode": "velocity",
        "unit": "mL/min",
        "timeUnit": "s",
        "duration": 60,
        "sliceDurationS": 2,
        "channelA": {
            "enabled": True,
            "diameter": 14.5,
            "source": "preset",
            "preset": "triangle",
            "amplitude": 4.0,
            "cycles": 1.0,
            "offset": 1.0,
        },
        "channelB": {"enabled": False},
    }
    res = curves.compile_curve_config(cfg)
    # Starts at offset (1.0), peaks at midpoint (1.0 + 4.0 = 5.0), ends at offset (1.0)
    assert math.isclose(res["valuesA"][0], 1.0, abs_tol=1e-3)
    mid_idx = len(res["valuesA"]) // 2
    assert math.isclose(res["valuesA"][mid_idx], 5.0, abs_tol=0.1)
    assert math.isclose(res["valuesA"][-1], 1.0, abs_tol=1e-3)


# ---------------------------------------------------------------------------
# 6. Pulse / Step Preset with Duty Cycle
# ---------------------------------------------------------------------------
def test_pulse_parameters():
    cfg = {
        "yMode": "velocity",
        "unit": "mL/min",
        "timeUnit": "s",
        "duration": 60,
        "sliceDurationS": 2,
        "channelA": {
            "enabled": True,
            "diameter": 14.5,
            "source": "preset",
            "preset": "pulse",
            "amplitude": 3.0,
            "cycles": 1.0,
            "offset": 1.0,
            "duty": 0.25,  # 25% high (4.0 mL/min), 75% baseline (1.0 mL/min)
        },
        "channelB": {"enabled": False},
    }
    res = curves.compile_curve_config(cfg)
    # First 25% should be 4.0, remaining 75% should be 1.0
    first_quarter_val = res["valuesA"][10]
    last_quarter_val = res["valuesA"][-10]
    assert math.isclose(first_quarter_val, 4.0, abs_tol=1e-3)
    assert math.isclose(last_quarter_val, 1.0, abs_tol=1e-3)
    # Expected volume: 4.0 * 0.25 min + 1.0 * 0.75 min = 1.0 + 0.75 = 1.75 mL
    assert math.isclose(res["metricsA"]["totalInfusedMl"], 1.75, rel_tol=0.08)


# ---------------------------------------------------------------------------
# 7. Custom Equation Preset
# ---------------------------------------------------------------------------
def test_custom_equation_harmonics_and_exponential():
    # Sine with 2nd harmonic
    cfg_harm = {
        "yMode": "velocity",
        "unit": "mL/min",
        "timeUnit": "s",
        "duration": 60,
        "sliceDurationS": 2,
        "channelA": {
            "enabled": True,
            "diameter": 14.5,
            "source": "equation",
            "equation": "1.0 + 0.5 * sin(2 * pi * u) + 0.25 * sin(4 * pi * u)",
        },
        "channelB": {"enabled": False},
    }
    res = curves.compile_curve_config(cfg_harm)
    assert math.isclose(res["valuesA"][0], 1.0, abs_tol=1e-3)
    assert math.isclose(res["valuesA"][-1], 1.0, abs_tol=1e-3)

    # Exponential decay equation
    cfg_exp = {
        "yMode": "velocity",
        "unit": "mL/min",
        "timeUnit": "s",
        "duration": 60,
        "sliceDurationS": 2,
        "channelA": {
            "enabled": True,
            "diameter": 14.5,
            "source": "equation",
            "equation": "2.0 * exp(-3 * u) + 0.2",
        },
        "channelB": {"enabled": False},
    }
    res_exp = curves.compile_curve_config(cfg_exp)
    assert math.isclose(res_exp["valuesA"][0], 2.2, abs_tol=1e-3)
    # At u=1, 2.0*e^-3 + 0.2 = 2.0*0.0498 + 0.2 = ~0.2996
    assert math.isclose(res_exp["valuesA"][-1], 0.2996, abs_tol=1e-2)


# ---------------------------------------------------------------------------
# 8. Cross-Channel Push-Pull (Sin/Cos with k = -1)
# ---------------------------------------------------------------------------
def test_cross_channel_push_pull_k_minus_one():
    cfg = {
        "yMode": "velocity",
        "unit": "mL/min",
        "timeUnit": "s",
        "duration": 60,
        "sliceDurationS": 5,
        "link": {"mode": "B_from_A", "factor": -1.0},
        "channelA": {
            "enabled": True,
            "diameter": 14.5,
            "source": "preset",
            "preset": "constant",
            "amplitude": 2.0,
            "offset": 0.0,
        },
        "channelB": {
            "enabled": True,
            "diameter": 14.5,
            "source": "preset",
            "preset": "constant",
            "amplitude": 0.0,
            "offset": 0.0,
        },
    }
    res = curves.compile_curve_config(cfg)
    # Pump A: 2 mL/min infuse -> 2.0 mL total
    assert math.isclose(res["metricsA"]["totalInfusedMl"], 2.0, rel_tol=1e-2)
    assert res["metricsA"]["hasInfusion"] is True
    assert res["metricsA"]["hasWithdrawal"] is False

    # Pump B: -2 mL/min withdraw -> 2.0 mL total withdrawn
    assert math.isclose(res["metricsB"]["totalWithdrawnMl"], 2.0, rel_tol=1e-2)
    assert res["metricsB"]["hasInfusion"] is False
    assert res["metricsB"]["hasWithdrawal"] is True

    # Slices verify independent hardware directions
    for sl in res["_compiled_slices"]:
        assert sl.active_a is True and sl.withdrawal_a is False
        assert sl.active_b is True and sl.withdrawal_b is True


# ---------------------------------------------------------------------------
# 9. Displacement Mode with Millimeter (Stroke) Units
# ---------------------------------------------------------------------------
def test_displacement_mode_stroke_units():
    # 20 mm stroke in 14.5 mm diameter syringe over 60s
    # Area = pi * (14.5/2)^2 = 165.13 mm^2
    # 20 mm stroke = 165.13 * 20 mm^3 = 3302.6 uL = 3.303 mL
    cfg = {
        "yMode": "displacement",
        "unit": "mm",
        "timeUnit": "s",
        "duration": 60,
        "sliceDurationS": 5,
        "channelA": {
            "enabled": True,
            "diameter": 14.5,
            "source": "preset",
            "preset": "linear",
            "amplitude": 20.0,
            "offset": 0.0,
        },
        "channelB": {"enabled": False},
    }
    res = curves.compile_curve_config(cfg)
    assert math.isclose(res["metricsA"]["peakStrokeMm"], 20.0, abs_tol=1e-2)
    assert math.isclose(res["metricsA"]["totalInfusedMl"], 3.303, rel_tol=1e-2)


# ---------------------------------------------------------------------------
# 10. End-to-End Motor Controller Execution Test
# ---------------------------------------------------------------------------
def test_end_to_end_controller_preset_execution():
    fake = FakeFactory(silent=False)
    ctrl = PumpController(
        device_factory=lambda port: PumpDevice(port=port, serial_factory=fake, ack_timeout_ms=50, connect_timeout_ms=50)
    )
    ctrl.connect("COM_TEST")
    assert ctrl.connected is True

    # Configure a 4-second 2-slice curve
    cfg = {
        "yMode": "velocity",
        "unit": "mL/min",
        "timeUnit": "s",
        "duration": 4,
        "sliceDurationS": 2,
        "channelA": {
            "enabled": True,
            "diameter": 14.5,
            "source": "preset",
            "preset": "shifted_sine",
            "amplitude": 1.0,
            "offset": 1.0,
        },
        "channelB": {
            "enabled": True,
            "diameter": 14.5,
            "source": "preset",
            "preset": "shifted_cosine",
            "amplitude": 1.0,
            "offset": 1.0,
        },
    }
    ctrl.set_curve_config(cfg)
    ctrl.run(mode="curve")

    # Monitor run to completion
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        st = ctrl.state()
        if not st["running"]:
            break
        time.sleep(0.05)

    final_state = ctrl.state()
    assert final_state["error"] is None
    assert final_state["status"] == "Program completed"
    assert final_state["racks"]["A"]["status"] == "Program Completed"
    assert final_state["racks"]["B"]["status"] == "Program Completed"
