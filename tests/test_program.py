"""Unit tests for the program model and the speed calculation."""

from __future__ import annotations

import math
import unittest
from pathlib import Path

from pump.program import (
    DurationType,
    DurationUnit,
    FlowRateUnit,
    ProgramData,
    VolumeUnit,
    calculate_values,
    format_program,
    parse_program_text,
)

_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = _ROOT / "examples" / "sample_program.txt"
if not SAMPLE.exists():
    SAMPLE = _ROOT / "sample prog.txt"


class ParseTests(unittest.TestCase):
    def test_sample_file_parses(self):
        steps = parse_program_text(SAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(len(steps), 5)

        expected_flows = [1.0, 2.0, 3.0, 4.0, 5.0]
        for index, step in enumerate(steps):
            self.assertEqual(step.step, index + 1)
            self.assertTrue(step.pumb_status)
            self.assertFalse(step.withdrawal)
            self.assertAlmostEqual(step.diameter, 20.0)
            self.assertAlmostEqual(step.flow_rate, expected_flows[index])
            self.assertEqual(step.flow_rate_unit, FlowRateUnit.ML_MIN)
            self.assertEqual(step.duration_type, DurationType.DURATION)
            self.assertAlmostEqual(step.duration, 1.0)
            self.assertEqual(step.duration_unit, DurationUnit.MINUTE)
            self.assertAlmostEqual(step.cal_duration, 60.0)

    def test_blank_and_ragged_files(self):
        text = (
            "Step : 1 \n"
            "Syringe Status : True \n"
            "Withdrawal Status : False \n"
            "Syringe_Diameter : 20 mm \n"
            "Flow_Rate        : 1 Mlmin \n"
            "Duration/TV      : Duration 1 Minute\n"
        )
        steps = parse_program_text(text)
        self.assertEqual(len(steps), 1)
        self.assertAlmostEqual(steps[0].cal_duration, 60.0)

    def test_volume_mode_units(self):
        data = ProgramData(
            duration_type=DurationType.VOLUME,
            volume_unit=VolumeUnit.MICROLITER,
            duration=600.0,           # 600 ul requested
            flow_rate=600.0,          # at 600 ul/hr
            flow_rate_unit=FlowRateUnit.UL_HR,
        )
        # 600 ul / 600 ul/hr = 1 h = 3600 s
        self.assertAlmostEqual(data.compute_cal_duration(), 3600.0)

    def test_round_trip(self):
        steps = parse_program_text(SAMPLE.read_text(encoding="utf-8"))
        text = format_program(steps)
        again = parse_program_text(text)
        self.assertEqual(len(again), 5)
        for a, b in zip(steps, again):
            self.assertAlmostEqual(a.diameter, b.diameter)
            self.assertAlmostEqual(a.flow_rate, b.flow_rate)
            self.assertAlmostEqual(a.duration, b.duration)
            self.assertEqual(a.flow_rate_unit, b.flow_rate_unit)
            self.assertEqual(a.duration_unit, b.duration_unit)
            self.assertEqual(a.pumb_status, b.pumb_status)
            self.assertEqual(a.withdrawal, b.withdrawal)


class CalculateValuesTests(unittest.TestCase):
    def test_ml_per_min_steps(self):
        """8143*D^2/Q, /600 for ml/min, *12/2, *1.03, round half up."""
        expected = {1.0: 33549, 2.0: 16775, 3.0: 11183, 4.0: 8387, 5.0: 6710}
        for flow, want in expected.items():
            data = ProgramData(
                diameter=20.0,
                flow_rate=flow,
                flow_rate_unit=FlowRateUnit.ML_MIN,
                duration=1.0,
                duration_unit=DurationUnit.MINUTE,
            )
            command = calculate_values(data)
            self.assertEqual(command.tdsp, want, f"flow {flow}")
            self.assertEqual(command.prescaler, 1, f"flow {flow}")
            self.assertEqual(command.duration_step, 60, f"flow {flow}")

    def test_no_unit_prescaler_when_value_fits(self):
        data = ProgramData(
            diameter=20.0,
            flow_rate=1.0,
            flow_rate_unit=FlowRateUnit.ML_MIN,
            duration=1.0,
            duration_unit=DurationUnit.MINUTE,
        )
        self.assertEqual(calculate_values(data).prescaler, 1)

    def test_prescaler_eight(self):
        data = ProgramData(
            diameter=20.0,
            flow_rate=10000.0,
            flow_rate_unit=FlowRateUnit.UL_HR,
            duration=1.0,
            duration_unit=DurationUnit.MINUTE,
        )
        command = calculate_values(data)
        self.assertEqual(command.prescaler, 8)
        self.assertEqual(command.tdsp, 25162)

    def test_prescaler_sixty_four(self):
        data = ProgramData(
            diameter=20.0,
            flow_rate=2000.0,
            flow_rate_unit=FlowRateUnit.UL_HR,
            duration=1.0,
            duration_unit=DurationUnit.MINUTE,
        )
        command = calculate_values(data)
        # 8143*400/2000 = 1628.6 -> *100 = 162860 -> *6 = 977160 -> *1.03 = 1006474.8
        # /8 = 125809.35 > 65536  ->  /64 = 15726.2 <= 65536
        self.assertEqual(command.prescaler, 64)
        self.assertEqual(command.tdsp, 15726)

    def test_round_half_up(self):
        # 8143*400/3/600*6*1.03 = 11183.05... must round down
        data = ProgramData(
            diameter=20.0,
            flow_rate=3.0,
            flow_rate_unit=FlowRateUnit.ML_MIN,
            duration=1.0,
            duration_unit=DurationUnit.MINUTE,
        )
        self.assertEqual(calculate_values(data).tdsp, 11183)

    def test_duration_units(self):
        for unit, factor in (
            (DurationUnit.SECOND, 1),
            (DurationUnit.MINUTE, 60),
            (DurationUnit.HOUR, 3600),
        ):
            data = ProgramData(
                diameter=20.0,
                flow_rate=1.0,
                flow_rate_unit=FlowRateUnit.ML_MIN,
                duration=2.0,
                duration_unit=unit,
            )
            self.assertEqual(calculate_values(data).duration_step, 2 * factor)

    def test_zero_flow_rejected(self):
        data = ProgramData(diameter=20.0, flow_rate=0.0)
        with self.assertRaises(ValueError):
            calculate_values(data)

    def test_validation_bounds(self):
        data = ProgramData(diameter=20.0, flow_rate=1.0, duration=0.0)
        self.assertIsNotNone(data.validate())
        data.duration = 70000.0
        self.assertIsNotNone(data.validate())
        data.duration = 1.0
        self.assertIsNone(data.validate())

        # Sub-milliliter volume mode (e.g. 0.5 ml @ 1 ml/min = 30 s duration)
        vol_data = ProgramData(
            diameter=20.0,
            flow_rate=1.0,
            flow_rate_unit=FlowRateUnit.ML_MIN,
            duration_type=DurationType.VOLUME,
            volume_unit=VolumeUnit.MILILITER,
            duration=0.5,
        )
        self.assertIsNone(vol_data.validate())


if __name__ == "__main__":
    unittest.main()
