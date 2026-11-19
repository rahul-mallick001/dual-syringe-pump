"""Unit tests for the wire framing (no hardware required)."""

from __future__ import annotations

import unittest

from pump import protocol


class EncodingTests(unittest.TestCase):
    def test_u16_big_endian(self):
        self.assertEqual(protocol.encode_u16(0x0102), b"\x01\x02")
        self.assertEqual(protocol.encode_u16(1), b"\x00\x01")
        self.assertEqual(protocol.encode_u16(1024), b"\x04\x00")

    def test_u32_big_endian(self):
        self.assertEqual(protocol.encode_u32(6000), b"\x00\x00\x17\x70")
        self.assertEqual(protocol.encode_u32(1), b"\x00\x00\x00\x01")
        self.assertEqual(protocol.encode_u32(0x01020304), b"\x01\x02\x03\x04")


class FrameTests(unittest.TestCase):
    def test_sample_step_one_frame(self):
        """Step 1 of sample prog.txt: tdsp=33549, 60 s, prescaler 1."""
        frame = protocol.send_values_frame(33549, 60, 1)
        values = [value for value, _ in frame]
        self.assertEqual(
            values,
            [0x0A, 0x83, 0x0D, 0x00, 0x00, 0x17, 0x70, 0x00, 0x01, 0x0F],
        )
        # every byte but the final GO expects an acknowledgement
        self.assertTrue(all(expect for _, expect in frame[:-1]))
        self.assertFalse(frame[-1][1])

    def test_tdsp_split(self):
        frame = protocol.send_values_frame(0x1234, 1, 1)
        values = [value for value, _ in frame]
        self.assertEqual(values[1], 0x12)
        self.assertEqual(values[2], 0x34)

    def test_duration_is_multiplied_by_100(self):
        frame = protocol.send_values_frame(1, 90, 1)
        values = [value for value, _ in frame]
        # 90 * 100 = 9000 = 0x00002328
        self.assertEqual(values[3:7], [0x00, 0x00, 0x23, 0x28])

    def test_prescaler_split(self):
        frame = protocol.send_values_frame(1, 1, 1024)
        values = [value for value, _ in frame]
        self.assertEqual(values[7], 0x04)  # 1024 >> 8
        self.assertEqual(values[8], 0x00)  # 1024 & 0xFF

    def test_frame_length(self):
        frame = protocol.send_values_frame(100, 10, 8)
        self.assertEqual(len(frame), 10)


class WithdrawalTests(unittest.TestCase):
    def test_direction_bytes(self):
        self.assertEqual(protocol.withdrawal_byte(True), protocol.CMD_DIR_PLUS)
        self.assertEqual(protocol.withdrawal_byte(False), protocol.CMD_WITHDRAW_OFF)
        self.assertEqual(protocol.withdrawal_byte(True), 0x01)
        self.assertEqual(protocol.withdrawal_byte(False), 0x02)


class ConstantsTests(unittest.TestCase):
    def test_key_constants(self):
        self.assertEqual(protocol.BAUD_RATE, 19200)
        self.assertEqual(protocol.CMD_DEVICE, 0x05)
        self.assertEqual(protocol.CMD_ACK_OK, 0x0A)
        self.assertEqual(protocol.CMD_STOP, 0x14)
        self.assertEqual(protocol.CMD_HOME, 0x1A)
        self.assertEqual(protocol.ACK_STEP_DONE, 0x1E)
        self.assertEqual(protocol.ACK_FAR_LIMIT, 0x23)
        self.assertEqual(protocol.CMD_PROGRAM_START, 0x23)
        self.assertEqual(protocol.ACK_HOME_LIMIT, 0x32)
        self.assertEqual(protocol.CMD_PROGRAM_GO, 0x0F)


if __name__ == "__main__":
    unittest.main()
