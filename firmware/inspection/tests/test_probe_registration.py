import tempfile
import unittest
from pathlib import Path

from utinspect.basic_vision import BasicVision, project_point, solve_homography, unproject_point
from test_basic_vision import CORNERS, NOW, frame_bytes


class ProbeRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "frame.ppm"
        self.now, self.sequence = NOW, 0
        self.vision = BasicVision(self.path, clock=lambda: self.now)
        self.publish()
        selection = self.vision.freeze()["selection"]
        self.vision.calibrate({"calibration_id": selection["calibration_id"], "corners": CORNERS, "fixed_confirmed": True})

    def publish(self, position=(40, 40), *, status=1, candidates=1, step_ms=180, delay_ms=0, stream_id=1_000_000_000):
        self.now += step_ms * 1_000_000
        self.sequence += 1
        pixel_x, pixel_y = 24 + position[0] * 1.65, 17 + position[1] * 1.1
        data = frame_bytes(magic="UTCAM2", sequence=self.sequence, captured=self.now - delay_ms * 1_000_000, stream_id=stream_id)
        fields = f" {status} {candidates} {round(pixel_x * 1000)} {round(pixel_y * 1000)} 64\n160 120\n".encode()
        self.path.write_bytes(data.replace(b"\n160 120\n", fields, 1))
        return self.vision.state()

    def stable(self, position):
        self.vision.marker_history.clear()
        for _ in range(3):
            state = self.publish(position)
        return state

    def capture(self, index):
        return self.vision.capture_probe_reference({"point_index": index, "contact_confirmed": True, "fixed_pose_confirmed": True})

    def register(self, count=5):
        for index, position in enumerate(((10, 10), (70, 10), (70, 70), (10, 70), (40, 40))):
            if index >= count:
                break
            self.stable(position)
            state = self.capture(index)
        return state

    def test_four_points_fit_but_only_independent_center_check_enables_contact_coordinates(self):
        state = self.register(4)
        self.assertFalse(state["probe_registration"]["complete"])
        self.assertIsNone(state["probe_registration"]["registration_id"])
        self.stable((40, 40))
        state = self.capture(4)
        self.assertTrue(state["probe_registration"]["complete"])
        self.assertEqual(state["probe_registration"]["center_error_mm"], 0)
        self.assertFalse(state["physical_accuracy_verified"])

    def test_fixed_height_parallax_and_offset_are_removed_in_synthetic_geometry(self):
        self.register()
        for contact in ((15, 25), (65, 30), (50, 60), (0, 0), (80, 80)):
            with self.subTest(contact=contact):
                state = self.publish(contact)
                self.assertEqual(state["position_mm"], list(contact))
                self.assertEqual(state["coordinate_basis"], "REGISTERED_PROBE_CONTACT_ESTIMATE")
                self.assertNotEqual(state["surface_projection_mm"], list(contact))
                expected = [20 + contact[0] * 1.5, 20 + contact[1]]
                for actual, target in zip(state["contact_pixel"], expected):
                    self.assertAlmostEqual(actual, target)

    def test_center_check_failure_does_not_complete_registration(self):
        self.register(4)
        self.stable((45, 40))
        with self.assertRaisesRegex(ValueError, "center check failed"):
            self.capture(4)
        self.assertEqual(len(self.vision.probe_samples), 4)
        self.assertIsNone(self.vision.registration_id)

    def test_single_frozen_or_duplicate_frame_is_not_stability_evidence(self):
        self.publish()
        for _ in range(5):
            self.vision.state()
        with self.assertRaisesRegex(ValueError, "hold one fresh"):
            self.capture(0)
        self.assertFalse(self.vision.state()["probe_registration"]["stable"])

    def test_moving_marker_is_not_captured(self):
        for position in ((10, 10), (15, 10), (20, 10)):
            self.publish(position)
        with self.assertRaises(ValueError):
            self.capture(0)

    def test_stale_ambiguous_and_missing_markers_clear_stability(self):
        for changes in ({"delay_ms": 301}, {"status": 2, "candidates": 0}, {"status": 3, "candidates": 2}):
            with self.subTest(changes=changes):
                self.stable((10, 10))
                state = self.publish((10, 10), step_ms=500, **changes)
                self.assertFalse(state["probe_registration"]["stable"])
                self.assertIsNone(state["position_mm"])
                with self.assertRaises(ValueError):
                    self.capture(0)
        self.assertEqual(self.vision.probe_samples, [])

    def test_contact_confirmation_index_and_pose_are_required(self):
        self.stable((10, 10))
        good = {"point_index": 0, "contact_confirmed": True, "fixed_pose_confirmed": True}
        for changes in ({"point_index": True}, {"point_index": -1}, {"point_index": 1},
                        {"contact_confirmed": False}, {"fixed_pose_confirmed": 1}, {"pixel": [1, 2]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.vision.capture_probe_reference({**good, **changes})
        self.assertEqual(self.vision.probe_samples, [])

    def test_repeated_points_cannot_define_a_probe_plane(self):
        for index in range(3):
            self.stable((10, 10))
            self.capture(index)
        self.stable((10, 10))
        with self.assertRaises(ValueError):
            self.capture(3)
        self.assertFalse(self.vision.state()["probe_registration"]["complete"])

    def test_freezing_disables_contact_capture_until_live_preview_is_resumed(self):
        self.stable((10, 10))
        self.vision.freeze()
        with self.assertRaises(ValueError):
            self.capture(0)
        self.vision.resume()
        self.stable((10, 10))
        self.assertEqual(self.capture(0)["probe_registration"]["point_count"], 1)

    def test_stream_change_clear_and_recalibration_invalidate_probe_registration(self):
        for action in ("stream", "clear", "surface"):
            with self.subTest(action=action):
                if self.vision.transform is None:
                    selection = self.vision.freeze()["selection"]
                    self.vision.calibrate({"calibration_id": selection["calibration_id"], "corners": CORNERS, "fixed_confirmed": True})
                self.register()
                if action == "stream":
                    state = self.publish(stream_id=self.now - 1)
                elif action == "clear":
                    state = self.vision.clear()
                else:
                    selection = self.vision.freeze()["selection"]
                    state = self.vision.calibrate({"calibration_id": selection["calibration_id"], "corners": CORNERS, "fixed_confirmed": True})
                self.assertFalse(state["probe_registration"]["complete"])
                self.assertIsNone(state["probe_registration"]["registration_id"])
                self.publish()

    def test_explicit_reset_retains_surface_but_not_contact_registration(self):
        self.register()
        surface_id = self.vision.surface_calibration_id
        state = self.vision.reset_probe_reference()
        self.assertTrue(state["calibrated"])
        self.assertEqual(state["surface_calibration_id"], surface_id)
        self.assertEqual(state["probe_registration"]["point_count"], 0)

    def test_inverse_projection_round_trip_in_perspective(self):
        transform = solve_homography([[25, 24], [133, 10], [151, 104], [8, 99]], 160, 120)
        for point in ((0, 0), (80, 0), (80, 80), (0, 80), (40, 40), (15, 60)):
            pixel = unproject_point(transform, point, 160, 120, 80, 80)
            actual = project_point(transform, pixel, 160, 120, 80, 80)
            for result, target in zip(actual, point):
                self.assertAlmostEqual(result, target)
