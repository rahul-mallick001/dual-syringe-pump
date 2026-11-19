"""Unit tests for the true dual-channel pump wire protocol and execution."""

from __future__ import annotations

import time
import unittest

from pump import device as device_mod
from pump import engine
from pump import protocol
try:
    from fake_pump import FakeFactory
except ImportError:
    from tests.fake_pump import FakeFactory


class DualFramingTests(unittest.TestCase):
    def test_build_pump_frame_a(self):
        frame = protocol.build_pump_frame("A", tdsp=0x1234, duration_seconds=10.0, prescaler=1)
        self.assertEqual(len(frame), 9)
        self.assertEqual(frame[0], protocol.OPENER_PUMP_A)  # 0x0A
        self.assertEqual(frame[1], 0x12)
        self.assertEqual(frame[2], 0x34)
        # 10.0 * 100 = 1000 = 0x000003E8
        self.assertEqual(frame[3:7], [0x00, 0x00, 0x03, 0xE8])
        self.assertEqual(frame[7], 0x00)
        self.assertEqual(frame[8], 0x01)

    def test_build_pump_frame_b(self):
        frame = protocol.build_pump_frame("B", tdsp=0x5678, duration_seconds=5.0, prescaler=8)
        self.assertEqual(len(frame), 9)
        self.assertEqual(frame[0], protocol.OPENER_PUMP_B)  # 0x28
        self.assertEqual(frame[1], 0x56)
        self.assertEqual(frame[2], 0x78)
        # 5.0 * 100 = 500 = 0x000001F4
        self.assertEqual(frame[3:7], [0x00, 0x00, 0x01, 0xF4])
        self.assertEqual(frame[7], 0x00)
        self.assertEqual(frame[8], 0x08)

    def test_home_byte_mapping(self):
        self.assertEqual(protocol.home_byte("both"), protocol.CMD_HOME_BOTH)
        self.assertEqual(protocol.home_byte("all"), protocol.CMD_HOME_BOTH)
        self.assertEqual(protocol.home_byte("A"), protocol.CMD_HOME_A)
        self.assertEqual(protocol.home_byte(1), protocol.CMD_HOME_A)
        self.assertEqual(protocol.home_byte("1"), protocol.CMD_HOME_A)
        self.assertEqual(protocol.home_byte("B"), protocol.CMD_HOME_B)
        self.assertEqual(protocol.home_byte(2), protocol.CMD_HOME_B)
        self.assertEqual(protocol.home_byte("2"), protocol.CMD_HOME_B)

    def test_trigger_bytes(self):
        self.assertEqual(protocol.TRIGGER_FORWARD, 0x0F)
        self.assertEqual(protocol.TRIGGER_REVERSE, 0x37)


class DualOutcomeTests(unittest.TestCase):
    def test_forward_outcomes(self):
        factory = FakeFactory()
        dev = device_mod.PumpDevice("COM_TEST", serial_factory=factory)
        dev.open()
        
        # Inject forward completion bytes
        fake_ser = factory.last
        fake_ser._rx.extend([
            protocol.ACK_PUMP_A_DONE_FWD,
            protocol.ACK_PUMP_B_DONE_FWD,
            protocol.ACK_ALL_DONE,
        ])
        
        outcomes = dev.wait_dual_outcome(expected_s=0.2, withdrawal=False)
        self.assertTrue(outcomes["a_done"])
        self.assertTrue(outcomes["b_done"])
        self.assertTrue(outcomes["all_done"])
        self.assertFalse(outcomes["timeout"])

    def test_reverse_limits(self):
        factory = FakeFactory()
        dev = device_mod.PumpDevice("COM_TEST", serial_factory=factory)
        dev.open()
        
        fake_ser = factory.last
        fake_ser._rx.extend([
            protocol.ACK_HOME_LIMIT_A,
            protocol.ACK_HOME_LIMIT_B,
            protocol.ACK_ALL_DONE,
        ])
        
        outcomes = dev.wait_dual_outcome(expected_s=0.2, withdrawal=True)
        self.assertTrue(outcomes["home_limit_a"])
        self.assertTrue(outcomes["home_limit_b"])
        self.assertTrue(outcomes["all_done"])


class DualEngineTests(unittest.TestCase):
    def test_dual_homing(self):
        factory = FakeFactory()
        ctrl = engine.PumpController(
            device_factory=lambda port: device_mod.PumpDevice(port=port, serial_factory=factory)
        )
        ctrl.connect("COM_TEST")
        
        # Home both
        ctrl.home("both")
        time.sleep(0.1)
        self.assertIn(protocol.CMD_HOME_BOTH, factory.last.written)
        self.assertTrue(ctrl.at_home)
        self.assertTrue(ctrl.at_home2)

        # Home rack 2 only
        ctrl.mark_at_home(False, "both")
        ctrl.home("B")
        time.sleep(0.1)
        self.assertIn(protocol.CMD_HOME_B, factory.last.written)
        self.assertFalse(ctrl.at_home)
        self.assertTrue(ctrl.at_home2)

    def test_dual_run_execution(self):
        factory = FakeFactory()
        ctrl = engine.PumpController(
            device_factory=lambda port: device_mod.PumpDevice(port=port, serial_factory=factory)
        )
        ctrl.connect("COM_TEST")
        
        # Set dual params for A and B
        ctrl.update_pump("A", {
            "diameter": 14.5,
            "flowRate": 5.0,
            "flowRateUnit": 3,  # ml/min
            "durationType": 0,  # Duration
            "duration": 2.0,
            "durationUnit": 0,  # Second
            "enabled": True,
        })
        ctrl.update_pump("B", {
            "diameter": 14.5,
            "flowRate": 10.0,
            "flowRateUnit": 3,  # ml/min
            "durationType": 0,
            "duration": 2.0,
            "durationUnit": 0,
            "enabled": True,
        })
        
        written_start = len(factory.last.written)
        ctrl.run(mode="dual")
        if ctrl._thread:
            ctrl._thread.join(timeout=2.0)
        
        written = factory.last.written[written_start:]
        # Must have frame A opener 0x0A
        self.assertIn(protocol.OPENER_PUMP_A, written)
        # Must have frame B opener 0x28
        self.assertIn(protocol.OPENER_PUMP_B, written)
        # Must have trigger forward 0x0F
        self.assertIn(protocol.TRIGGER_FORWARD, written)
        # 0x23 (single-step program start) must NOT be present in dual mode!
        self.assertNotIn(protocol.CMD_PROGRAM_START, written)
        
        # State should report completed
        self.assertEqual(ctrl.status, "Program completed")
        self.assertEqual(ctrl.status_a, "Program Completed")
        self.assertEqual(ctrl.status_b, "Program Completed")

    def test_dual_run_safety_limitation(self):
        factory = FakeFactory()
        ctrl = engine.PumpController(
            device_factory=lambda port: device_mod.PumpDevice(port=port, serial_factory=factory)
        )
        ctrl.connect("COM_TEST")
        # Oversized volume on Pump 1: 5 ml/min for 600 s = 50 ml (> 20 ml limit)
        ctrl.update_pump("A", {
            "diameter": 14.5,
            "flowRate": 5.0,
            "flowRateUnit": 3,
            "durationType": 0,
            "duration": 600.0,
            "durationUnit": 0,
            "enabled": True,
        })
        with self.assertRaises(RuntimeError) as ctx:
            ctrl.run(mode="dual")
        self.assertIn("above the", str(ctx.exception))
