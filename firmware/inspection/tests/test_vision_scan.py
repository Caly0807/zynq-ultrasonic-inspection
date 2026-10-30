import json
import tempfile
import threading
import time
import unittest
from dataclasses import asdict, replace
from pathlib import Path

import cv2
import numpy as np

from utinspect.echo import EchoConfig, synthetic_waveform
from utinspect.scan import ExchangeCapture, ReplayCapture, ScanPlan, Scanner, SimulatedStage
from utinspect.vision import Intrinsics, Observation, Tracker, atomic_json, generate_targets, synthetic_scene


def valid_pose(position_mm, settled_ns, stop_event):
    return Observation(True, "test", (70. + position_mm, 0., 0.), time.monotonic_ns(), "DEMO", (0, 1))


class VisionTests(unittest.TestCase):
    def test_exact_front_parallel_pose_has_positive_depth_and_visible_face(self):
        _, intrinsics = synthetic_scene(0)
        tracker = Tracker(intrinsics)
        corners, _ = cv2.projectPoints(tracker.object_points(30.), np.array([np.pi, 0., 0.]),
                                      np.array([0., 0., 500.]), intrinsics.matrix, intrinsics.distortion)
        error, transform = tracker._pose(corners, 30.)
        self.assertLess(error, 0.01)
        self.assertGreater(transform[2, 3], 0)
        self.assertLess(float(transform[:3, 2] @ transform[:3, 3]), 0)

    def test_actual_marker_detection_and_physical_tip_transform(self):
        for position in (0., 25., 50.):
            with self.subTest(position=position):
                image, intrinsics = synthetic_scene(position)
                tracker = Tracker(intrinsics, tip_offset_mm=(0., 0., -35.), contact_tolerance_mm=5.)
                observation, annotated = tracker.locate(image, "DEMO")
                self.assertTrue(observation.valid, asdict(observation))
                self.assertEqual(set(observation.marker_ids), {0, 1})
                self.assertAlmostEqual(observation.position_mm[0], 70. + position, delta=3.)
                self.assertAlmostEqual(observation.position_mm[2], 0., delta=5.)
                self.assertEqual(annotated.shape, image.shape)

    def test_markers_without_intrinsics_do_not_produce_millimetres(self):
        image, _ = synthetic_scene(0)
        observation, _ = Tracker().locate(image)
        self.assertFalse(observation.valid)
        self.assertEqual(observation.reason, "camera_intrinsics_required")

    def test_missing_marker_invalidates_current_pose(self):
        image = np.full((720, 960, 3), 255, np.uint8)
        observation, _ = Tracker().locate(image)
        self.assertFalse(observation.valid)
        self.assertIsNone(observation.position_mm)

    def test_missing_tip_offset_cannot_be_treated_as_contact_position(self):
        image, intrinsics = synthetic_scene(0)
        observation, _ = Tracker(intrinsics).locate(image)
        self.assertEqual(observation.reason, "physical_marker_to_tip_offset_required")

    def test_changed_image_size_rejected(self):
        image, intrinsics = synthetic_scene(0)
        image = cv2.resize(image, (640, 480))
        observation, _ = Tracker(intrinsics, tip_offset_mm=(0., 0., -35.)).locate(image)
        self.assertEqual(observation.reason, "image_size_differs_from_calibration")

    def test_wrong_probe_height_rejected(self):
        image, intrinsics = synthetic_scene(0)
        observation, _ = Tracker(intrinsics, tip_offset_mm=(0., 0., -5.)).locate(image)
        self.assertEqual(observation.reason, "tip_not_on_calibrated_workpiece_plane")

    def test_invalid_intrinsics_rejected(self):
        with self.assertRaises(ValueError):
            Intrinsics(np.zeros((3, 3)), np.zeros(5), 640, 480)

    def test_print_targets_have_physical_units(self):
        with tempfile.TemporaryDirectory() as directory:
            generate_targets(directory)
            reference = (Path(directory) / "reference_0.svg").read_text()
            checkerboard = (Path(directory) / "checkerboard_9x6.svg").read_text()
            self.assertIn('width="40mm"', reference)
            self.assertIn('width="200mm"', checkerboard)


