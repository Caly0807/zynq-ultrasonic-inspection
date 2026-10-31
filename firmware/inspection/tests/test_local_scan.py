import json
import struct
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from utinspect.basic_vision import BasicVision
from utinspect.board_health import local_scan_diagnostics
from utinspect.echo import EchoConfig, Waveform
from utinspect.local_scan import LocalScan, PREVIEW, STATUS, TIMING_BASIS
from utinspect.mapping import MapSession
from test_basic_vision import CORNERS, frame_bytes


class LocalScanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = time.monotonic_ns()
        self.patch = mock.patch("utinspect.mapping.time.monotonic_ns", side_effect=lambda: self.now)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.path = self.root / "camera.ppm"
        self.sequence = 0
        self.session = MapSession(self.root / "session", mode="LIVE")
        self.session.bind_local_display(self.root / "lcd")
        self.vision = BasicVision(self.path, clock=lambda: self.now)
        self.owner = "lcd-" + "1" * 32
        self.active = True
        self.stops = 0
        self.waveform = None
        self.live = mock.Mock()
        self.live.state.side_effect = lambda: {"active": self.active}
        self.live.mapping_frame.side_effect = lambda: (self.owner, self.waveform) if self.active else (None, None)
        self.live.stop.side_effect = self.stop_live
        self.scan = LocalScan(self.root / "lcd", self.session, self.vision, self.live, clock=lambda: self.now)
        self.camera()

    def stop_live(self):
        self.active = False
        self.stops += 1

    def camera(self, *, step_ms=0, marker=True, center=80, camera_delay_ms=0, adc_delay_ms=0, stream_id=1_000_000_000, flaw=False):
        self.now += step_ms * 1_000_000
        self.sequence += 1
        boxes = [(center - 8, 52, center + 8, 68)] if marker else []
        self.path.write_bytes(frame_bytes(magic="UTCAM1", sequence=self.sequence,
                                         captured=self.now - camera_delay_ms * 1_000_000, stream_id=stream_id, boxes=boxes))
        samples = [128] * 4096
        if flaw:
            samples[110], samples[254] = 210, 190
        self.waveform = Waveform(tuple(samples), 0, .04, "LIVE", frame_id=f"ADC_TEST_{self.sequence}",
                                 acquired_monotonic_ns=self.now - adc_delay_ms * 1_000_000)

    def calibrate(self):
        self.scan.command("freeze", {})
        for position_x, position_y in CORNERS:
            self.scan.command("corner", {"x": position_x / 160, "y": position_y / 120})
        self.scan.command("calibrate", {"fixed_confirmed": True})

    def start(self):
        self.calibrate()
        self.scan.command("scan-start", {"tracking_confirmed": True, "normal_incidence_confirmed": True})

    def stable_sample(self, **kwargs):
        for index in range(5):
            self.camera(step_ms=120, **kwargs)
            state = self.scan.tick()
        return state

    def test_boot_publishes_preview_without_arming_transmission_or_recording(self):
        self.active = False
        state = self.scan.tick()
        self.assertEqual(state["phase"], "IDLE")
        self.assertEqual(state["sampled_cells"], 0)
        self.live.start.assert_not_called()
        data = (self.root / "lcd/scan-preview.bin").read_bytes()
        magic, length, timestamp = PREVIEW.unpack(data[:PREVIEW.size])
        self.assertEqual((magic, length, timestamp), (b"UTP1", 320 * 240 * 4, self.now))
        self.assertEqual(len(data), PREVIEW.size + length)
        self.assertEqual(data[PREVIEW.size:PREVIEW.size + 4], b"\x50\x45\x40\x00")

    def test_local_corners_and_fixed_confirmation_need_no_http_or_computer(self):
        self.calibrate()
        self.assertTrue(self.vision.state()["calibrated"])
        self.assertEqual(self.vision.state()["position_mm"], [39.667, 39.5])
        self.assertFalse(self.live.start.called)

    def test_four_corners_cannot_be_implicitly_confirmed(self):
        self.scan.command("freeze", {})
        with self.assertRaises(ValueError):
            self.scan.command("calibrate", {"fixed_confirmed": True})
        for position_x, position_y in CORNERS:
            self.scan.command("corner", {"x": position_x / 160, "y": position_y / 120})
        with self.assertRaises(ValueError):
            self.scan.command("calibrate", {"fixed_confirmed": False})
        self.assertFalse(self.vision.state()["calibrated"])

    def test_real_acquisition_and_calibrated_camera_are_both_required(self):
        payload = {"tracking_confirmed": True, "normal_incidence_confirmed": True}
        with self.assertRaises(ValueError):
            self.scan.command("scan-start", payload)
        self.calibrate()
        self.active = False
        with self.assertRaises(ValueError):
            self.scan.command("scan-start", payload)
        self.assertEqual(self.session.snapshot()["point_count"], 0)

    def test_pc_owned_capture_cannot_become_local_continuous_scan(self):
        self.calibrate()
        self.owner = "pc-" + "2" * 32
        with self.assertRaises(ValueError):
            self.scan.command("scan-start", {"tracking_confirmed": True, "normal_incidence_confirmed": True})

    def test_stable_real_pair_records_position_evidence_not_manual_accuracy(self):
        self.start()
        state = self.stable_sample()
        self.assertEqual(state["sampled_cells"], 1)
        self.assertEqual(state["valid_cells"], 0)
        point = self.session.snapshot()["points"][0]
        self.assertEqual(point["source"], "LIVE")
        self.assertEqual(point["position_basis"], "CAMERA_MARKER_ESTIMATE_UNVERIFIED")
        self.assertFalse(point["position_confirmed"])
        self.assertIsNone(point["position_accuracy_mm"])
        self.assertIsNone(point["reflector_depth_mm"])
        self.assertEqual(point["visual_evidence"]["timing_basis"], TIMING_BASIS)
        self.assertTrue((self.root / "lcd/map.csv").exists())

    def test_same_cell_does_not_accumulate_duplicate_waveforms(self):
        self.start()
        self.stable_sample()
        self.stable_sample()
        self.assertEqual(self.session.snapshot()["point_count"], 1)
        self.assertLessEqual(len(self.scan.stable_frames), 3)

    def test_calibrated_fixture_publishes_2d_cell_and_reflector_depth_for_3d(self):
        self.session.echo = EchoConfig(calibrated=True, calibration_id="UNIT_FIXTURE_NOT_BOARD_VALIDATION",
                                       baseline_gate_us=(20, 22))
        self.start()
        self.stable_sample(flaw=True)
        snapshot = self.session.snapshot()
        self.assertEqual(snapshot["suspect_cells"], 1)
        self.assertEqual(snapshot["valid_cells"], 1)
        self.assertEqual(snapshot["depth_point_count"], 1)
        self.assertAlmostEqual(snapshot["points"][0]["reflector_depth_mm"], 12.98, places=2)
        self.assertEqual(snapshot["reconstruction"], "INDICATION_POINTS_ONLY_NOT_HOLE_SHAPE_OR_CT")

    def test_moving_probe_is_not_recorded_until_it_dwells(self):
        self.start()
        for center in (45, 55, 65, 75, 85):
            self.camera(step_ms=120, center=center)
            self.scan.tick()
        self.assertEqual(self.session.snapshot()["point_count"], 0)

    def test_lost_marker_does_not_paint_an_unknown_region(self):
        self.start()
        state = self.stable_sample(marker=False)
        self.assertEqual(state["reason"], "MARKER_LOST")
        self.assertEqual(state["sampled_cells"], 0)

    def test_mismatched_camera_and_adc_time_does_not_record(self):
        self.start()
        state = self.stable_sample(adc_delay_ms=250)
        self.assertEqual(state["reason"], "TIMING_MISMATCH")
        self.assertEqual(state["sampled_cells"], 0)

    def test_camera_stream_change_requires_recalibration_and_rearming(self):
        self.start()
        self.camera(step_ms=120, stream_id=2_000_000_000)
        state = self.scan.tick()
        self.assertEqual((state["phase"], state["reason"]), ("IDLE", "CALIBRATION_CHANGED"))
        self.assertEqual(state["sampled_cells"], 0)

    def test_finish_keeps_map_and_stops_real_acquisition(self):
        self.start()
        self.stable_sample()
        self.scan.command("scan-finish", {})
        self.assertFalse(self.active)
        self.assertEqual(self.stops, 1)
        self.assertEqual(self.scan.tick()["phase"], "FINISHED")
        self.assertEqual(self.session.snapshot()["point_count"], 1)

    def test_lease_loss_requires_explicit_new_scan_start(self):
        self.start()
        self.active = False
        self.assertEqual(self.scan.tick()["phase"], "IDLE")
        self.active = True
        self.stable_sample()
        self.assertEqual(self.session.snapshot()["point_count"], 0)

    def test_stale_local_command_never_starts_scanning(self):
        self.calibrate()
        request = {"action": "scan-start", "payload": {"tracking_confirmed": True, "normal_incidence_confirmed": True},
                   "monotonic_ns": self.now - 500_000_001}
        (self.root / "lcd/scan-command.json").write_text(json.dumps(request))
        self.assertEqual(self.scan.tick()["reason"], "ERROR")
        self.assertEqual(self.scan.phase, "IDLE")
        self.assertFalse(self.live.start.called)

    def test_status_binary_layout_matches_native_port(self):
        self.calibrate()
        self.scan.tick()
        data = (self.root / "lcd/scan-status.bin").read_bytes()
        self.assertEqual(len(data), 112)
        fields = STATUS.unpack(data)
        self.assertEqual(fields[0], b"UTS1")
        self.assertEqual(fields[2], self.now)
        self.assertEqual(struct.unpack_from("<I", data, 40)[0], 256)
        self.assertAlmostEqual(struct.unpack_from("<f", data, 64)[0], 39.667, places=4)

    def test_local_status_is_visible_to_authenticated_device_health(self):
        self.scan.tick()
        status = local_scan_diagnostics(self.root / "lcd/scan-status.json")
        self.assertTrue(status["available"])
        self.assertEqual(status["phase"], "IDLE")
        self.assertFalse(status["position_accuracy_verified"])
        self.now += 3_000_000_001
        self.assertFalse(local_scan_diagnostics(self.root / "lcd/scan-status.json")["available"])

    def test_new_workpiece_keeps_old_data_and_requires_explicit_restart(self):
        self.start()
        self.stable_sample()
        previous = self.session.root
        previous_session = self.session.session_id
        self.scan.command("scan-finish", {})
        self.scan.command("new-scan", {"archive_confirmed": True})
        self.assertTrue((previous / "session.json").exists())
        self.assertEqual(len(json.loads((previous / "session.json").read_text())["points"]), 1)
        self.assertNotEqual(self.session.session_id, previous_session)
        self.assertEqual(self.session.snapshot()["point_count"], 0)
        self.assertFalse(self.active)
        self.assertEqual(self.scan.phase, "IDLE")
        self.assertTrue(self.vision.state()["calibrated"])

    def test_new_workpiece_needs_explicit_archive_confirmation(self):
        with self.assertRaises(ValueError):
            self.scan.command("new-scan", {"archive_confirmed": False})

    def test_truthy_values_do_not_substitute_for_explicit_confirmation(self):
        for value in (1, "true", [True]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.scan.command("new-scan", {"archive_confirmed": value})

    def test_corrupt_visual_timing_cannot_enter_map_session(self):
        self.start()
        evidence = {"camera_frame_id": self.vision.state()["frame"]["frame_id"],
                    "camera_monotonic_ns": self.now - 300_000_000, "adc_monotonic_ns": self.now,
                    "skew_ms": 300.0, "stable_ms": 500.0, "timing_basis": TIMING_BASIS}
        with self.assertRaisesRegex(ValueError, "time-associated"):
            self.session.add(self.waveform, 40, 40, True, visual_evidence=evidence)
        self.assertEqual(self.session.snapshot()["point_count"], 0)


if __name__ == "__main__":
    unittest.main()
