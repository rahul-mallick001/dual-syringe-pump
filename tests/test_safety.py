"""Tests for the over-travel guards and the emergency stop."""

from __future__ import annotations

import time
import unittest

from pump import safety
from pump.device import PumpDevice
from pump.engine import PumpController
from pump.program import (
    DurationType,
    DurationUnit,
    FlowRateUnit,
    ProgramData,
    VolumeUnit,
)

from fake_pump import FakeFactory


def step(flow: float, seconds: int, unit: FlowRateUnit = FlowRateUnit.ML_MIN) -> ProgramData:
    return ProgramData(
        diameter=20.0,
        flow_rate=flow,
        flow_rate_unit=unit,
        duration_type=DurationType.DURATION,
        duration=float(seconds),
        duration_unit=DurationUnit.SECOND,
    )


class VolumeTests(unittest.TestCase):
    def test_duration_mode_volume(self):
        # 1 ml/min for 60 s = 1 ml
        self.assertAlmostEqual(safety.step_volume_ml(step(1.0, 60)), 1.0)
        # 5 ml/min for 120 s = 10 ml
        self.assertAlmostEqual(safety.step_volume_ml(step(5.0, 120)), 10.0)

    def test_flow_unit_conversions(self):
        # 600 ul/min = 0.6 ml/min -> 60 s = 0.6 ml
        self.assertAlmostEqual(
            safety.step_volume_ml(step(600.0, 60, FlowRateUnit.UL_MIN)), 0.6
        )
        # 3600 ul/hr = 3.6 ml/hr -> 3600 s = 3.6 ml
        self.assertAlmostEqual(
            safety.step_volume_ml(step(3600.0, 3600, FlowRateUnit.UL_HR)), 3.6
        )

    def test_volume_mode(self):
        data = ProgramData(
            duration_type=DurationType.VOLUME,
            volume_unit=VolumeUnit.MILILITER,
            duration=5.0,
            flow_rate=1.0,
            flow_rate_unit=FlowRateUnit.ML_MIN,
        )
        self.assertAlmostEqual(safety.step_volume_ml(data), 5.0)


class CheckStepTests(unittest.TestCase):
    def test_small_step_allowed(self):
        self.assertIsNone(safety.check_step(step(1.0, 60)))

    def test_oversized_step_refused(self):
        # 5 ml/min for 600 s = 50 ml, over the 20 ml default
        error = safety.check_step(step(5.0, 600))
        self.assertIsNotNone(error)
        self.assertIn("above the", error)

    def test_program_total_refused(self):
        steps = [step(5.0, 240)] * 4  # 20 ml each = 80 ml total
        error = safety.check_program(steps)
        self.assertIsNotNone(error)
        self.assertIn("in total", error)

    def test_sub_second_refused(self):
        data = step(1.0, 1)
        data.duration = 0.2  # 0.2 s
        self.assertIsNotNone(safety.check_step(data))


class ControllerGuardTests(unittest.TestCase):
    def build(self) -> tuple[PumpController, FakeFactory]:
        fake = FakeFactory()

        def factory(port: str) -> PumpDevice:
            return PumpDevice(
                port=port,
                serial_factory=fake,
                ack_timeout_ms=80,
                connect_timeout_ms=80,
            )

        return PumpController(device_factory=factory), fake

    def test_run_refused_when_over_volume(self):
        controller, _ = self.build()
        controller.connect("FAKE")
        controller.set_steps([step(5.0, 600)])  # 50 ml
        with self.assertRaises(RuntimeError) as ctx:
            controller.run()
        self.assertIn("above the", str(ctx.exception))
        self.assertFalse(controller.state()["running"])

    def test_emergency_stop_latches_and_blocks_run(self):
        controller, fake = self.build()
        controller.connect("FAKE")
        controller.set_steps([step(1.0, 60)])

        controller.emergency_stop("test estop")
        state = controller.state()
        self.assertEqual(state["fault"], "test estop")
        self.assertIn(0x14, fake.last.written, "Stop byte must be transmitted")

        with self.assertRaises(RuntimeError) as ctx:
            controller.run()
        self.assertIn("clear the fault", str(ctx.exception))

        controller.clear_fault()
        self.assertIsNone(controller.state()["fault"])
        controller.run()  # allowed again
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and controller.state()["running"]:
            time.sleep(0.02)
        self.assertEqual(controller.state()["status"], "Program completed")

    def test_state_exposes_limits(self):
        controller, _ = self.build()
        limits = controller.state()["limits"]
        self.assertIn("maxStepMl", limits)
        self.assertIn("maxTotalMl", limits)
        self.assertGreater(limits["maxStepMl"], 0)

    def test_withdrawal_blocked_while_at_home(self):
        """At the home stop, withdrawing would drive into the stop."""
        controller, _ = self.build()
        controller.connect("FAKE")
        infuse = step(1.0, 60)
        withdraw = step(1.0, 60)
        withdraw.withdrawal = True
        controller.set_steps([withdraw])
        controller.selected = [True]
        controller.at_home = True

        with self.assertRaises(RuntimeError) as ctx:
            controller.run()
        message = str(ctx.exception).lower()
        self.assertIn("home", message)
        self.assertIn("withdraw", message)

    def test_infuse_allowed_while_at_home(self):
        controller, _ = self.build()
        controller.connect("FAKE")
        controller.set_steps([step(1.0, 60)])  # infuse = away from home
        controller.selected = [True]
        controller.at_home = True
        controller.run()  # must not raise
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and controller.state()["running"]:
            time.sleep(0.02)
        self.assertEqual(controller.state()["status"], "Program completed")

    def test_home_sets_at_home_and_motion_clears_it(self):
        controller, _ = self.build()
        controller.connect("FAKE")
        controller.home()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and controller.state()["homing"]:
            time.sleep(0.02)
        self.assertTrue(controller.state()["atHome"])

        controller.set_steps([step(1.0, 60)])
        controller.selected = [True]
        controller.run()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and controller.state()["running"]:
            time.sleep(0.02)
        self.assertFalse(controller.state()["atHome"])

    def test_home_channel_2_sends_0x1B(self):
        """Rack 2 homing was confirmed on hardware as byte 0x1B."""
        controller, fake = self.build()
        controller.connect("FAKE")
        controller.home(2)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and controller.state()["homing"]:
            time.sleep(0.02)
        written = fake.last.written
        self.assertIn(0x1B, written, "rack 2 home byte must be 0x1B")
        self.assertNotIn(0x1A, written, "rack 1 home byte must not be sent")
        self.assertTrue(controller.state()["atHome2"])

    def test_home_all_homes_both_racks(self):
        controller, fake = self.build()
        controller.connect("FAKE")
        controller.home_all()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and controller.state()["homing"]:
            time.sleep(0.02)
        written = fake.last.written
        self.assertTrue(0x19 in written or (0x1A in written and 0x1B in written))
        state = controller.state()
        self.assertTrue(state["atHome"])
        self.assertTrue(state["atHome2"])


if __name__ == "__main__":
    unittest.main()
