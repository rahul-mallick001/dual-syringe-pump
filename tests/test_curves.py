"""Unit and API tests for the Dual-Syringe Curve & Waveform Preset engine."""

from __future__ import annotations

import math
import pytest
from fastapi.testclient import TestClient

from app import app
from pump import curves


def test_safe_equation_valid():
    val = curves.evaluate_equation("1.5 + 0.5 * sin(2 * pi * t / T)", t=15.0, total_T=60.0)
    assert math.isclose(val, 2.0, rel_tol=1e-6)

    val2 = curves.evaluate_equation("2 * u^2 + 1", t=30.0, total_T=60.0)
    assert math.isclose(val2, 1.5, rel_tol=1e-6)


@pytest.mark.parametrize(
    "bad_expr",
    [
        "__import__('os').system('echo hi')",
        "open('/etc/passwd')",
        "(lambda x: x)(1)",
        "unknown_var + 1",
        "t.__class__",
        "",
    ],
)
def test_safe_equation_rejects_unsafe(bad_expr: str):
    with pytest.raises(ValueError):
        curves.evaluate_equation(bad_expr, t=1.0, total_T=10.0)


def test_bezier_evaluation():
    knots = [
        {"t": 0.0, "y": 0.0, "outX": 20.0, "outY": 0.0},
        {"t": 60.0, "y": 6.0, "inX": 40.0, "inY": 6.0},
    ]
    assert math.isclose(curves.evaluate_bezier_at_t(knots, 0.0), 0.0, abs_tol=1e-6)
    assert math.isclose(curves.evaluate_bezier_at_t(knots, 30.0), 3.0, abs_tol=1e-3)
    assert math.isclose(curves.evaluate_bezier_at_t(knots, 60.0), 6.0, abs_tol=1e-6)


def test_presets_shifted_sine_and_cosine():
    s0 = curves.evaluate_preset("shifted_sine", 0.0, 60.0, amplitude=1.0, cycles=1.0, offset=1.0)
    c0 = curves.evaluate_preset("shifted_cosine", 0.0, 60.0, amplitude=1.0, cycles=1.0, offset=1.0)
    assert math.isclose(s0, 1.0, abs_tol=1e-6)
    assert math.isclose(c0, 2.0, abs_tol=1e-6)


