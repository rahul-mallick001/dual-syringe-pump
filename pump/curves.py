"""Curve & waveform preset engine for the Dual Syringe Pump.

Supports:
- Cubic Bezier curves (tone-curve style, monotonic in time)
- Parametric waveform presets (linear, constant, sawtooth, triangle,
  shifted sine, shifted cosine, pulse)
- Safe AST-based custom mathematical equation evaluation (no eval())
- Displacement <-> Velocity conversion via numerical differentiation/integration
- Cross-channel numeric scaling (Pump 2 = k * Pump 1 or vice versa)
- Unit-aware syringe volume, fill, headroom, and plunger stroke calculations
- Compilation into time-sliced hardware StepCommands with independent per-axis
  direction control
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass, field
from typing import Any

from . import safety
from .program import (
    DurationType,
    DurationUnit,
    FlowRateUnit,
    ProgramData,
    StepCommand,
    calculate_values,
)

# ---------------------------------------------------------------------------
# Constants & unit conversion helpers
# ---------------------------------------------------------------------------
TIME_TO_SECONDS = {
    "s": 1.0,
    "min": 60.0,
    "hr": 3600.0,
}

DISPLACEMENT_UNITS = ("uL", "mL", "mm")
VELOCITY_UNITS = ("uL/min", "mL/min", "uL/hr", "mL/hr", "mm/s")


def syringe_area_mm2(diameter_mm: float) -> float:
    """Cross-sectional area of a syringe barrel in mm^2 (1 mm^3 = 1 uL)."""
    d = max(0.0, float(diameter_mm))
    return math.pi * (d * 0.5) ** 2


def displacement_to_ml(value: float, unit: str, diameter_mm: float) -> float:
    """Convert a displacement value in (uL, mL, mm) to mL."""
    if unit == "mL":
        return float(value)
    if unit == "uL":
        return float(value) / 1000.0
    if unit == "mm":
        area = syringe_area_mm2(diameter_mm)
        return (float(value) * area) / 1000.0
    raise ValueError(f"unsupported displacement unit: {unit}")


def ml_to_displacement(ml: float, unit: str, diameter_mm: float) -> float:
    """Convert mL to a displacement value in (uL, mL, mm)."""
    if unit == "mL":
        return float(ml)
    if unit == "uL":
        return float(ml) * 1000.0
    if unit == "mm":
        area = syringe_area_mm2(diameter_mm)
        if area <= 0:
            return 0.0
        return (float(ml) * 1000.0) / area
    raise ValueError(f"unsupported displacement unit: {unit}")


def velocity_to_ml_per_s(value: float, unit: str, diameter_mm: float) -> float:
    """Convert a velocity/flow-rate value to mL/s."""
    v = float(value)
    if unit == "mL/min":
        return v / 60.0
    if unit == "mL/hr":
        return v / 3600.0
    if unit == "uL/min":
        return v / 1000.0 / 60.0
    if unit == "uL/hr":
        return v / 1000.0 / 3600.0
    if unit == "mm/s":
        area = syringe_area_mm2(diameter_mm)
        return (v * area) / 1000.0
    raise ValueError(f"unsupported velocity unit: {unit}")


def ml_per_s_to_velocity(ml_s: float, unit: str, diameter_mm: float) -> float:
    """Convert mL/s to the requested velocity unit."""
    if unit == "mL/min":
        return ml_s * 60.0
    if unit == "mL/hr":
        return ml_s * 3600.0
    if unit == "uL/min":
        return ml_s * 1000.0 * 60.0
    if unit == "uL/hr":
        return ml_s * 1000.0 * 3600.0
    if unit == "mm/s":
        area = syringe_area_mm2(diameter_mm)
        if area <= 0:
            return 0.0
        return (ml_s * 1000.0) / area
    raise ValueError(f"unsupported velocity unit: {unit}")


# ---------------------------------------------------------------------------
# Safe AST-based math equation evaluator
# ---------------------------------------------------------------------------
_SAFE_FUNCS: dict[str, Any] = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "sqrt": math.sqrt,
    "abs": abs,
    "min": min,
    "max": max,
    "floor": math.floor,
    "ceil": math.ceil,
    "round": round,
    "pow": pow,
    "sign": lambda x: -1.0 if x < 0 else (1.0 if x > 0 else 0.0),
    "mod": lambda a, b: math.fmod(a, b),
}

_SAFE_CONSTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}

_BIN_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}

_UNARY_OPS = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}


def compile_equation(expr: str) -> ast.Expression:
    """Parse and validate a mathematical expression into a safe AST."""
    cleaned = (expr or "").strip()
    if not cleaned:
        raise ValueError("Equation cannot be empty")
    if len(cleaned) > 500:
        raise ValueError("Equation is too long (max 500 characters)")
    # Allow ^ as exponentiation convenience
    cleaned = cleaned.replace("^", "**")
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid equation syntax: {exc.msg}") from exc

    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load)):
            continue
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise ValueError("Only numeric constants are allowed in equations")
            continue
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            continue
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            continue
        if isinstance(node, ast.Name):
            if node.id in _SAFE_FUNCS or node.id in _SAFE_CONSTS or node.id in ("t", "T", "u"):
                continue
            raise ValueError(f"Unknown symbol '{node.id}' in equation")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_FUNCS:
                raise ValueError("Only whitelisted math functions are allowed")
            if node.keywords:
                raise ValueError("Keyword arguments are not supported in equations")
            continue
        if isinstance(node, tuple(_BIN_OPS.keys()) + tuple(_UNARY_OPS.keys())):
            continue
        raise ValueError(f"Disallowed expression element: {type(node).__name__}")

    return tree


def _eval_node(node: ast.AST, env: dict[str, float]) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, env)
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id in env:
            return float(env[node.id])
        if node.id in _SAFE_CONSTS:
            return _SAFE_CONSTS[node.id]
        raise ValueError(f"Unknown variable '{node.id}'")
    if isinstance(node, ast.UnaryOp):
        return float(_UNARY_OPS[type(node.op)](_eval_node(node.operand, env)))
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, env)
        right = _eval_node(node.right, env)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("Exponent magnitude too large")
        return float(_BIN_OPS[type(node.op)](left, right))
    if isinstance(node, ast.Call):
        fn = _SAFE_FUNCS[node.func.id]  # type: ignore[union-attr]
        args = [_eval_node(arg, env) for arg in node.args]
        return float(fn(*args))
    raise ValueError(f"Unsupported AST node: {type(node).__name__}")


def evaluate_equation(expr: str, t: float, total_T: float) -> float:
    """Evaluate a validated equation at time t with total duration total_T."""
    tree = compile_equation(expr)
    u = (t / total_T) if total_T > 0 else 0.0
    val = _eval_node(tree, {"t": float(t), "T": float(total_T), "u": float(u)})
    if not math.isfinite(val):
        raise ValueError(f"Equation produced non-finite value at t={t:.3f}")
    return val


# ---------------------------------------------------------------------------
# Cubic Bezier evaluator (monotonic in t)
# ---------------------------------------------------------------------------
def _cubic_bezier_1d(p0: float, p1: float, p2: float, p3: float, s: float) -> float:
    inv = 1.0 - s
    return (
        inv * inv * inv * p0
        + 3.0 * inv * inv * s * p1
        + 3.0 * inv * s * s * p2
        + s * s * s * p3
    )


def evaluate_bezier_at_t(knots: list[dict[str, Any]], t: float) -> float:
    """Evaluate a piecewise cubic Bezier curve at time t.

    Each knot has:
      t: float (time coordinate)
      y: float (value coordinate)
      inX, inY: optional incoming handle coordinates
      outX, outY: optional outgoing handle coordinates
    """
    if not knots:
        return 0.0
    ordered = sorted(knots, key=lambda k: float(k["t"]))
    if len(ordered) == 1:
        return float(ordered[0]["y"])
    if t <= float(ordered[0]["t"]):
        return float(ordered[0]["y"])
    if t >= float(ordered[-1]["t"]):
        return float(ordered[-1]["y"])

    # Find enclosing segment
    seg_idx = 0
    for i in range(len(ordered) - 1):
        if float(ordered[i]["t"]) <= t <= float(ordered[i + 1]["t"]):
            seg_idx = i
            break

    k0 = ordered[seg_idx]
    k1 = ordered[seg_idx + 1]
    x0, y0 = float(k0["t"]), float(k0["y"])
    x3, y3 = float(k1["t"]), float(k1["y"])
    dt = x3 - x0
    if dt <= 1e-12:
        return y0

    # Default handles at 1/3 and 2/3 along the chord if omitted
    x1 = float(k0.get("outX", x0 + dt / 3.0))
    y1 = float(k0.get("outY", y0 + (y3 - y0) / 3.0))
    x2 = float(k1.get("inX", x3 - dt / 3.0))
    y2 = float(k1.get("inY", y3 - (y3 - y0) / 3.0))

    # Clamp handle X coordinates to [x0, x3] so x(s) is strictly monotonic
    x1 = max(x0, min(x3, x1))
    x2 = max(x0, min(x3, x2))

    # Binary search for parameter s in [0, 1] such that B_x(s) == t
    lo, hi = 0.0, 1.0
    for _ in range(32):
        mid = 0.5 * (lo + hi)
        xm = _cubic_bezier_1d(x0, x1, x2, x3, mid)
        if xm < t:
            lo = mid
        else:
            hi = mid
    s = 0.5 * (lo + hi)
    return _cubic_bezier_1d(y0, y1, y2, y3, s)


# ---------------------------------------------------------------------------
# Parametric waveform presets
# ---------------------------------------------------------------------------
def evaluate_preset(
    preset: str,
    t: float,
    total_T: float,
    amplitude: float = 1.0,
    cycles: float = 1.0,
    phase_deg: float = 0.0,
    offset: float = 0.0,
    duty: float = 0.5,
) -> float:
    """Evaluate a built-in waveform preset at time t in [0, total_T]."""
    u = (t / total_T) if total_T > 0 else 0.0
    p = (preset or "linear").strip().lower()
    phase_rad = math.radians(phase_deg)

    if p == "constant":
        return float(offset + amplitude)
    if p == "linear":
        return float(offset + amplitude * u)
    if p == "shifted_sine":
        return float(offset + amplitude * math.sin(2.0 * math.pi * cycles * u + phase_rad))
    if p == "shifted_cosine":
        return float(offset + amplitude * math.cos(2.0 * math.pi * cycles * u + phase_rad))
    if p == "sawtooth":
        cyc = (cycles * u + phase_deg / 360.0) % 1.0
        return float(offset + amplitude * cyc)
    if p == "triangle":
        cyc = (cycles * u + phase_deg / 360.0) % 1.0
        tri = 2.0 * cyc if cyc < 0.5 else 2.0 * (1.0 - cyc)
        return float(offset + amplitude * tri)
    if p == "pulse":
        cyc = (cycles * u + phase_deg / 360.0) % 1.0
        return float(offset + (amplitude if cyc < max(0.01, min(0.99, duty)) else 0.0))
    raise ValueError(f"unknown waveform preset: {preset}")


# ---------------------------------------------------------------------------
# Channel evaluation, linking, metrics, and slice compilation
# ---------------------------------------------------------------------------
@dataclass
class CompiledSlice:
    index: int
    start_s: float
    duration_s: float
    active_a: bool
    active_b: bool
    withdrawal_a: bool
    withdrawal_b: bool
    flow_a_ml_s: float
    flow_b_ml_s: float
    cmd_a: StepCommand | None
    cmd_b: StepCommand | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "startS": round(self.start_s, 3),
            "durationS": round(self.duration_s, 3),
            "activeA": self.active_a,
            "activeB": self.active_b,
            "withdrawalA": self.withdrawal_a,
            "withdrawalB": self.withdrawal_b,
            "flowAMlMin": round(self.flow_a_ml_s * 60.0, 5),
            "flowBMlMin": round(self.flow_b_ml_s * 60.0, 5),
        }


@dataclass
class ChannelMetrics:
    enabled: bool
    diameter_mm: float
    total_infused_ml: float = 0.0
    total_withdrawn_ml: float = 0.0
    net_volume_ml: float = 0.0
    required_initial_fill_ml: float = 0.0
    required_headroom_ml: float = 0.0
    min_syringe_capacity_ml: float = 0.0
    peak_stroke_mm: float = 0.0
    peak_flow_ml_min: float = 0.0
    has_infusion: bool = False
    has_withdrawal: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "diameterMm": round(self.diameter_mm, 3),
            "totalInfusedMl": round(self.total_infused_ml, 4),
            "totalWithdrawnMl": round(self.total_withdrawn_ml, 4),
            "netVolumeMl": round(self.net_volume_ml, 4),
            "requiredInitialFillMl": round(self.required_initial_fill_ml, 4),
            "requiredHeadroomMl": round(self.required_headroom_ml, 4),
            "minSyringeCapacityMl": round(self.min_syringe_capacity_ml, 4),
            "peakStrokeMm": round(self.peak_stroke_mm, 3),
            "peakFlowMlMin": round(self.peak_flow_ml_min, 4),
            "hasInfusion": self.has_infusion,
            "hasWithdrawal": self.has_withdrawal,
            "warnings": list(self.warnings),
        }


def _evaluate_raw_channel(
    ch_cfg: dict[str, Any],
    times_user: list[float],
    total_T_user: float,
) -> list[float]:
    source = str(ch_cfg.get("source", "bezier")).strip().lower()
    if source == "bezier":
        knots = ch_cfg.get("knots") or []
        return [evaluate_bezier_at_t(knots, t) for t in times_user]
    if source == "preset":
        preset = str(ch_cfg.get("preset", "linear"))
        amp = float(ch_cfg.get("amplitude", 1.0))
        cycles = float(ch_cfg.get("cycles", 1.0))
        phase = float(ch_cfg.get("phaseDeg", 0.0))
        offset = float(ch_cfg.get("offset", 0.0))
        duty = float(ch_cfg.get("duty", 0.5))
        return [
            evaluate_preset(preset, t, total_T_user, amp, cycles, phase, offset, duty)
            for t in times_user
        ]
    if source == "equation":
        expr = str(ch_cfg.get("equation", "0"))
        tree = compile_equation(expr)
        out: list[float] = []
        for t in times_user:
            u = (t / total_T_user) if total_T_user > 0 else 0.0
            val = _eval_node(tree, {"t": float(t), "T": float(total_T_user), "u": float(u)})
            if not math.isfinite(val):
                raise ValueError(f"Equation produced non-finite value at t={t:.3f}")
            out.append(val)
        return out
    raise ValueError(f"unknown curve source: {source}")


def _compute_channel_metrics_and_flows(
    ch_cfg: dict[str, Any],
    raw_values: list[float],
    times_s: list[float],
    y_mode: str,
    unit: str,
) -> tuple[ChannelMetrics, list[float], list[float]]:
    """Compute metrics, displacement profile (mL), and flow profile (mL/s)."""
    enabled = bool(ch_cfg.get("enabled", True))
    diameter = float(ch_cfg.get("diameter", 14.5))
    invert_dir = bool(ch_cfg.get("invertDirection", False))
    sign_mult = -1.0 if invert_dir else 1.0

    n = len(times_s)
    disp_ml: list[float] = [0.0] * n
    flow_ml_s: list[float] = [0.0] * n

    if y_mode == "displacement":
        disp_ml = [sign_mult * displacement_to_ml(v, unit, diameter) for v in raw_values]
        for i in range(n):
            if i == 0:
                dt = times_s[1] - times_s[0] if n > 1 else 1.0
                flow_ml_s[i] = (disp_ml[1] - disp_ml[0]) / dt if n > 1 and dt > 0 else 0.0
            elif i == n - 1:
                dt = times_s[i] - times_s[i - 1]
                flow_ml_s[i] = (disp_ml[i] - disp_ml[i - 1]) / dt if dt > 0 else 0.0
            else:
                dt = times_s[i + 1] - times_s[i - 1]
                flow_ml_s[i] = (disp_ml[i + 1] - disp_ml[i - 1]) / dt if dt > 0 else 0.0
    else:
        flow_ml_s = [sign_mult * velocity_to_ml_per_s(v, unit, diameter) for v in raw_values]
        cum = 0.0
        disp_ml[0] = 0.0
        for i in range(1, n):
            dt = times_s[i] - times_s[i - 1]
            cum += 0.5 * (flow_ml_s[i - 1] + flow_ml_s[i]) * dt
            disp_ml[i] = cum

    total_inf = 0.0
    total_wdr = 0.0
    for i in range(1, n):
        dv = disp_ml[i] - disp_ml[i - 1]
        if dv > 0:
            total_inf += dv
        elif dv < 0:
            total_wdr += -dv

    v0 = disp_ml[0] if disp_ml else 0.0
    v_max = max(disp_ml) if disp_ml else 0.0
    v_min = min(disp_ml) if disp_ml else 0.0

    req_fill = max(0.0, v_max - v0)
    req_headroom = max(0.0, v0 - v_min)
    span_ml = max(0.0, v_max - v_min)
    area = syringe_area_mm2(diameter)
    peak_stroke = (span_ml * 1000.0 / area) if area > 0 else 0.0
    peak_flow = max((abs(f) * 60.0 for f in flow_ml_s), default=0.0)

    warnings: list[str] = []
    if enabled:
        if diameter <= 0:
            warnings.append("Syringe diameter must be > 0 mm")
        if total_inf + total_wdr > safety.MAX_TOTAL_VOLUME_ML:
            warnings.append(
                f"Total swept volume ({total_inf + total_wdr:.2f} mL) exceeds "
                f"safety limit ({safety.MAX_TOTAL_VOLUME_ML:.2f} mL)"
            )
        capacity = ch_cfg.get("syringeCapacityMl")
        if capacity is not None and float(capacity) > 0 and span_ml > float(capacity):
            warnings.append(
                f"Required volume ({span_ml:.2f} mL) exceeds syringe capacity ({float(capacity):.2f} mL)"
            )

    metrics = ChannelMetrics(
        enabled=enabled,
        diameter_mm=diameter,
        total_infused_ml=total_inf,
        total_withdrawn_ml=total_wdr,
        net_volume_ml=(disp_ml[-1] - v0) if disp_ml else 0.0,
        required_initial_fill_ml=req_fill,
        required_headroom_ml=req_headroom,
        min_syringe_capacity_ml=span_ml,
        peak_stroke_mm=peak_stroke,
        peak_flow_ml_min=peak_flow,
        has_infusion=total_inf > 1e-6,
        has_withdrawal=total_wdr > 1e-6,
        warnings=warnings,
    )
    return metrics, disp_ml, flow_ml_s


def _build_slice_cmd(diameter_mm: float, flow_ml_s: float, duration_s: float) -> StepCommand | None:
    """Convert an absolute flow rate (mL/s) and duration (seconds) into a StepCommand."""
    abs_ml_min = abs(flow_ml_s) * 60.0
    if abs_ml_min < 1e-7 or diameter_mm <= 0:
        return None
    data = ProgramData(
        step=1,
        pumb_status=True,
        withdrawal=(flow_ml_s < 0),
        diameter=diameter_mm,
        flow_rate=abs_ml_min,
        flow_rate_unit=FlowRateUnit.ML_MIN,
        duration_type=DurationType.DURATION,
        duration=float(duration_s),
        duration_unit=DurationUnit.SECOND,
    )
    return calculate_values(data)


def compile_curve_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate, evaluate, and compile a dual-channel curve configuration.

    Returns a dictionary containing:
      - preview samples for Pump A and Pump B
      - per-channel syringe volume/stroke metrics & warnings
      - compiled hardware slices (CompiledSlice list)
    """
    y_mode = str(config.get("yMode", "velocity")).strip().lower()
    if y_mode not in ("displacement", "velocity"):
        raise ValueError(f"invalid yMode: {y_mode}")

    unit = str(config.get("unit", "mL/min" if y_mode == "velocity" else "mL"))
    if y_mode == "displacement" and unit not in DISPLACEMENT_UNITS:
        raise ValueError(f"invalid displacement unit: {unit}")
    if y_mode == "velocity" and unit not in VELOCITY_UNITS:
        raise ValueError(f"invalid velocity unit: {unit}")

    time_unit = str(config.get("timeUnit", "s"))
    if time_unit not in TIME_TO_SECONDS:
        raise ValueError(f"invalid timeUnit: {time_unit}")

    total_T_user = float(config.get("duration", 60.0))
    if total_T_user <= 0:
        raise ValueError("Total duration must be greater than zero")
    total_T_s = total_T_user * TIME_TO_SECONDS[time_unit]
    if total_T_s > safety.MAX_STEP_DURATION_S:
        raise ValueError(
            f"Total duration ({total_T_s:.0f}s) exceeds maximum limit ({safety.MAX_STEP_DURATION_S:.0f}s)"
        )

    slice_duration_s = max(0.05, float(config.get("sliceDurationS", 0.25)))
    num_slices = max(1, int(math.ceil(total_T_s / slice_duration_s)))
    if num_slices > 2400:
        raise ValueError(
            f"Too many time slices ({num_slices}); increase slice duration or shorten total duration"
        )

    ch_a = dict(config.get("channelA") or {})
    ch_b = dict(config.get("channelB") or {})
    link = dict(config.get("link") or {})
    link_mode = str(link.get("mode", "independent"))
    link_factor = float(link.get("factor", 1.0))

    # High-resolution sample grid for smooth preview & accurate integration
    preview_points = 121
    times_user = [total_T_user * i / (preview_points - 1) for i in range(preview_points)]
    times_s = [t * TIME_TO_SECONDS[time_unit] for t in times_user]

    if link_mode == "B_from_A":
        raw_a = _evaluate_raw_channel(ch_a, times_user, total_T_user)
        raw_b = [v * link_factor for v in raw_a]
    elif link_mode == "A_from_B":
        raw_b = _evaluate_raw_channel(ch_b, times_user, total_T_user)
        raw_a = [v * link_factor for v in raw_b]
    else:
        raw_a = _evaluate_raw_channel(ch_a, times_user, total_T_user)
        raw_b = _evaluate_raw_channel(ch_b, times_user, total_T_user)

    metrics_a, disp_a_ml, flow_a_ml_s = _compute_channel_metrics_and_flows(
        ch_a, raw_a, times_s, y_mode, unit
    )
    metrics_b, disp_b_ml, flow_b_ml_s = _compute_channel_metrics_and_flows(
        ch_b, raw_b, times_s, y_mode, unit
    )

    # Compile into hardware slices
    slices: list[CompiledSlice] = []
    diam_a = float(ch_a.get("diameter", 14.5))
    diam_b = float(ch_b.get("diameter", 14.5))
    en_a = bool(ch_a.get("enabled", True))
    en_b = bool(ch_b.get("enabled", True))

    for idx in range(num_slices):
        t0 = round(idx * slice_duration_s, 3)
        t1 = round(min(total_T_s, (idx + 1) * slice_duration_s), 3)
        dur = max(0.01, round(t1 - t0, 3))
        t_mid = 0.5 * (t0 + t1)

        # Interpolate flow_ml_s at slice midpoint (or average over slice)
        frac = min(1.0, max(0.0, t_mid / total_T_s)) if total_T_s > 0 else 0.0
        pos = frac * (preview_points - 1)
        lo = int(math.floor(pos))
        hi = min(preview_points - 1, lo + 1)
        alpha = pos - lo

        fa = (1.0 - alpha) * flow_a_ml_s[lo] + alpha * flow_a_ml_s[hi] if en_a else 0.0
        fb = (1.0 - alpha) * flow_b_ml_s[lo] + alpha * flow_b_ml_s[hi] if en_b else 0.0

        cmd_a = _build_slice_cmd(diam_a, fa, dur) if en_a else None
        cmd_b = _build_slice_cmd(diam_b, fb, dur) if en_b else None

        slices.append(
            CompiledSlice(
                index=idx,
                start_s=float(t0),
                duration_s=dur,
                active_a=cmd_a is not None,
                active_b=cmd_b is not None,
                withdrawal_a=(fa < 0),
                withdrawal_b=(fb < 0),
                flow_a_ml_s=fa,
                flow_b_ml_s=fb,
                cmd_a=cmd_a,
                cmd_b=cmd_b,
            )
        )

    return {
        "yMode": y_mode,
        "unit": unit,
        "timeUnit": time_unit,
        "duration": total_T_user,
        "durationS": total_T_s,
        "sliceDurationS": round(slice_duration_s, 3),
        "sliceCount": len(slices),
        "times": [round(t, 4) for t in times_user],
        "valuesA": [round(v, 5) for v in raw_a],
        "valuesB": [round(v, 5) for v in raw_b],
        "metricsA": metrics_a.to_dict(),
        "metricsB": metrics_b.to_dict(),
        "slices": [s.to_dict() for s in slices],
        "_compiled_slices": slices,
    }
