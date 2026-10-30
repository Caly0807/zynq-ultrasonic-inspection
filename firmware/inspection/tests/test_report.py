import csv
import hashlib
import json
import tempfile
import time
import unittest
import xml.etree.ElementTree as ElementTree
from dataclasses import asdict, replace
from pathlib import Path

from utinspect.echo import EchoConfig, load_csv, synthetic_waveform, write_lvgl_csv
from utinspect.report import build_report, validate_scan
from utinspect.scan import ReplayCapture, ScanPlan, Scanner, SimulatedStage
from utinspect.vision import Observation


def pose(position_mm, settled_ns, stop_event):
    return Observation(True, "test_only", (70. + position_mm, 0., 0.), time.monotonic_ns(), "DEMO", (0, 1))


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "scan"
        self.plan = ScanPlan(end_mm=25, step_mm=25, settle_s=0)
        self.echo = EchoConfig(calibrated=True, calibration_id="DEMO_TEST_ONLY")
        self.scanner = self.new_scanner(self.output)
        self.scanner.run()

    def new_scanner(self, output, echo=None, capture=None):
        return Scanner(self.plan, echo or self.echo, SimulatedStage(), pose,
                       capture or ReplayCapture(synthetic_waveform(position) for position in self.plan.positions()), output)

    def manifest(self, **changes):
        path = self.output / "manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(changes)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def points(self):
        return [json.loads(line) for line in (self.output / "points.jsonl").read_text(encoding="utf-8").splitlines()]

    def save_points(self, points):
        (self.output / "points.jsonl").write_text("".join(json.dumps(point, ensure_ascii=False) + "\n" for point in points), encoding="utf-8")

    def summary(self, update):
        path = self.output / "summary.csv"
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fields, rows = reader.fieldnames, list(reader)
        update(rows)
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_self_contained_report_keeps_demo_and_source_files_unchanged(self):
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in self.output.rglob("*") if path.is_file()}
        destination = build_report(self.output)
        data = json.loads((destination / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(data["committed_points"], 2)
        self.assertEqual(data["counts"], {"NO_INDICATION": 1, "SUSPECT": 1})
        self.assertEqual(data["display_source_counts"], {"DEMO": 2})
        self.assertIn("DEMO", data["evidence_label"])
        self.assertEqual(data["points"][1]["diagnosis"]["depth_mm"], 16.52)
        self.assertEqual(len(data["source_file_sha256"]), 5)
        document = (destination / "report.html").read_text(encoding="utf-8")
        self.assertIn("单轴扫描复盘报告", document)
        self.assertIn("../waveforms/00001.csv", document)
        self.assertNotIn("<script", document)
        self.assertNotIn("<link", document)
        ElementTree.parse(destination / "scan.svg")
        ElementTree.parse(destination / "waveform.svg")
        for path, digest in before.items():
            self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), digest)

    def test_legacy_records_without_hash_are_explicitly_identified(self):
        points = self.points()
        for point in points:
            point.pop("waveform_sha256")
        self.save_points(points)
        scan = validate_scan(self.output)
        self.assertTrue(any("旧记录中 2 点" in warning for warning in scan.warnings))

    def test_stopped_empty_scan_is_not_a_complete_inspection(self):
        output = Path(self.temporary.name) / "stopped"
        scanner = self.new_scanner(output)
        scanner.stop()
        scanner.run()
        destination = build_report(output)
        data = json.loads((destination / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(data["run_status"], "STOPPED")
        self.assertEqual(data["committed_points"], 0)
        self.assertIn("NO DATA", data["evidence_label"])
        self.assertFalse((destination / "waveform.svg").exists())

    def test_fault_report_contains_only_committed_prefix_and_escaped_error(self):
        output = Path(self.temporary.name) / "partial"
        class FailingCapture:
            def __init__(self):
                self.count = 0
            def capture(self, request_id, stop_event):
                self.count += 1
                if self.count == 2:
                    raise ValueError("<script>test_only_failure</script>")
                return synthetic_waveform(0)
        scanner = self.new_scanner(output, capture=FailingCapture())
        with self.assertRaises(ValueError):
            scanner.run()
        destination = build_report(output)
        data = json.loads((destination / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(data["run_status"], "FAULT")
        self.assertEqual(data["committed_points"], 1)
        self.assertEqual(data["planned_points"], 2)
        self.assertTrue(any("不能视为完整扫描" in warning for warning in data["warnings"]))
        document = (destination / "report.html").read_text(encoding="utf-8")
        self.assertIn("&lt;script&gt;test_only_failure", document)
        self.assertNotIn("<script>", document)

    def test_active_run_is_not_exported(self):
        self.manifest(status="RUNNING")
        with self.assertRaisesRegex(ValueError, "finished"):
            build_report(self.output)
        self.assertFalse((self.output / "report").exists())

    def test_inconsistent_count_is_not_silently_repaired(self):
        self.manifest(status="STOPPED", point_count=1)
        with self.assertRaisesRegex(ValueError, "journal disagree"):
            build_report(self.output)

    def test_complete_with_missing_points_is_rejected(self):
        self.manifest(point_count=1)
        with self.assertRaisesRegex(ValueError, "all planned"):
            validate_scan(self.output)

    def test_summary_disagreement_is_rejected(self):
        self.summary(lambda rows: rows[0].update(stage_x_mm="100"))
        with self.assertRaisesRegex(ValueError, "numeric fields disagree"):
            build_report(self.output)

    def test_traversal_path_is_rejected_even_if_file_exists(self):
        points = self.points()
        points[0]["waveform"] = "../scan/waveforms/00000.csv"
        self.save_points(points)
        with self.assertRaisesRegex(ValueError, "Waveform path"):
            validate_scan(self.output)

    def test_missing_waveform_is_not_replaced_by_latest(self):
        (self.output / "waveforms/00000.csv").unlink()
        with self.assertRaisesRegex(ValueError, "Missing or unsafe"):
            build_report(self.output)

    def test_modified_raw_file_is_caught_by_committed_hash(self):
        path = self.output / "waveforms/00000.csv"
        with path.open("ab") as stream:
            stream.write(b"\n")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            build_report(self.output)

    def test_point_id_from_other_run_is_rejected(self):
        points = self.points()
        points[0]["point_id"] = "other_00000"
        self.save_points(points)
        with self.assertRaisesRegex(ValueError, "Point ID"):
            validate_scan(self.output)

    def test_nonfinite_journal_is_rejected_before_output(self):
        points = self.points()
        points[0]["diagnosis"]["reason"] = float("nan")
        self.save_points(points)
        with self.assertRaisesRegex(ValueError, "journal line"):
            build_report(self.output)
        self.assertFalse((self.output / "report").exists())

    def test_unfinished_jsonl_is_not_automatically_truncated(self):
        with (self.output / "points.jsonl").open("a", encoding="utf-8") as stream:
            stream.write('{"point_id":')
        with self.assertRaisesRegex(ValueError, "no automatic repair"):
            validate_scan(self.output)

    def test_uncalibrated_data_have_no_reported_depths(self):
        output = Path(self.temporary.name) / "uncalibrated"
        self.new_scanner(output, echo=EchoConfig()).run()
        destination = build_report(output)
        data = json.loads((destination / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(data["counts"], {"UNCALIBRATED": 2})
        self.assertTrue(all(point["diagnosis"]["depth_mm"] is None for point in data["points"]))
        self.assertTrue(any("当前参数未标定" in warning for warning in data["warnings"]))

    def test_false_calibration_claim_is_rejected(self):
        manifest = self.manifest()
        echo = {**manifest["echo"], "calibrated": False}
        self.manifest(echo=echo)
        with self.assertRaisesRegex(ValueError, "Calibration metadata"):
            validate_scan(self.output)

    def test_live_history_is_displayed_as_recorded_not_new_capture(self):
        points = self.points()
        manifest = self.manifest()
        self.manifest(stage="MANUAL", echo={**manifest["echo"], "calibration_id": "TEST_FIXTURE_NOT_PHYSICAL"})
        for point in points:
            point["source"] = "LIVE"
            point["vision"]["source"] = "LIVE"
            point["vision_after_capture"]["source"] = "LIVE"
            point["diagnosis"].update(source="LIVE", status=point["diagnosis"]["status"].removeprefix("DEMO_"), calibration_id="TEST_FIXTURE_NOT_PHYSICAL")
            path = self.output / point["waveform"]
            write_lvgl_csv(path, replace(load_csv(path), source="LIVE"))
            point["waveform_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.save_points(points)
        def update(rows):
            for row in rows:
                row.update(source="LIVE", status=row["status"].removeprefix("DEMO_"))
        self.summary(update)
        destination = build_report(self.output)
        data = json.loads((destination / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(data["display_source_counts"], {"RECORDED": 2})
        self.assertTrue(all(point["display_source"] == "RECORDED" and point["source"] == "LIVE" for point in data["points"]))
        self.assertTrue(data["evidence_label"].startswith("RECORDED"))

    def test_calibration_text_cannot_inject_active_html(self):
        calibration_id = '<script>alert("test_only")</script>'
        manifest = self.manifest()
        self.manifest(echo={**manifest["echo"], "calibration_id": calibration_id})
        points = self.points()
        for point in points:
            point["diagnosis"]["calibration_id"] = calibration_id
        self.save_points(points)
        destination = build_report(self.output)
        document = (destination / "report.html").read_text(encoding="utf-8")
        self.assertIn("&lt;script&gt;", document)
        self.assertNotIn("<script>", document)

    def test_conflicting_destinations_and_existing_reports_are_not_overwritten(self):
        for output in (self.output, self.output.parent, self.output / "waveforms/report"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                build_report(self.output, output)
        build_report(self.output)
        with self.assertRaises(FileExistsError):
            build_report(self.output)

    def test_uncommitted_orphan_waveform_is_not_reported_as_a_point(self):
        write_lvgl_csv(self.output / "waveforms/00002.csv", synthetic_waveform(50))
        scan = validate_scan(self.output)
        self.assertEqual(len(scan.points), 2)
        self.assertTrue(any("1 个未提交" in warning for warning in scan.warnings))

    def test_single_position_plot_uses_finite_coordinates(self):
        self.plan = ScanPlan(start_mm=5, end_mm=5, settle_s=0)
        output = Path(self.temporary.name) / "single"
        self.new_scanner(output).run()
        destination = build_report(output)
        ElementTree.parse(destination / "scan.svg")
        self.assertNotIn("nan", (destination / "scan.svg").read_text(encoding="utf-8").lower())


class SafeEchoParameterTests(unittest.TestCase):
    def test_bad_numeric_and_calibration_types_are_rejected(self):
        cases = ({"sound_speed_mps": True}, {"minimum_snr": "6"}, {"calibration_id": None},
                 {"flaw_gate_us": [False, 8]}, {"backwall_gate_us": None}, {"baseline_gate_us": [1]},
                 {"time_zero_us": "0"}, {"flaw_threshold_codes": float("nan")})
        for configuration in cases:
            with self.subTest(configuration=configuration), self.assertRaises(ValueError):
                EchoConfig(**configuration)


if __name__ == "__main__":
    unittest.main()
