"""Fidelity tests: the Python port vs. the algorithm in the original IL.

`reference_calculate` below is transcribed from `MainForm.CalculateValues`
using the *original's own control-flow shape* (nested tests, no extra
fallback), so it is an independent check on `pump.program.calculate_values`.
"""

from __future__ import annotations

import math
import unittest

from pump.program import (
    DurationType,
    DurationUnit,
    FlowRateUnit,
    ProgramData,
    VolumeUnit,
    _convert_to_int32,
    _round_half_up,
    calculate_values,
)


def reference_calculate(diameter: float, flow: float, unit: int):
    """MainForm.CalculateValues, written the way the IL does it."""
    # IL 0-31: 8143 * D * D / Q
    tdspa = 8143 * diameter * diameter / flow

    # IL 42-170: flow-rate unit scaling
    if unit == 0:
        tdspa = tdspa * 100
    elif unit == 1:
        tdspa = tdspa * 100 / 60
    elif unit == 2:
        tdspa = tdspa / 10
    else:
        tdspa = tdspa / 600

    # IL 175-224: * 12 / 2 then * 1.03
    tdspa = tdspa * 12 / 2
    tdspa = tdspa * 1.03

    # IL 244: fits already -> prescaler 1
    if tdspa <= 65536:
        tdsp = tdspa
        prescaler = 1
    else:
        # IL 249-293
        tdsp = tdspa / 8
        prescaler = 8
        if tdsp > 65536:                    # IL 293
            tdsp = tdspa / 64               # IL 295-320
            prescaler = 64
            if tdsp > 65536:                # IL 340
                tdsp = tdspa / 256          # IL 342-370
                prescaler = 256
                if tdsp > 65536:            # IL 390
                    tdsp = tdspa / 1024     # IL 392-420
                    prescaler = 1024
                    # IL 425: unconditional branch, no further fallback

    # IL 446-516: round half up
    floor = math.floor(tdsp)
    tdsp = floor + 1 if (tdsp - floor) >= 0.5 else floor
    return tdsp, prescaler


def make(diameter: float, flow: float, unit: FlowRateUnit) -> ProgramData:
    return ProgramData(
        diameter=diameter,
        flow_rate=flow,
        flow_rate_unit=unit,
        duration_type=DurationType.DURATION,
        duration=1.0,
        duration_unit=DurationUnit.SECOND,
    )


class SpeedFormulaTests(unittest.TestCase):
    def test_matches_reference_across_a_sweep(self):
        diameters = [1.0, 4.5, 10.0, 14.5, 20.0, 26.7, 34.0, 50.0]
        flows = [
            0.001, 0.01, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 25.0, 50.0,
            100.0, 250.0, 500.0, 1000.0, 5000.0, 10000.0, 20000.0,
        ]
        checked = 0
        for unit in (FlowRateUnit.UL_HR, FlowRateUnit.UL_MIN,
                     FlowRateUnit.ML_HR, FlowRateUnit.ML_MIN):
            for diameter in diameters:
                for flow in flows:
                    want_tdsp, want_presc = reference_calculate(diameter, flow, int(unit))
                    got = calculate_values(make(diameter, flow, unit))
                    self.assertEqual(
                        (got.tdsp, got.prescaler),
                        (want_tdsp, want_presc),
                        f"D={diameter} Q={flow} unit={unit.name}",
                    )
                    checked += 1
        self.assertGreater(checked, 500)

    def test_prescaler_ladder(self):
        """Walk each rung of the prescaler ladder at D=20 mm."""
        # ul/hr: tdspa = 8143*400*100*6*1.03 / Q = 2012949600 / Q
        ladder = {10000.0: 8, 1000.0: 64, 200.0: 256, 10.0: 1024, 1.0: 1024}
        for flow, want_prescaler in ladder.items():
            got = calculate_values(make(20.0, flow, FlowRateUnit.UL_HR))
            self.assertEqual(
                got.prescaler, want_prescaler, f"flow {flow} ul/hr")
            self.assertEqual(
                (got.tdsp, got.prescaler),
                reference_calculate(20.0, flow, int(FlowRateUnit.UL_HR)),
                f"flow {flow} ul/hr vs reference",
            )

    def test_prescaler_one_when_value_already_fits(self):
        got = calculate_values(make(20.0, 1.0, FlowRateUnit.ML_MIN))
        self.assertEqual(got.prescaler, 1)
        self.assertLessEqual(got.tdsp, 65535)

    def test_round_half_up_boundary(self):
        self.assertEqual(_round_half_up(2.4), 2.0)
        self.assertEqual(_round_half_up(2.5), 3.0)
        self.assertEqual(_round_half_up(2.6), 3.0)
        self.assertEqual(_round_half_up(0.5), 1.0)

    def test_extreme_low_flow_uses_1024_not_raw(self):
        """tdspa/1024 still exceeds 65536 -> the original keeps /1024."""
        got = calculate_values(make(20.0, 1.0, FlowRateUnit.UL_HR))
        self.assertEqual(got.prescaler, 1024)
        self.assertAlmostEqual(got.tdsp, 1965771.0, delta=1.0)
        # this is the case the engine refuses to run
        self.assertGreater(got.tdsp, 65535)

    def test_known_values_ml_per_min(self):
        expected = {1.0: 33549, 2.0: 16775, 3.0: 11183, 4.0: 8387, 5.0: 6710}
        for flow, want in expected.items():
            got = calculate_values(make(20.0, flow, FlowRateUnit.ML_MIN))
            self.assertEqual(got.tdsp, want)


