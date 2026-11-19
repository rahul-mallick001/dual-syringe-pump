"""Over-travel / over-volume guards.

The pump reports limit switches (`0x32` home, `0x23` far) and we stop on them,
but we should never *plan* a move that could drive a rack into a hard stop:
stalling a leadscrew against its end damages the mechanism.

All limits are configurable through environment variables so they can be
tightened for a given rig without touching code.
"""

from __future__ import annotations

import os

from .program import DurationType, FlowRateUnit, ProgramData, VolumeUnit


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# Largest volume a single step may command (ml).
MAX_STEP_VOLUME_ML = _env_float("PUMP_MAX_STEP_ML", 20.0)
# Largest cumulative volume a whole program may command (ml).
MAX_TOTAL_VOLUME_ML = _env_float("PUMP_MAX_TOTAL_ML", 60.0)
# Longest single step (seconds) - the vendor spec tops out at 18 h.
MAX_STEP_DURATION_S = _env_float("PUMP_MAX_STEP_S", 18 * 3600.0)
# Longest continuous homing run before we give up and stop (seconds).
HOME_TIMEOUT_S = _env_float("PUMP_HOME_TIMEOUT_S", 45.0)


def flow_ml_per_second(flow_rate: float, unit: FlowRateUnit) -> float:
    if flow_rate <= 0:
        return 0.0
    if unit == FlowRateUnit.UL_HR:
        return flow_rate / 1000.0 / 3600.0
    if unit == FlowRateUnit.UL_MIN:
        return flow_rate / 1000.0 / 60.0
    if unit == FlowRateUnit.ML_HR:
        return flow_rate / 3600.0
    return flow_rate / 60.0


def step_volume_ml(data: ProgramData) -> float:
    """Volume a step will command, in ml.

    Duration mode: flow rate x duration.  Volume mode: the duration field is
    the requested volume itself.
    """
    if data.duration_type == DurationType.VOLUME:
        volume = float(data.duration)
        if data.volume_unit == VolumeUnit.MICROLITER:
            volume /= 1000.0
        return volume
    seconds = data.compute_cal_duration()
    return flow_ml_per_second(data.flow_rate, data.flow_rate_unit) * seconds


def check_step(data: ProgramData) -> str | None:
    """Return a human-readable refusal, or None if the step is safe to plan."""
    seconds = data.compute_cal_duration()
    if seconds < 1:
        return "duration resolves to less than one second"
    if seconds > MAX_STEP_DURATION_S:
        return (
            f"duration {seconds:.0f}s exceeds the {MAX_STEP_DURATION_S:.0f}s limit "
            f"(set PUMP_MAX_STEP_S to change)"
        )

    volume = step_volume_ml(data)
    if volume > MAX_STEP_VOLUME_ML:
        return (
            f"step would command {volume:.2f} ml, above the "
            f"{MAX_STEP_VOLUME_ML:.2f} ml per-step limit - this risks driving the "
            f"rack past its home/far stop (set PUMP_MAX_STEP_ML to change)"
        )
    return None


def check_program(steps: list[ProgramData]) -> str | None:
    """Refuse a program whose cumulative volume could over-travel a rack."""
    total = 0.0
    for step in steps:
        error = check_step(step)
        if error:
            return f"Step {step.step}: {error}"
        total += step_volume_ml(step)
    if total > MAX_TOTAL_VOLUME_ML:
        return (
            f"program would command {total:.2f} ml in total, above the "
            f"{MAX_TOTAL_VOLUME_ML:.2f} ml limit (set PUMP_MAX_TOTAL_ML to change)"
        )
    return None
