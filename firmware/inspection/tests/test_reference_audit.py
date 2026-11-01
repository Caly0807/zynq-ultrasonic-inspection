import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from utinspect.reference_audit import audit_run, read_ila, render_report


class ReferenceAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.acquisition = {"source": "PHYSICAL_ADC_ILA", "frame_count": 1,
                            "ila_sample_period_ns": 20, "adc_sample_period_ns": 40,
                            "ila_samples_per_frame": 4096,
                            "specimen_role": "TEST_FIXTURE_NOT_HARDWARE_EVIDENCE"}
        (self.root / "acquisition.json").write_text(json.dumps(self.acquisition))
        self.path = self.root / "raw_ila_001.csv"
        with self.path.open("w", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(["Sample in Buffer", "TRIGGER", "ad_data_r[7:0]", "ad_otr_r", "gain_val_1[7:0]"])
            writer.writerow(["Radix - UNSIGNED", "UNSIGNED", "HEX", "HEX", "HEX"])
            for index in range(4096):
                value = 200 if index in (512 + 240, 512 + 550) else 128
                writer.writerow([index, int(index == 512), f"{value:02X}", 0, "80"])

    def test_explicit_ila_period_and_historical_source_are_preserved(self):
        frame = read_ila(self.path, self.acquisition)
        self.assertEqual(frame.sample_period_us, .02)
        self.assertEqual(frame.source, "RECORDED")
        self.assertEqual(frame.trigger_index, 512)

    def test_default_false_indication_is_reported_not_approved(self):
        original = self.path.read_bytes()
        audit, frames = audit_run(self.root)
        self.assertEqual(audit["default_candidate_counts"], {"SUSPECT": 1})
        self.assertFalse(audit["calibrated"])
        self.assertFalse(audit["accepted_as_calibration"])
        self.assertFalse(audit["real_defect_detection_verified"])
        self.assertIn("HELD_ADC_REGISTER", " ".join(audit["warnings"]))
        self.assertEqual(self.path.read_bytes(), original)
        report = render_report(audit, frames)
        self.assertIn("未标定", report)
        self.assertIn("不是两块缺陷工件检出报告", report)

    def test_changed_raw_checksum_is_rejected(self):
        (self.root / "quality.json").write_text(json.dumps({"frames": [{"filename": self.path.name, "sha256": "0" * 64}]}))
        with self.assertRaisesRegex(ValueError, "checksum"):
            audit_run(self.root)

    def test_matching_checksum_is_verified(self):
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        (self.root / "quality.json").write_text(json.dumps({"frames": [{"filename": self.path.name, "sha256": digest}]}))
        audit, _ = audit_run(self.root)
        self.assertTrue(audit["frames"][0]["preserved_checksum_matches"])

    def test_missing_timebase_and_provenance_are_rejected(self):
        for changed in ({"ila_sample_period_ns": None}, {"adc_sample_period_ns": 30}, {"source": "LIVE"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                read_ila(self.path, dict(self.acquisition, **changed))

    def test_missing_or_extra_frames_are_rejected(self):
        self.path.rename(self.root / "unknown.csv")
        with self.assertRaisesRegex(ValueError, "count"):
            audit_run(self.root)


if __name__ == "__main__":
    unittest.main()
