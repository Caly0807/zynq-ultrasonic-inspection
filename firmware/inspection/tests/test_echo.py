import csv
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from utinspect.echo import EchoConfig, Waveform, analyze, load_csv, synthetic_waveform, write_lvgl_csv


class EchoTests(unittest.TestCase):
    def setUp(self):
        self.approved = EchoConfig(calibrated=True, calibration_id="TEST_ONLY")

    def test_uncalibrated_never_reports_no_indication(self):
        frame = replace(synthetic_waveform(0), source="RECORDED")
        result = analyze(frame, EchoConfig())
        self.assertEqual(result["status"], "UNCALIBRATED")
        self.assertIsNone(result["depth_mm"])

    def test_calibrated_flaw_and_depth(self):
        frame = replace(synthetic_waveform(25), source="RECORDED")
        result = analyze(frame, self.approved)
        self.assertEqual(result["status"], "SUSPECT")
        self.assertAlmostEqual(result["depth_mm"], 16.52, delta=0.2)

    def test_visible_backwall_without_flaw_is_not_a_certification(self):
        result = analyze(replace(synthetic_waveform(0), source="RECORDED"), self.approved)
        self.assertEqual(result["status"], "NO_INDICATION")
        self.assertTrue(result["backwall_visible"])

    def test_demo_stays_demo_even_with_approved_test_parameters(self):
        result = analyze(synthetic_waveform(25), self.approved)
        self.assertEqual(result["status"], "DEMO_SUSPECT")
        self.assertEqual(result["source"], "DEMO")

    def test_missing_echo_is_invalid_not_healthy(self):
        frame = Waveform((128,) * 4096, 256, 0.04, "RECORDED")
        result = analyze(frame, self.approved)
        self.assertEqual(result["status"], "INVALID")
        self.assertIn("coupling", result["reason"])

    def test_clipping_and_overrange_rejected(self):
        frame = replace(synthetic_waveform(25), source="RECORDED")
        samples = list(frame.samples)
        samples[256 + 140] = 255
        for damaged in (replace(frame, samples=tuple(samples)), replace(frame, overrange=True)):
            with self.subTest(damaged=damaged.overrange):
                self.assertEqual(analyze(damaged, self.approved)["status"], "INVALID")

    def test_no_pretrigger_requires_an_explicit_quiet_gate(self):
        original = synthetic_waveform(25)
        frame = replace(original, samples=original.samples[256:] + (128,) * 256, trigger_index=0, source="RECORDED")
        self.assertEqual(analyze(frame, self.approved)["status"], "INVALID")
        config = replace(self.approved, baseline_gate_us=(100., 110.))
        self.assertEqual(analyze(frame, config)["status"], "SUSPECT")

    def test_gates_outside_frame_rejected(self):
        frame = Waveform((128,) * 64, 32, 0.04, "RECORDED")
        self.assertEqual(analyze(frame, self.approved)["status"], "INVALID")

    def test_bad_configuration_rejected(self):
        for values in ({"calibrated": True}, {"minimum_snr": float("nan")}, {"flaw_gate_us": (8., 9.)},
                       {"time_zero_us": -1}, {"baseline_gate_us": (3., 4.)}, {"calibrated": "false"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                EchoConfig(**values)

    def test_bad_samples_rejected(self):
        for samples in ((256,) * 64, (True,) * 64, (128.0,) * 64):
            with self.subTest(sample=samples[0]), self.assertRaises(ValueError):
                Waveform(samples, 32, 0.04, "RECORDED")

    def test_csv_demo_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame.csv"
            original = synthetic_waveform(25)
            write_lvgl_csv(path, original)
            loaded = load_csv(path)
            self.assertEqual(loaded.samples, original.samples)
            self.assertEqual(loaded.trigger_index, 256)
            self.assertEqual(loaded.source, "DEMO")
            self.assertEqual(loaded.sample_period_us, 0.04)

    def test_imported_live_csv_is_historical(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame.csv"
            write_lvgl_csv(path, replace(synthetic_waveform(0), source="LIVE"))
            self.assertEqual(load_csv(path).source, "RECORDED")

    def test_inconsistent_sample_period_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame.csv"
            write_lvgl_csv(path, synthetic_waveform(0))
            with path.open(newline="") as stream:
                rows = list(csv.reader(stream))
            rows[20][-2] = "20"
            with path.open("w", newline="") as stream:
                csv.writer(stream).writerows(rows)
            with self.assertRaises(ValueError):
                load_csv(path)

    def test_existing_4096_sample_lvgl_contract(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            write_lvgl_csv(Path(directory) / "bad.csv", Waveform((128,) * 64, 32, 0.04, "RECORDED"))


if __name__ == "__main__":
    unittest.main()
