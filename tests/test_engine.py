"""End-to-end engine tests against a fake controller (no hardware)."""

from __future__ import annotations

import time
import unittest

from pump.device import PumpDevice
from pump.engine import PumpController
from pump.program import DurationType, DurationUnit, FlowRateUnit, ProgramData

from fake_pump import FakeFactory

# 20 mm, 5 ml/min -> tdsp 6710 (0x1A36); 60 s -> 6000 (0x00001770); prescaler 1
FRAME_5MLMIN_60S = [0x0A, 0x1A, 0x36, 0x00, 0x00, 0x17, 0x70, 0x00, 0x01, 0x0F]


def step(flow: float = 5.0, seconds: int = 60) -> ProgramData:
    return ProgramData(
        diameter=20.0,
        flow_rate=flow,
        flow_rate_unit=FlowRateUnit.ML_MIN,
        duration_type=DurationType.DURATION,
        duration=float(seconds),
        duration_unit=DurationUnit.SECOND,
    )


class EngineTests(unittest.TestCase):
    def build(self, silent: bool = False) -> PumpController:
        self.fake = FakeFactory(silent=silent)

        def factory(port: str) -> PumpDevice:
            return PumpDevice(
                port=port,
                serial_factory=self.fake,
                ack_timeout_ms=80,
                connect_timeout_ms=80,
            )

        return PumpController(device_factory=factory)

    @staticmethod
    def wait_idle(controller: PumpController, timeout: float = 12.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = controller.state()
            if not state["running"] and not state["homing"]:
                return True
            time.sleep(0.02)
        return False

    # -- framing -----------------------------------------------------------
    def test_run_emits_exact_byte_sequence(self):
        controller = self.build()
        controller.connect("FAKE")
        controller.set_steps([step(), step()])
        controller.set_withdrawal(False)

        controller.run()
        self.assertTrue(self.wait_idle(controller))
        self.assertEqual(controller.state()["status"], "Program completed")

        written = self.fake.last.written
        expected = (
            [0x05, 0x05, 0x23, 0x02, 0x02]  # probe, worker probe, start, steps, dir
            + FRAME_5MLMIN_60S
            + FRAME_5MLMIN_60S
        )
        self.assertEqual([f"{b:02X}" for b in written], [f"{b:02X}" for b in expected])

    def test_open_loop_direction_once_when_steps_agree(self):
        controller = self.build()
        controller.connect("FAKE")
        controller.set_steps([step(), step(), step()])
        controller.set_withdrawal(False)
        controller.run()
        self.assertTrue(self.wait_idle(controller))

        written = self.fake.last.written
        # a single direction byte (0x02) because every step agrees
        self.assertEqual(written.count(0x02), 1)
        self.assertEqual(written[3], 0x03)        # step count = 3

    # -- stop --------------------------------------------------------------
    def test_stop_terminates_promptly(self):
        controller = self.build(silent=True)
        controller.connect("FAKE")
        controller.set_steps([step(seconds=60)])
        controller.set_withdrawal(False)

        controller.run()
        time.sleep(0.3)
        started = time.monotonic()
        controller.stop()
        self.assertTrue(self.wait_idle(controller, timeout=6.0))
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 2.0, "stop must recover quickly")
        self.assertEqual(controller.state()["status"], "Program Terminated")

    # -- no acknowledgement from the controller ----------------------------
    def test_silent_controller_finishes_on_duration(self):
        controller = self.build(silent=True)
        controller.connect("FAKE")
        controller.set_steps([step(seconds=1)])
        controller.set_withdrawal(False)

        controller.run()
        self.assertTrue(self.wait_idle(controller, timeout=12.0))
        state = controller.state()
        self.assertEqual(state["status"], "Program completed")
        self.assertIsNone(state["error"])
        self.assertTrue(
            any("open-loop" in line for line in state["trace"]),
            "expected an open-loop warning in the trace",
        )

    # -- not connected -----------------------------------------------------
    def test_run_without_device_refuses_to_start(self):
        controller = self.build()
        controller.set_steps([step()])
        with self.assertRaises(RuntimeError) as ctx:
            controller.run()
        self.assertIn("not connected", str(ctx.exception).lower())
        # nothing was spawned, so the UI stays responsive
        self.assertFalse(controller.state()["running"])

    def test_home_without_device_refuses_to_start(self):
        controller = self.build()
        with self.assertRaises(RuntimeError):
            controller.home()
        self.assertFalse(controller.state()["homing"])

    # -- home --------------------------------------------------------------
    def test_home_stops_at_home_limit(self):
        controller = self.build()
        controller.connect("FAKE")
        controller.home()
        self.assertTrue(self.wait_idle(controller))
        self.assertEqual(controller.state()["status"], "Home Limit Reached (rack 1)")
        self.assertIn(0x1A, self.fake.last.written)

    # -- per-step direction ------------------------------------------------
    def test_alternating_steps_resend_direction(self):
        controller = self.build()
        controller.connect("FAKE")
        first = step()
        second = step()
        first.withdrawal = False
        second.withdrawal = True
        controller.set_steps([first, second])
        controller.selected = [True, True]
        controller.steps[0].pumb_status = True
        controller.steps[1].pumb_status = True

        controller.run()
        self.assertTrue(self.wait_idle(controller))

        written = self.fake.last.written
        # direction byte appears before each frame
        self.assertIn(0x02, written)
        self.assertIn(0x01, written)
        # both direction bytes must appear after the step count
        first_dir = written.index(0x02, 3)
        second_dir = written.index(0x01, first_dir + 1)
        self.assertLess(first_dir, second_dir)

    def test_curve_run_subsecond_slices(self):
        controller = self.build()
        controller.connect("FAKE")
        controller.set_mode("curve")
        controller.set_curve_config({
            "yMode": "velocity",
            "unit": "mL/min",
            "timeUnit": "s",
            "duration": 1.0,
            "sliceDurationS": 0.25,
            "channelA": {
                "enabled": True,
                "diameter": 14.5,
                "source": "preset",
                "preset": "constant",
                "amplitude": 2.0,
                "offset": 0.0,
            },
            "channelB": {
                "enabled": False,
                "diameter": 14.5,
            },
        })
        controller.run()
        self.assertTrue(self.wait_idle(controller))
        self.assertEqual(controller.state()["status"], "Program completed")
        written = self.fake.last.written
        openers = [b for b in written if b == 0x0A]
        self.assertGreaterEqual(len(openers), 4)



if __name__ == "__main__":
    unittest.main()
