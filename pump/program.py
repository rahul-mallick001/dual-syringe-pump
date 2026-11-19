"""Program model, `.txt` (de)serialisation and the pump speed calculation.

Everything here mirrors `Infusion_Pump.MainForm.CalculateValues` and the
`ProgramData` accessors used by `buttonLoad_Click` / `GetData`, so that a
program loaded by this application produces byte-identical commands to the
original.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path


# ---------------------------------------------------------------------------
# Enumerations (values and names match Infusion_Pump.Class.CommonEnums)
# ---------------------------------------------------------------------------
class FlowRateUnit(IntEnum):
    UL_HR = 0
    UL_MIN = 1
    ML_HR = 2
    ML_MIN = 3

    @property
    def file_name(self) -> str:
        return {0: "UlHr", 1: "Ulmin", 2: "Mlhr", 3: "Mlmin"}[int(self)]

    @property
    def label(self) -> str:
        return {0: "ul/hr", 1: "ul/min", 2: "ml/hr", 3: "ml/min"}[int(self)]


class DurationType(IntEnum):
    DURATION = 0
    VOLUME = 1

    @property
    def file_name(self) -> str:
        return {0: "Duration", 1: "Volume"}[int(self)]

    @property
    def label(self) -> str:
        return {0: "Duration", 1: "T_Volume"}[int(self)]


class DurationUnit(IntEnum):
    SECOND = 0
    MINUTE = 1
    HOUR = 2

    @property
    def file_name(self) -> str:
        return {0: "Second", 1: "Minute", 2: "Hour"}[int(self)]

    @property
    def label(self) -> str:
        return self.file_name


class VolumeUnit(IntEnum):
    MICROLITER = 0
    MILILITER = 1

    @property
    def file_name(self) -> str:
        return {0: "Microliter", 1: "Mililiter"}[int(self)]

    @property
    def label(self) -> str:
        return self.file_name


_STEP_COUNT = 5


# ---------------------------------------------------------------------------
# Tolerant unit parsing
# ---------------------------------------------------------------------------
def _norm(token: str) -> str:
    return re.sub(r"[\s/_.\-]", "", token.strip().lower()).replace("µ", "u")


_FLOW_UNITS = {
    "ulhr": FlowRateUnit.UL_HR,
    "ulh": FlowRateUnit.UL_HR,
    "ulmin": FlowRateUnit.UL_MIN,
    "ulm": FlowRateUnit.UL_MIN,
    "mlhr": FlowRateUnit.ML_HR,
    "mlh": FlowRateUnit.ML_HR,
    "mlmin": FlowRateUnit.ML_MIN,
    "mlm": FlowRateUnit.ML_MIN,
}

_DURATION_UNITS = {
    "second": DurationUnit.SECOND,
    "seconds": DurationUnit.SECOND,
    "sec": DurationUnit.SECOND,
    "s": DurationUnit.SECOND,
    "minute": DurationUnit.MINUTE,
    "minutes": DurationUnit.MINUTE,
    "min": DurationUnit.MINUTE,
    "m": DurationUnit.MINUTE,
    "hour": DurationUnit.HOUR,
    "hours": DurationUnit.HOUR,
    "hr": DurationUnit.HOUR,
    "h": DurationUnit.HOUR,
}

_DURATION_TYPES = {
    "duration": DurationType.DURATION,
    "volume": DurationType.VOLUME,
    "tvolume": DurationType.VOLUME,
    "tv": DurationType.VOLUME,
}

_VOLUME_UNITS = {
    "microliter": VolumeUnit.MICROLITER,
    "microliters": VolumeUnit.MICROLITER,
    "ul": VolumeUnit.MICROLITER,
    "mililiter": VolumeUnit.MILILITER,
    "milliliter": VolumeUnit.MILILITER,
    "ml": VolumeUnit.MILILITER,
}


def _parse_bool(token: str) -> bool:
    return str(token).strip().lower() in {"true", "1", "yes", "y", "on"}


def _parse_number(token: str) -> float:
    return float(str(token).strip().replace(",", "."))


# ---------------------------------------------------------------------------
# ProgramData
# ---------------------------------------------------------------------------
@dataclass
class ProgramData:
    step: int = 1
    pumb_status: bool = True          # "syringe enabled" (original spelling)
    withdrawal: bool = False
    diameter: float = 0.0
    flow_rate: float = 0.0
    flow_rate_unit: FlowRateUnit = FlowRateUnit.ML_MIN
    duration_type: DurationType = DurationType.DURATION
    duration: float = 0.0
    duration_unit: DurationUnit = DurationUnit.MINUTE
    volume_unit: VolumeUnit = VolumeUnit.MICROLITER
    cal_duration: float = 0.0

    # -- derived values ----------------------------------------------------
    def compute_cal_duration(self) -> float:
        """Seconds for this step, exactly as MainForm.GetData computes it."""
        if self.duration_type == DurationType.DURATION:
            if self.duration_unit == DurationUnit.MINUTE:
                return self.duration * 60.0
            if self.duration_unit == DurationUnit.HOUR:
                return self.duration * 3600.0
            return self.duration

        # Volume mode: `duration` holds the requested volume.
        if self.duration <= 0 or self.flow_rate <= 0:
            return 0.0
        ratio = self.duration / self.flow_rate
        if self.volume_unit == VolumeUnit.MICROLITER:
            if self.flow_rate_unit == FlowRateUnit.UL_HR:
                return ratio * 3600.0
            if self.flow_rate_unit == FlowRateUnit.UL_MIN:
                return ratio * 60.0
            if self.flow_rate_unit == FlowRateUnit.ML_HR:
                return ratio * 3.6
            return ratio * 0.06
        if self.flow_rate_unit == FlowRateUnit.UL_HR:
            return ratio * 3600.0 * 1000.0
        if self.flow_rate_unit == FlowRateUnit.UL_MIN:
            return ratio * 60.0 * 1000.0
        if self.flow_rate_unit == FlowRateUnit.ML_HR:
            return ratio * 3600.0
        return ratio * 60.0

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "pumbStatus": self.pumb_status,
            "withdrawal": self.withdrawal,
            "diameter": self.diameter,
            "flowRate": self.flow_rate,
            "flowRateUnit": int(self.flow_rate_unit),
            "flowRateUnitLabel": self.flow_rate_unit.label,
            "durationType": int(self.duration_type),
            "durationTypeLabel": self.duration_type.label,
            "duration": self.duration,
            "durationUnit": int(self.duration_unit),
            "durationUnitLabel": self.duration_unit.label,
            "volumeUnit": int(self.volume_unit),
            "volumeUnitLabel": self.volume_unit.label,
            "calDuration": round(self.cal_duration, 3),
        }

    def validate(self) -> str | None:
        """Mirror the checks of MainForm.GetData; returns an error or None."""
        if self.diameter is None or self.diameter <= 0:
            return "Please Enter the Valuable Parameters"
        if self.flow_rate is None or self.flow_rate <= 0:
            return "Please Enter the Valuable Parameters"
        if self.duration_type == DurationType.VOLUME:
            if self.duration is None or self.duration <= 0:
                return "Please Enter the Valuable Parameters"
            cal_dur = self.compute_cal_duration()
            if not (1.0 <= cal_dur <= 18.0 * 3600.0):
                return "Adjust Target Volume Or Flowrate To Get A Duration Between 1 Second to 18 Hours"
        else:
            if self.duration is None or self.duration <= 0:
                return "Please Enter the Syringe Pump Duration Between 1 To 65535"
            cal_dur = self.compute_cal_duration()
            if not (1.0 <= cal_dur <= 18.0 * 3600.0):
                return "Please Enter the Syringe Pump Duration Between 1 To 65535"
        return None


# ---------------------------------------------------------------------------
# Speed / prescaler calculation (MainForm.CalculateValues)
# ---------------------------------------------------------------------------
@dataclass
class StepCommand:
    tdsp: float
    prescaler: int
    duration_step: int
    cal_duration: float

    @property
    def total_seconds(self) -> int:
        return self.duration_step


def _round_half_up(value: float) -> float:
    """The IL rounds with Floor() + 0.5 comparison (round half up)."""
    floor = math.floor(value)
    return floor + 1.0 if (value - floor) >= 0.5 else float(floor)


def _convert_to_int32(value: float) -> int:
    """`Convert.ToInt32(double)` rounds half to even, unlike a C cast."""
    return int(round(value))


def calculate_values(data: ProgramData) -> StepCommand:
    """Port of MainForm.CalculateValues.

    tdspa = 8143 * D^2 / Q, scaled for the flow-rate unit, then * 12 / 2 * 1.03.
    A prescaler of 8/64/256/1024 (or 1) is selected so tdsp fits in 16 bits.
    """
    if data.flow_rate <= 0:
        raise ValueError("flow rate must be greater than zero")

    tdspa = 8143.0 * data.diameter * data.diameter / data.flow_rate

    unit = data.flow_rate_unit
    if unit == FlowRateUnit.UL_HR:
        tdspa = tdspa * 100.0
    elif unit == FlowRateUnit.UL_MIN:
        tdspa = tdspa * 100.0 / 60.0
    elif unit == FlowRateUnit.ML_HR:
        tdspa = tdspa / 10.0
    else:  # ML_MIN
        tdspa = tdspa / 600.0

    tdspa = tdspa * 12.0 / 2.0
    tdspa = tdspa * 1.03

    # IL 244: if (tdspa <= 65536) -> prescaler 1 with the raw value.
    # Otherwise try 8, 64, 256, 1024 in turn and take the first that fits.
    # When even /1024 does not fit the original still uses /1024 (IL 425
    # branches unconditionally to the rounding step) - there is no extra
    # fallback, so reproduce that exactly.
    if tdspa <= 65536.0:
        tdsp, prescaler = tdspa, 1
    elif tdspa / 8.0 <= 65536.0:
        tdsp, prescaler = tdspa / 8.0, 8
    elif tdspa / 64.0 <= 65536.0:
        tdsp, prescaler = tdspa / 64.0, 64
    elif tdspa / 256.0 <= 65536.0:
        tdsp, prescaler = tdspa / 256.0, 256
    else:
        tdsp, prescaler = tdspa / 1024.0, 1024

    tdsp = _round_half_up(tdsp)

    data.cal_duration = data.compute_cal_duration()
    # IL 532/760: the transmitted duration is Convert.ToInt32(CalDuration) of the
    # unrounded value.  In volume mode the original then rounds CalDuration
    # half-up in place (IL 777-825), which only affects the total-time display.
    duration_step = _convert_to_int32(data.cal_duration)
    if data.duration_type == DurationType.VOLUME:
        data.cal_duration = _round_half_up(data.cal_duration)
    return StepCommand(
        tdsp=tdsp,
        prescaler=prescaler,
        duration_step=duration_step,
        cal_duration=data.cal_duration,
    )


# ---------------------------------------------------------------------------
# File format
# ---------------------------------------------------------------------------
_STEP_LINE = re.compile(r"^\s*step\s*:\s*(?P<step>\d+)", re.IGNORECASE)


def _value_tokens(line: str) -> list[str]:
    """Everything to the right of the first ':' as whitespace tokens."""
    if ":" not in line:
        return line.split()
    return line.split(":", 1)[1].split()


def parse_program_text(text: str) -> list[ProgramData]:
    """Parse the `.txt` program format written by the original application.

    A tolerant re-implementation: the original splits on single spaces and
    indexes fixed positions, which is brittle; here everything after the
    first colon is tokenised instead, which yields identical results for
    well-formed files.
    """
    lines = [ln for ln in text.splitlines()]
    steps: list[ProgramData] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if not _STEP_LINE.match(line):
            i += 1
            continue

        step = ProgramData()
        tokens = _value_tokens(line)
        step.step = int(tokens[0]) if tokens and tokens[0].isdigit() else 1
        i += 1

        # Syringe Status
        if i < len(lines):
            toks = _value_tokens(lines[i])
            if toks:
                step.pumb_status = _parse_bool(toks[0])
            i += 1
        # Withdrawal Status
        if i < len(lines):
            toks = _value_tokens(lines[i])
            if toks:
                step.withdrawal = _parse_bool(toks[0])
            i += 1
        # Syringe_Diameter
        if i < len(lines):
            toks = _value_tokens(lines[i])
            if toks:
                step.diameter = _parse_number(toks[0])
            i += 1
        # Flow_Rate <value> <unit>
        if i < len(lines):
            toks = _value_tokens(lines[i])
            if toks:
                step.flow_rate = _parse_number(toks[0])
            if len(toks) > 1:
                step.flow_rate_unit = _FLOW_UNITS.get(_norm(toks[1]), FlowRateUnit.UL_HR)
            i += 1
        # Duration/TV <type> <value> <unit>
        if i < len(lines):
            toks = _value_tokens(lines[i])
            if toks:
                step.duration_type = _DURATION_TYPES.get(_norm(toks[0]), DurationType.DURATION)
            if len(toks) > 1:
                step.duration = _parse_number(toks[1])
            if len(toks) > 2:
                if step.duration_type == DurationType.DURATION:
                    step.duration_unit = _DURATION_UNITS.get(
                        _norm(toks[2]), DurationUnit.SECOND
                    )
                else:
                    step.volume_unit = _VOLUME_UNITS.get(
                        _norm(toks[2]), VolumeUnit.MICROLITER
                    )
            i += 1

        step.cal_duration = step.compute_cal_duration()
        steps.append(step)

    return steps


def load_program(path: str | Path) -> list[ProgramData]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    steps = parse_program_text(text)
    if not steps:
        raise ValueError("no program steps found in file")
    return steps


def _fmt_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def format_program(steps: list[ProgramData]) -> str:
    """Serialise in exactly the layout the original Save uses."""
    out: list[str] = []
    for data in steps:
        out.append(f"Step : {data.step} ")
        out.append(f"Syringe Status : {str(data.pumb_status)} ")
        out.append(f"Withdrawal Status : {str(data.withdrawal)} ")
        out.append(f"Syringe_Diameter : {_fmt_number(data.diameter)} mm ")
        out.append(
            f"Flow_Rate        : {_fmt_number(data.flow_rate)} "
            f"{data.flow_rate_unit.file_name} "
        )
        if data.duration_type == DurationType.DURATION:
            unit = data.duration_unit.file_name
        else:
            unit = data.volume_unit.file_name
        out.append(
            f"Duration/TV      : {data.duration_type.file_name} "
            f"{_fmt_number(data.duration)} {unit}"
        )
        out.append(" ")
    return "\n".join(out) + "\n"


def save_program(path: str | Path, steps: list[ProgramData]) -> None:
    Path(path).write_text(format_program(steps), encoding="utf-8")


def make_steps(count: int = _STEP_COUNT) -> list[ProgramData]:
    return [ProgramData(step=i + 1) for i in range(count)]