class DurationTests(unittest.TestCase):
    def _cal(self, **kwargs) -> float:
        data = ProgramData(**kwargs)
        return data.compute_cal_duration()

    def test_duration_units(self):
        self.assertEqual(self._cal(duration=2, duration_unit=DurationUnit.SECOND), 2)
        self.assertEqual(self._cal(duration=2, duration_unit=DurationUnit.MINUTE), 120)
        self.assertEqual(self._cal(duration=2, duration_unit=DurationUnit.HOUR), 7200)

    def test_volume_microliter(self):
        base = dict(duration_type=DurationType.VOLUME, volume_unit=VolumeUnit.MICROLITER,
                    duration=120.0, flow_rate=120.0)
        self.assertAlmostEqual(
            self._cal(flow_rate_unit=FlowRateUnit.UL_HR, **base), 3600.0)
        self.assertAlmostEqual(
            self._cal(flow_rate_unit=FlowRateUnit.UL_MIN, **base), 60.0)
        self.assertAlmostEqual(
            self._cal(flow_rate_unit=FlowRateUnit.ML_HR, **base), 3.6)
        self.assertAlmostEqual(
            self._cal(flow_rate_unit=FlowRateUnit.ML_MIN, **base), 0.06)

    def test_volume_mililiter(self):
        base = dict(duration_type=DurationType.VOLUME, volume_unit=VolumeUnit.MILILITER,
                    duration=1.0, flow_rate=1.0)
        self.assertAlmostEqual(
            self._cal(flow_rate_unit=FlowRateUnit.UL_HR, **base), 3_600_000.0)
        self.assertAlmostEqual(
            self._cal(flow_rate_unit=FlowRateUnit.UL_MIN, **base), 60_000.0)
        self.assertAlmostEqual(
            self._cal(flow_rate_unit=FlowRateUnit.ML_HR, **base), 3600.0)
        self.assertAlmostEqual(
            self._cal(flow_rate_unit=FlowRateUnit.ML_MIN, **base), 60.0)

    def test_duration_step_uses_dotnet_rounding(self):
        """Convert.ToInt32(double) rounds half to even, not half up."""
        self.assertEqual(_convert_to_int32(2.5), 2)
        self.assertEqual(_convert_to_int32(3.5), 4)
        self.assertEqual(_convert_to_int32(2.4999), 2)
        self.assertEqual(_convert_to_int32(2.5001), 3)


if __name__ == "__main__":
    unittest.main()