def test_velocity_mode_volume_metrics_and_cross_channel_link():
    cfg = {
        "yMode": "velocity",
        "unit": "mL/min",
        "timeUnit": "s",
        "duration": 60,
        "sliceDurationS": 10,
        "link": {"mode": "B_from_A", "factor": -0.5},
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
    # 2 mL/min for 60s = 2.0 mL infused on Pump A
    assert math.isclose(res["metricsA"]["totalInfusedMl"], 2.0, rel_tol=1e-2)
    assert math.isclose(res["metricsA"]["requiredInitialFillMl"], 2.0, rel_tol=1e-2)
    # Pump B = -0.5 * Pump A => -1.0 mL/min for 60s = 1.0 mL withdrawn
    assert math.isclose(res["metricsB"]["totalWithdrawnMl"], 1.0, rel_tol=1e-2)
    assert math.isclose(res["metricsB"]["requiredHeadroomMl"], 1.0, rel_tol=1e-2)
    # Independent directions in compiled slices: A infusing (False), B withdrawing (True)
    sl0 = res["_compiled_slices"][0]
    assert sl0.active_a is True and sl0.withdrawal_a is False
    assert sl0.active_b is True and sl0.withdrawal_b is True


def test_displacement_mode_mm_unit():
    cfg = {
        "yMode": "displacement",
        "unit": "mm",
        "timeUnit": "s",
        "duration": 60,
        "sliceDurationS": 10,
        "channelA": {
            "enabled": True,
            "diameter": 20.0,
            "source": "preset",
            "preset": "linear",
            "amplitude": 10.0,
            "offset": 0.0,
        },
        "channelB": {"enabled": False, "diameter": 20.0, "source": "preset", "preset": "constant", "amplitude": 0.0},
    }
    res = curves.compile_curve_config(cfg)
    # 10 mm stroke in 20 mm diameter syringe => pi * 10^2 * 10 mm^3 = 3141.59 uL = 3.1416 mL
    assert math.isclose(res["metricsA"]["peakStrokeMm"], 10.0, rel_tol=1e-2)
    assert math.isclose(res["metricsA"]["totalInfusedMl"], math.pi, rel_tol=1e-2)


def test_api_curve_preview_and_config():
    client = TestClient(app)
    payload = {
        "yMode": "velocity",
        "unit": "mL/min",
        "timeUnit": "s",
        "duration": 30,
        "sliceDurationS": 5,
        "channelA": {
            "enabled": True,
            "diameter": 14.5,
            "source": "equation",
            "equation": "1.0 + 0.5 * sin(2 * pi * t / T)",
        },
        "channelB": {
            "enabled": True,
            "diameter": 14.5,
            "source": "equation",
            "equation": "1.0 + 0.5 * cos(2 * pi * t / T)",
        },
    }
    r1 = client.post("/api/curve/preview", json=payload)
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["sliceCount"] == 6
    assert len(body1["times"]) == 121

    r2 = client.post("/api/curve/config", json=payload)
    assert r2.status_code == 200
    assert r2.json()["preview"]["sliceCount"] == 6


def test_subsecond_slicing_and_hardware_packet():
    from pump import protocol
    cfg = {
        "yMode": "velocity",
        "unit": "mL/min",
        "timeUnit": "s",
        "duration": 5.0,
        "sliceDurationS": 0.25,
        "channelA": {
            "enabled": True,
            "diameter": 14.5,
            "source": "preset",
            "preset": "shifted_sine",
            "amplitude": 1.0,
            "offset": 1.0,
        },
        "channelB": {
            "enabled": False,
            "diameter": 14.5,
        },
    }
    res = curves.compile_curve_config(cfg)
    assert res["sliceDurationS"] == 0.25
    assert res["sliceCount"] == 20
    assert len(res["slices"]) == 20
    sl0 = res["_compiled_slices"][0]
    assert math.isclose(sl0.duration_s, 0.25, abs_tol=1e-4)
    assert sl0.cmd_a is not None
    assert math.isclose(sl0.cmd_a.duration_step, 0.25, abs_tol=1e-4)

    # Verify wire frame encodes 0.25s as 25 centiseconds (0x00000019)
    frame = protocol.build_pump_frame(
        axis="A",
        tdsp=sl0.cmd_a.tdsp,
        duration_seconds=sl0.cmd_a.duration_step,
        prescaler=sl0.cmd_a.prescaler,
    )
    assert frame[0] == protocol.OPENER_PUMP_A
    # Bytes 3,4,5,6 encode 32-bit duration_packet: 25 -> 0x00, 0x00, 0x00, 0x19
    assert frame[3:7] == [0x00, 0x00, 0x00, 0x19]


def test_ultrasmooth_0_1s_slicing_and_hardware_packet():
    from pump import protocol
    cfg = {
        "yMode": "velocity",
        "unit": "mL/min",
        "timeUnit": "s",
        "duration": 2.0,
        "sliceDurationS": 0.1,
        "channelA": {
            "enabled": True,
            "diameter": 14.5,
            "source": "preset",
            "preset": "linear",
            "amplitude": 2.0,
            "offset": 0.5,
        },
        "channelB": {
            "enabled": True,
            "diameter": 14.5,
            "source": "preset",
            "preset": "linear",
            "amplitude": 1.0,
            "offset": 0.2,
        },
    }
    res = curves.compile_curve_config(cfg)
    assert res["sliceDurationS"] == 0.1
    assert res["sliceCount"] == 20
    sl0 = res["_compiled_slices"][0]
    assert math.isclose(sl0.duration_s, 0.1, abs_tol=1e-4)
    assert sl0.cmd_a is not None
    assert math.isclose(sl0.cmd_a.duration_step, 0.1, abs_tol=1e-4)

    # 0.10s * 100 = 10 centiseconds (0x0000000A)
    frame_a = protocol.build_pump_frame(
        axis="A",
        tdsp=sl0.cmd_a.tdsp,
        duration_seconds=sl0.cmd_a.duration_step,
        prescaler=sl0.cmd_a.prescaler,
    )
    assert frame_a[3:7] == [0x00, 0x00, 0x00, 0x0A]

