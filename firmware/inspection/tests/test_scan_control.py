import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from utinspect.echo import EchoConfig, synthetic_waveform
from utinspect.scan import ReplayCapture, ScanPlan, Scanner, SimulatedStage, StopRequested, request_control, scan_status
from utinspect.vision import Observation


def pose(position_mm, settled_ns, stop_event):
    return Observation(True, "test_only", (70. + position_mm, 0., 0.), time.monotonic_ns(), "DEMO", (0, 1))


class PlanPreviewTests(unittest.TestCase):
    def test_preview_includes_endpoint_and_lower_bound_without_hardware(self):
        preview = ScanPlan(end_mm=1, step_mm=0.3, settle_s=0.2).preview()
        self.assertEqual(preview["positions_mm"], [0., 0.3, 0.6, 0.9, 1.])
        self.assertEqual(preview["point_count"], 5)
        self.assertAlmostEqual(preview["last_step_mm"], 0.1)
        self.assertEqual(preview["settling_lower_bound_s"], 1.)
        self.assertFalse(preview["hardware_motion_driver"])
        self.assertFalse(preview["transmit_enabled_by_this_software"])

    def test_bad_json_numeric_types_rejected(self):
        for value in (True, "5", None, float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ScanPlan(step_mm=value)

    def test_point_count_boundary_and_lvgl_capacity_are_distinct(self):
        plan = ScanPlan(end_mm=99.99, step_mm=0.01)
        self.assertEqual(plan.point_count, 10000)
        self.assertFalse(plan.preview()["lvgl_review_compatible"])
        with self.assertRaises(ValueError):
            ScanPlan(end_mm=100, step_mm=0.01)


class TaskControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "scan"
        self.plan = ScanPlan(end_mm=10, settle_s=0)
        self.scanner = Scanner(self.plan, EchoConfig(), SimulatedStage(), pose,
                               ReplayCapture(synthetic_waveform(position) for position in self.plan.positions()), self.output)
        self.results, self.errors = [], []

    def launch(self):
        def run():
            try:
                self.results.append(self.scanner.run())
            except Exception as error:
                self.errors.append(error)
        self.worker = threading.Thread(target=run)
        self.worker.start()
        self.addCleanup(self.finish)

    def finish(self):
        self.scanner.stop()
        self.worker.join(3)
        self.assertFalse(self.worker.is_alive(), "test worker did not stop")

    def wait_status(self, expected):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                manifest = scan_status(self.output)
                if manifest["status"] == expected:
                    return manifest
            except FileNotFoundError:
                pass
            time.sleep(0.005)
        self.fail(f"did not reach {expected}; errors={self.errors}")

    def test_pause_before_first_point_and_file_resume(self):
        self.scanner.pause()
        self.launch()
        paused = self.wait_status("PAUSED")
        self.assertEqual(paused["point_count"], 0)
        self.assertFalse(paused["activity_confirmed"])
        requested = request_control(self.output, "resume")
        self.assertEqual(requested["state"], "REQUESTED_NOT_CONFIRMED")
        self.worker.join(3)
        self.assertEqual(self.errors, [])
        manifest = scan_status(self.output)
        self.assertEqual(manifest["status"], "COMPLETE")
        self.assertEqual(manifest["point_count"], 3)
        self.assertEqual(manifest["last_control"]["action"], "resume")
        self.assertTrue(manifest["cleanup_complete"])

    def test_pause_during_capture_finishes_point_before_pausing(self):
        original = self.scanner.capture
        scanner = self.scanner
        class PauseCapture:
            def capture(self, request_id, stop_event):
                scanner.pause()
                return original.capture(request_id, stop_event)
        self.scanner.capture = PauseCapture()
        self.launch()
        paused = self.wait_status("PAUSED")
        self.assertEqual(paused["point_count"], 1)
        records = (self.output / "points.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(records), 1)
        request_control(self.output, "stop")
        self.worker.join(3)
        self.assertEqual(self.results[0]["status"], "STOPPED")
        self.assertEqual(self.results[0]["point_count"], 1)

    def test_stop_file_is_seen_while_capture_waits(self):
        acquiring = threading.Event()
        class WaitingCapture:
            def capture(self, request_id, stop_event):
                acquiring.set()
                if stop_event.wait(2):
                    raise StopRequested()
                raise TimeoutError("software test wait expired")
        self.scanner.capture = WaitingCapture()
        self.launch()
        self.assertTrue(acquiring.wait(1))
        request_control(self.output, "stop")
        self.worker.join(3)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.results[0]["status"], "STOPPED")
        self.assertEqual(self.results[0]["point_count"], 0)

    def test_stop_failure_is_fault_not_success(self):
        class BadStop(SimulatedStage):
            def stop(self):
                raise OSError("test_stop_failed")
        self.scanner.stage = BadStop()
        with self.assertRaisesRegex(OSError, "test_stop_failed"):
            self.scanner.run()
        manifest = scan_status(self.output)
        self.assertEqual(manifest["status"], "FAULT")
        self.assertFalse(manifest["cleanup_complete"])
        self.assertEqual(manifest["stop_error"], "test_stop_failed")

    def test_instance_cannot_run_twice_and_terminal_control_is_rejected(self):
        self.scanner.run()
        with self.assertRaisesRegex(ValueError, "once"):
            self.scanner.run()
        with self.assertRaisesRegex(ValueError, "finished"):
            request_control(self.output, "resume")

    def test_wrong_run_control_fails_closed(self):
        self.scanner.pause()
        self.launch()
        self.wait_status("PAUSED")
        control = self.output / "control"
        control.mkdir()
        (control / "invalid.json").write_text(json.dumps({"schema": 1, "run_id": "wrong", "action": "resume"}))
        self.worker.join(3)
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(scan_status(self.output)["status"], "FAULT")


if __name__ == "__main__":
    unittest.main()