class RecordingStage(SimulatedStage):
    def __init__(self):
        self.moves, self.stopped = [], False

    def move_to(self, position_mm):
        self.moves.append(position_mm)

    def stop(self):
        self.stopped = True


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "run"
        self.plan = ScanPlan(end_mm=10, step_mm=5, settle_s=0)
        self.echo = EchoConfig(calibrated=True, calibration_id="TEST_ONLY")

    def scanner(self, locate=valid_pose, capture=None):
        self.stage = RecordingStage()
        capture = capture or ReplayCapture(synthetic_waveform(position) for position in self.plan.positions())
        return Scanner(self.plan, self.echo, self.stage, locate, capture, self.output)

    def test_decimal_steps_and_endpoint_without_overshoot(self):
        plan = ScanPlan(start_mm=0, end_mm=1, step_mm=0.3)
        self.assertEqual(list(plan.positions()), [0., 0.3, 0.6, 0.9, 1.])

    def test_soft_limits_and_invalid_step_rejected(self):
        for values in ({"start_mm": -1}, {"step_mm": 0}, {"end_mm": 101}, {"step_mm": 1e-9}, {"max_pose_age_s": float("nan")}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ScanPlan(**values)

    def test_complete_session_preserves_demo_and_records_raw_waveforms(self):
        scanner = self.scanner()
        manifest = scanner.run()
        self.assertEqual(manifest["status"], "COMPLETE")
        self.assertEqual(manifest["point_count"], 3)
        records = [json.loads(line) for line in (self.output / "points.jsonl").read_text().splitlines()]
        self.assertEqual(len(records), 3)
        self.assertTrue(all(record["source"] == "DEMO" for record in records))
        self.assertEqual(self.stage.moves, [0., 5., 10.])
        self.assertTrue(self.stage.stopped)
        self.assertEqual(len(list((self.output / "waveforms").glob("*.csv"))), 3)

    def test_stale_pose_faults_before_acquisition(self):
        def stale(position_mm, settled_ns, stop_event):
            return replace(valid_pose(position_mm, settled_ns, stop_event), timestamp_ns=settled_ns - 1)
        scanner = self.scanner(stale)
        with self.assertRaisesRegex(ValueError, "stale"):
            scanner.run()
        self.assertTrue(self.stage.stopped)
        self.assertFalse((self.output / "points.jsonl").exists())
        self.assertEqual(json.loads((self.output / "manifest.json").read_text())["status"], "FAULT")

    def test_missing_marker_faults_instead_of_reusing_last_position(self):
        def missing(position_mm, settled_ns, stop_event):
            return Observation(False, "missing", None, time.monotonic_ns(), "LIVE", ())
        with self.assertRaisesRegex(ValueError, "Vision rejected"):
            self.scanner(missing).run()

    def test_off_scanline_pose_rejected(self):
        def off_line(position_mm, settled_ns, stop_event):
            return replace(valid_pose(position_mm, settled_ns, stop_event), position_mm=(70. + position_mm, 30., 0.))
        with self.assertRaisesRegex(ValueError, "scan line"):
            self.scanner(off_line).run()

    def test_stop_request_preserves_valid_manifest(self):
        scanner = self.scanner()
        scanner.stop()
        self.assertEqual(scanner.run()["status"], "STOPPED")
        self.assertEqual(self.stage.moves, [])

    def test_existing_run_not_overwritten(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.scanner().run()

    def test_invalid_final_pose_discards_point(self):
        call_count = 0
        def drift(position_mm, settled_ns, stop_event):
            nonlocal call_count
            call_count += 1
            observation = valid_pose(position_mm, settled_ns, stop_event)
            return observation if call_count == 1 else replace(observation, valid=False, reason="lost_during_capture")
        with self.assertRaisesRegex(ValueError, "lost_during_capture"):
            self.scanner(drift).run()
        self.assertFalse((self.output / "points.jsonl").exists())


class ExchangeTests(unittest.TestCase):
    def test_timeout_never_falls_back_to_a_fake_waveform(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = ExchangeCapture(directory, timeout_s=0.04)
            with self.assertRaises(TimeoutError):
                capture.capture("test", threading.Event())
            request = json.loads((Path(directory) / "request_test.json").read_text())
            self.assertIs(request["enable_transmit"], False)

    def test_completed_new_request_and_dma_are_required(self):
        for dma_complete in (False, True):
            with self.subTest(dma_complete=dma_complete), tempfile.TemporaryDirectory() as directory:
                capture = ExchangeCapture(directory, timeout_s=1)
                response_path = Path(directory) / "response_test.json"
                request_path = Path(directory) / "request_test.json"
                def respond():
                    deadline = time.monotonic() + 1
                    while not request_path.exists() and time.monotonic() < deadline:
                        time.sleep(0.005)
                    frame = replace(synthetic_waveform(0), source="LIVE", frame_id="unique-frame", acquired_monotonic_ns=time.monotonic_ns())
                    atomic_json(response_path, {"schema": 1, "request_id": "test", "capture_complete": True,
                                               "dma_complete": dma_complete, "frame": asdict(frame)})
                worker = threading.Thread(target=respond)
                worker.start()
                try:
                    if dma_complete:
                        self.assertEqual(capture.capture("test", threading.Event()).source, "LIVE")
                    else:
                        with self.assertRaisesRegex(ValueError, "DMA"):
                            capture.capture("test", threading.Event())
                finally:
                    worker.join()


if __name__ == "__main__":
    unittest.main()
