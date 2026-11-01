import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from test_basic_vision import CORNERS, frame_bytes
from utinspect.basic_vision import BasicVision
from utinspect.echo import Waveform
from utinspect.local_scan import LocalScan
from utinspect.mapping import MapSession


class LocalInspectionWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = time.monotonic_ns()
        timer = mock.patch("utinspect.mapping.time.monotonic_ns", side_effect=lambda: self.now)
        timer.start()
        self.addCleanup(timer.stop)
        self.active = False
        self.sequence = 0
        self.owner = "lcd-" + "3" * 32
        self.frame = None
        self.path = self.root / "camera.ppm"
        self.session = MapSession(self.root / "session", mode="LIVE")
        self.session.bind_local_display(self.root / "lcd")
        self.live = mock.Mock()
        self.live.state.side_effect = lambda: {"active": self.active, "capture_in_flight": False}
        self.live.mapping_frame.side_effect = lambda: (self.owner, self.frame) if self.active else (None, None)
        self.live.stop.side_effect = self.stop
        vision = BasicVision(self.path, clock=lambda: self.now)
        self.scan = LocalScan(self.root / "lcd", self.session, vision, self.live, clock=lambda: self.now)
        self.next_frame()

    def stop(self):
        self.active = False

    def next_frame(self, *, flaw=False, marker=True, center=80, gain=128):
        self.now += 120_000_000
        self.sequence += 1
        samples = [128] * 4096
        samples[275] = 195
        if flaw:
            samples[148] = 205
        self.frame = Waveform(tuple(samples), 0, .04, "LIVE", gain,
                              frame_id=f"INTEGRATION_FIXTURE_NOT_HARDWARE-{self.sequence}",
                              acquired_monotonic_ns=self.now)
        boxes = [(center - 8, 52, center + 8, 68)] if marker else []
        self.path.write_bytes(frame_bytes(magic="UTCAM1", sequence=self.sequence, captured=self.now, boxes=boxes))

    def collect_reference(self, **changes):
        self.active = True
        self.scan.command("echo-reference", {"reference_confirmed": True, "normal_incidence_confirmed": True})
        for _ in range(8):
            self.next_frame(**changes)
            self.scan.tick()

    def approve_reference(self):
        self.collect_reference()
        self.assertEqual(self.scan.echo_setup.phase, "READY")
        self.assertFalse(self.session.echo.calibrated)
        self.scan.command("echo-approve", {"review_confirmed": True})
        self.scan.tick()
        self.assertFalse(self.active)
        self.assertTrue(self.session.echo.calibrated)
        self.live.configure_echo.assert_called_once_with(self.session.echo)
        self.live.start.assert_not_called()

    def start_scan(self):
        self.next_frame()
        self.scan.command("freeze", {})
        for position_x, position_y in CORNERS:
            self.scan.command("corner", {"x": position_x / 160, "y": position_y / 120})
        self.scan.command("calibrate", {"fixed_confirmed": True})
        self.scan.command("scan-start", {"tracking_confirmed": True, "normal_incidence_confirmed": True})

    def dwell(self, **changes):
        for _ in range(5):
            self.next_frame(**changes)
            self.scan.tick()

    def test_reference_camera_coverage_depth_and_finish_share_the_same_configuration(self):
        self.approve_reference()
        self.assertAlmostEqual(self.session.echo.time_zero_us, 11 - 60000 / 5900)
        self.assertEqual(self.session.echo.reference_gain_code, 128)
        self.active = True
        self.start_scan()
        self.dwell(flaw=True)
        snapshot = self.session.snapshot()
        self.assertEqual((snapshot["sampled_cells"], snapshot["suspect_cells"], snapshot["depth_point_count"]), (1, 1, 1))
        expected_depth = (148 * .04 - self.session.echo.time_zero_us) * 5900 / 2000
        self.assertAlmostEqual(snapshot["points"][0]["reflector_depth_mm"], expected_depth, places=3)
        self.assertEqual(snapshot["points"][0]["diagnosis"]["calibration_id"], self.session.echo.calibration_id)
        self.assertEqual(sum(cell["status"] == "UNSCANNED" for cell in snapshot["cells"]), snapshot["total_cells"] - 1)
        self.dwell(marker=False, center=110)
        self.assertEqual(self.session.snapshot()["sampled_cells"], 1)
        self.dwell(center=110)
        snapshot = self.session.snapshot()
        self.assertEqual((snapshot["sampled_cells"], snapshot["valid_cells"], snapshot["suspect_cells"]), (2, 2, 1))
        self.scan.command("scan-finish", {})
        self.assertFalse(self.active)
        self.next_frame(flaw=True, center=45)
        self.scan.tick()
        self.assertEqual(self.session.snapshot()["point_count"], 2)
        self.assertTrue((self.root / "lcd/map.csv").is_file())
        self.assertTrue((self.root / "lcd/scan-status.bin").is_file())
        self.live.start.assert_not_called()

    def test_reference_flaw_is_rejected_before_an_explicit_clean_reference_can_be_approved(self):
        self.collect_reference(flaw=True)
        self.assertEqual(self.scan.echo_setup.reason, "REFERENCE_FLAW_GATE_CROSSED")
        with self.assertRaises(ValueError):
            self.scan.command("echo-approve", {"review_confirmed": True})
        self.assertFalse(self.session.echo.calibrated)
        self.assertEqual(self.session.echo.flaw_threshold_codes, 35)
        self.approve_reference()

    def test_gain_change_after_calibration_produces_no_depth_point(self):
        self.approve_reference()
        self.active = True
        self.start_scan()
        self.dwell(flaw=True, gain=192)
        snapshot = self.session.snapshot()
        self.assertEqual((snapshot["sampled_cells"], snapshot["valid_cells"], snapshot["depth_point_count"]), (1, 0, 0))
        self.assertIsNone(snapshot["points"][0]["reflector_depth_mm"])
        self.assertEqual(snapshot["points"][0]["status"], "INVALID")
        self.live.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
