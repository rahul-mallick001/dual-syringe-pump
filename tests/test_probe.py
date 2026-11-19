"""Tests for the axis profiles and the protocol probe harness.

The probe's *hardware* behaviour obviously cannot be tested here, but its
scoring logic and the byte sequences it generates can - and those are where a
mistake would quietly produce a false result.
"""

from __future__ import annotations

import unittest

from pump import probe, protocol


class AxisProfileTests(unittest.TestCase):
    def test_axis_a_matches_the_confirmed_trace(self):
        """AXIS_A must reproduce the step-1 frame captured from the real pump."""
        frame = protocol.send_values_frame(33549, 60, 1)
        self.assertEqual(
            [byte for byte, _ in frame],
            [0x0A, 0x83, 0x0D, 0x00, 0x00, 0x17, 0x70, 0x00, 0x01, 0x0F],
        )

    def test_explicit_profile_equals_default(self):
        implicit = protocol.send_values_frame(33549, 60, 1)
        explicit = protocol.send_values_frame(33549, 60, 1, profile=protocol.AXIS_A)
        self.assertEqual(implicit, explicit)

    def test_axis_b_is_not_marked_verified(self):
        """We must never present an unproven axis as working."""
        self.assertTrue(protocol.AXIS_A.flow_verified)
        self.assertFalse(protocol.AXIS_B.flow_verified)

    def test_profile_for_accepts_all_spellings(self):
        for value in (1, "1", "a", "A", " A "):
            self.assertIs(protocol.profile_for(value), protocol.AXIS_A, value)
        for value in (2, "2", "b", "B"):
            self.assertIs(protocol.profile_for(value), protocol.AXIS_B, value)

    def test_profile_for_rejects_nonsense(self):
        with self.assertRaises(ValueError):
            protocol.profile_for("C")

    def test_home_bytes(self):
        self.assertEqual(protocol.AXIS_A.home, 0x1A)
        self.assertEqual(protocol.AXIS_B.home, 0x1B)

    def test_withdrawal_follows_profile(self):
        self.assertEqual(protocol.withdrawal_byte(True), 0x01)
        self.assertEqual(protocol.withdrawal_byte(False), 0x02)
        self.assertEqual(protocol.withdrawal_byte(True, protocol.AXIS_B), 0x03)
        self.assertEqual(protocol.withdrawal_byte(False, protocol.AXIS_B), 0x04)


class FrameBytesTests(unittest.TestCase):
    def test_frame_length_and_delimiters(self):
        frame = probe.frame_bytes(0x0B, 0x10)
        self.assertEqual(len(frame), 10)
        self.assertEqual(frame[0], 0x0B)
        self.assertEqual(frame[-1], 0x10)

    def test_duration_encoding(self):
        # 12 s * 100 = 1200 = 0x000004B0
        frame = probe.frame_bytes(0x0A, 0x0F, duration_s=12)
        self.assertEqual(frame[3:7], [0x00, 0x00, 0x04, 0xB0])

    def test_tdsp_encoding(self):
        # 2237 = 0x08BD
        frame = probe.frame_bytes(0x0A, 0x0F, tdsp=2237)
        self.assertEqual(frame[1:3], [0x08, 0xBD])

    def test_prescaler_encoding(self):
        frame = probe.frame_bytes(0x0A, 0x0F, prescaler=1024)
        self.assertEqual(frame[7:9], [0x04, 0x00])


class AxisARunSequenceTests(unittest.TestCase):
    def test_starts_with_device_and_program_start(self):
        seq = probe.axis_a_run_sequence()
        self.assertEqual(seq[0], protocol.CMD_DEVICE)
        self.assertEqual(seq[1], protocol.CMD_PROGRAM_START)

    def test_ends_with_the_proven_frame(self):
        seq = probe.axis_a_run_sequence()
        self.assertEqual(
            seq[-10:],
            [0x0A, 0x08, 0xBD, 0x00, 0x00, 0x04, 0xB0, 0x00, 0x01, 0x0F],
        )


class TrialTests(unittest.TestCase):
    def test_the_original_hypotheses_are_still_present(self):
        keys = [trial.key for trial in probe.build_trials()]
        for expected in ("h1", "h2", "h3", "h4", "h5", "h6", "h7"):
            self.assertIn(expected, keys)
        self.assertEqual(len(keys), len(set(keys)), "trial keys must be unique")

    def test_round_two_trials_use_the_real_opener(self):
        """0x0B does not start a frame - P1/P3 proved it on hardware. The
        round-2 trials that are meant to be taken seriously must therefore use
        the real 0x0A opener. (The earlier h1/h3/p1/p2/p3 sequences keep 0x0B
        on purpose: they are the evidence that it does not work.)"""
        trials = {trial.key: trial for trial in probe.build_trials()}
        for key in ("t1", "t2"):
            self.assertIn(
                protocol.CMD_ACK_OK, trials[key].sequence,
                f"{key} must use the 0x0A frame opener",
            )

    def test_any_trial_carrying_a_frame_waits_for_it(self):
        """A frame trial must settle for at least its own step duration."""
        for trial in probe.build_trials():
            if len(trial.sequence) > 8:      # carries a SendValues payload
                self.assertGreaterEqual(
                    trial.settle_s, probe.TRIAL_DURATION_S, trial.key
                )

    def test_every_trial_begins_with_a_device_probe(self):
        for trial in probe.build_trials():
            self.assertEqual(trial.sequence[0], protocol.CMD_DEVICE, trial.key)

    def test_no_trial_addresses_axis_a_as_its_target(self):
        for trial in probe.build_trials():
            self.assertEqual(trial.target_axis, "B", trial.key)

    def test_h2_sets_speed_before_moving(self):
        """The whole point of H2: speed must precede the move byte."""
        trial = {t.key: t for t in probe.build_trials()}["h2"]
        self.assertIn(protocol.CMD_MOTOR_SPEED_XY, trial.sequence)
        self.assertLess(
            trial.sequence.index(protocol.CMD_MOTOR_SPEED_XY),
            trial.sequence.index(0x6F),
        )


class DetectorTests(unittest.TestCase):
    """The detector decides whether a candidate worked, so it must be strict."""

    def _result(self, baseline, after):
        return probe.TrialResult(
            trial=probe.build_trials()[0], baseline_s=baseline, after_s=after
        )

    def test_clear_delay_counts_as_movement(self):
        self.assertTrue(self._result(0.2, 1.5).moved)

    def test_small_jitter_is_not_movement(self):
        self.assertFalse(self._result(0.20, 0.30).moved)

    def test_shorter_after_is_not_movement(self):
        self.assertFalse(self._result(1.5, 0.2).moved)

    def test_missing_measurement_is_inconclusive_not_movement(self):
        self.assertFalse(self._result(None, 1.5).moved)
        self.assertFalse(self._result(0.2, None).moved)
        self.assertFalse(self._result(None, None).moved)

    def test_unmeasurable_after_state_is_inconclusive(self):
        """A missing post-trial ack must not be reported as 'did not move'."""
        self.assertTrue(self._result(0.2, None).inconclusive)
        self.assertFalse(self._result(0.2, 0.3).inconclusive)
        self.assertIn("INCONCLUSIVE", self._result(0.2, None).describe())

    def test_noise_threshold_is_respected(self):
        just_under = probe.LATENCY_NOISE_S - 0.01
        just_over = probe.LATENCY_NOISE_S + 0.01
        self.assertFalse(self._result(0.0, just_under).moved)
        self.assertTrue(self._result(0.0, just_over).moved)


if __name__ == "__main__":
    unittest.main()
