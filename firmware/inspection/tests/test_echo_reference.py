import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from utinspect.echo import Waveform, analyze, live_echo_config
from utinspect.echo_reference import NormalReference, load_normal_reference


class EchoReferenceTests(unittest.TestCase):
    def setUp(self):
        envelope = [0.] * 4096
        envelope[63] = 65.
        self.reference = NormalReference("NORMAL-TEST-ONLY", tuple(envelope), .04, 0, 128,
                                         estimated_time_zero_us=.55)
        samples = [128] * 4096
        samples[63], samples[268] = 190, 195
        self.frame = Waveform(tuple(samples), 0, .04, "LIVE")

    def test_known_normal_interference_is_not_the_reported_reflector(self):
        result = analyze(self.frame, live_echo_config(), self.reference)
        self.assertEqual(result["status"], "UNCALIBRATED")
        self.assertEqual(result["reason"], "normal_reference_overlap")
        self.assertFalse(result["flaw_gate_crossed"])
        self.assertTrue(result["backwall_visible"])
        self.assertEqual(result["raw_flaw"]["time_us"], 2.52)
        self.assertIsNone(result["depth_mm"])
        self.assertNotIn("estimated_depth_mm", result)

    def test_a_new_late_reflector_wins_over_a_larger_known_front_echo(self):
        samples = list(self.frame.samples)
        samples[201] = 188
        result = analyze(replace(self.frame, samples=tuple(samples)), live_echo_config(), self.reference)
        self.assertTrue(result["flaw_gate_crossed"])
        self.assertAlmostEqual(result["flaw"]["time_us"], 8.04)
        self.assertAlmostEqual(result["estimated_depth_mm"], 22.0955)
        self.assertEqual(result["status"], "UNCALIBRATED")
        self.assertFalse(result["calibrated"])
        self.assertIsNone(result["depth_mm"])

    def test_a_reference_never_suppresses_adc_clipping(self):
        samples = list(self.frame.samples)
        samples[63] = 255
        result = analyze(replace(self.frame, samples=tuple(samples)), live_echo_config(), self.reference)
        self.assertEqual(result["reason"], "adc_overrange_or_clipping")
        self.assertEqual(result["status"], "INVALID")

    def test_gain_clock_or_trigger_mismatch_rejects_reference_comparison(self):
        for frame in (replace(self.frame, gain=192), replace(self.frame, sample_period_us=.05),
                      replace(self.frame, trigger_index=1)):
            with self.subTest(frame=frame.gain):
                result = analyze(frame, live_echo_config(), self.reference)
                self.assertEqual(result["reason"], "normal_reference_acquisition_mismatch")
                self.assertEqual(result["status"], "INVALID")

    def test_plain_live_default_cannot_invent_a_zero_estimate(self):
        result = analyze(self.frame, live_echo_config())
        self.assertNotIn("estimated_depth_mm", result)

    def test_profile_roundtrip_does_not_approve_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.json"
            payload = self.reference.payload()
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(load_normal_reference(path), self.reference)
            payload["calibration_approved"] = True
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_normal_reference(path)
