import base64
import http.client
import json
import os
import struct
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest import mock

from utinspect.basic_vision import BasicVision, MAX_AGE_NS, SELECTION_AGE_NS, parse_frame, png_bytes, project_point, red_marker, solve_homography
from utinspect.device_app import create_server
from utinspect.mapping import MapSession


NOW = 4_000_000_000
CORNERS = [[20, 20], [140, 20], [140, 100], [20, 100]]


def frame_bytes(*, sequence=1, captured=NOW, stream_id=1_000_000_000, magic="UTHOST1", boxes=(), width=160, height=120):
    pixels = bytearray(b"\x40\x45\x50" * (width * height))
    for left, top, right, bottom in boxes:
        for row in range(top, bottom):
            for column in range(left, right):
                start = (row * width + column) * 3
                pixels[start:start + 3] = b"\xf0\x20\x10"
    return f"P6\n# {magic} {sequence} {captured} {stream_id}\n{width} {height}\n255\n".encode("ascii") + pixels


class BasicFrameTests(unittest.TestCase):
    def test_fixture_requires_explicit_host_permission_and_never_becomes_camera(self):
        data = frame_bytes()
        with self.assertRaisesRegex(ValueError, "fixtures are disabled"):
            parse_frame(data, NOW)
        frame = parse_frame(data, NOW, True)
        self.assertEqual(frame.source, "HOST_FIXTURE")
        self.assertEqual((frame.width, frame.height, frame.sequence), (160, 120, 1))

    def test_native_protocol_is_distinct_from_fixture_protocol(self):
        frame = parse_frame(frame_bytes(magic="UTCAM1"), NOW)
        self.assertEqual(frame.source, "USB_CAMERA")
        self.assertIn(str(NOW), frame.frame_id)

    def test_stale_and_future_camera_timestamps_are_rejected(self):
        for captured in (NOW - MAX_AGE_NS - 1, NOW + 1):
            with self.subTest(captured=captured), self.assertRaisesRegex(ValueError, "stale|clock"):
                parse_frame(frame_bytes(captured=captured), NOW, True)

    def test_malformed_geometry_metadata_and_trailing_data_are_rejected(self):
        good = frame_bytes()
        for bad in (good[:-1], good + b"x", good.replace(b"160 120", b"161 120", 1),
                    good.replace(b"# UTHOST1", b"# UNKNOWN", 1), good.replace(b"255\n", b"256\n", 1),
                    good.replace(b"1000000000", b"9999999999", 1), good.replace(b"# UTHOST1 1 ", b"# UTHOST1 -1 ", 1)):
            with self.subTest(header=bad[:85]), self.assertRaises(ValueError):
                parse_frame(bad, NOW, True)

    def test_png_contains_original_rgb_and_valid_checksums(self):
        frame = parse_frame(frame_bytes(boxes=[(70, 50, 90, 70)]), NOW, True)
        data = png_bytes(frame)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        offset, compressed = 8, bytearray()
        while offset < len(data):
            length = struct.unpack_from(">I", data, offset)[0]
            kind, payload = data[offset + 4:offset + 8], data[offset + 8:offset + 8 + length]
            crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
            self.assertEqual(crc, zlib.crc32(kind + payload) & 0xffffffff)
            if kind == b"IDAT":
                compressed.extend(payload)
            offset += 12 + length
        rows = zlib.decompress(compressed)
        stride = frame.width * 3
        self.assertEqual(rows, b"".join(b"\0" + frame.pixels[start:start + stride] for start in range(0, len(frame.pixels), stride)))


class CalibrationMathTests(unittest.TestCase):
    def test_rectangle_maps_corners_and_center_to_millimeters(self):
        transform = solve_homography(CORNERS, 160, 120)
        for pixel, expected in zip(CORNERS + [[80, 60]], ([0, 0], [80, 0], [80, 80], [0, 80], [40, 40])):
            actual = project_point(transform, pixel, 160, 120, 80, 80)
            for result, target in zip(actual, expected):
                self.assertAlmostEqual(result, target, places=7)

    def test_perspective_quadrilateral_maps_all_four_corners(self):
        corners = [[25, 24], [133, 10], [151, 104], [8, 99]]
        transform = solve_homography(corners, 160, 120)
        for pixel, expected in zip(corners, ([0, 0], [80, 0], [80, 80], [0, 80])):
            for result, target in zip(project_point(transform, pixel, 160, 120, 80, 80), expected):
                self.assertAlmostEqual(result, target, places=6)

    def test_reversed_crossed_collinear_small_and_invalid_corners_are_rejected(self):
        for corners in (CORNERS[::-1], [CORNERS[index] for index in (0, 2, 1, 3)],
                        [[20, 20], [40, 20], [60, 20], [80, 20]], [[1, 1], [5, 1], [5, 5], [1, 5]],
                        [[-1, 20], *CORNERS[1:]], [[True, 20], *CORNERS[1:]],
                        [[float("nan"), 20], *CORNERS[1:]], CORNERS[:3], "corners"):
            with self.subTest(corners=corners), self.assertRaises(ValueError):
                solve_homography(corners, 160, 120)

    def test_one_red_component_is_detected_without_opencv(self):
        frame = parse_frame(frame_bytes(boxes=[(70, 50, 90, 70)]), NOW, True)
        marker = red_marker(frame)
        self.assertEqual(marker["status"], "FOUND")
        self.assertEqual(marker["pixel"], [79.5, 59.5])

    def test_multiple_or_oversized_red_components_do_not_report_valid_marker(self):
        for boxes, expected in (([], "NOT_FOUND"), ([(30, 30, 40, 40), (90, 70, 100, 80)], "AMBIGUOUS"),
                                ([(5, 5, 155, 115)], "TOO_LARGE")):
            with self.subTest(expected=expected):
                frame = parse_frame(frame_bytes(boxes=boxes), NOW, True)
                self.assertEqual(red_marker(frame)["status"], expected)


class BasicVisionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "frame.ppm"
        self.now = NOW
        self.vision = BasicVision(self.path, allow_host_fixture=True, clock=lambda: self.now)
        self.publish()

    def publish(self, **changes):
        self.path.with_suffix(".tmp").write_bytes(frame_bytes(**{"boxes": [(70, 50, 90, 70)], **changes}))
        self.path.with_suffix(".tmp").replace(self.path)

    def calibrate(self):
        frozen = self.vision.freeze()
        return self.vision.calibrate({"calibration_id": frozen["selection"]["calibration_id"],
                                      "corners": CORNERS, "fixed_confirmed": True})

    def test_new_service_never_implicitly_trusts_old_calibration(self):
        self.assertFalse(self.vision.state()["calibrated"])
        self.assertIsNone(self.vision.state()["position_mm"])
        self.assertFalse(self.vision.state()["automatic_workpiece_detection"])
        self.calibrate()
        other = BasicVision(self.path, allow_host_fixture=True, clock=lambda: self.now)
        self.assertFalse(other.state()["calibrated"])

    def test_freeze_calibrate_and_clear_keep_fixture_source_and_manual_boundaries(self):
        state = self.calibrate()
        self.assertTrue(state["preview_available"])
        self.assertEqual(state["source"], "HOST_FIXTURE")
        self.assertEqual(state["position_mm"], [39.667, 39.5])
        self.assertFalse(state["internal_damage_detection"])
        self.assertFalse(state["physical_accuracy_verified"])
        self.assertFalse(self.vision.clear()["calibrated"])

    def test_fixed_confirmation_and_matching_freeze_identity_are_required(self):
        frozen = self.vision.freeze()["selection"]
        for payload in ({"calibration_id": frozen["calibration_id"], "corners": CORNERS},
                        {"calibration_id": "wrong", "corners": CORNERS, "fixed_confirmed": True}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.vision.calibrate(payload)
        self.assertFalse(self.vision.state()["calibrated"])

    def test_stale_and_missing_frames_never_reuse_previous_position(self):
        self.calibrate()
        self.now += MAX_AGE_NS + 1
        state = self.vision.state()
        self.assertFalse(state["preview_available"])
        self.assertIsNone(state["position_mm"])
        with self.assertRaises(ValueError):
            self.vision.frame_png()
        self.path.unlink()
        self.assertFalse(self.vision.state()["preview_available"])

    def test_new_camera_stream_invalidates_previous_calibration_and_frozen_image(self):
        self.calibrate()
        self.vision.freeze()
        self.publish(sequence=0, stream_id=2_000_000_000)
        state = self.vision.state()
        self.assertFalse(state["calibrated"])
        self.assertNotIn("selection", state)
        self.assertIsNone(state["position_mm"])

    def test_selection_expires_even_if_current_stream_is_live(self):
        identity = self.vision.freeze()["selection"]["calibration_id"]
        self.now += SELECTION_AGE_NS + 1
        self.publish(captured=self.now, sequence=30)
        with self.assertRaisesRegex(ValueError, "Freeze"):
            self.vision.calibrate({"calibration_id": identity, "corners": CORNERS, "fixed_confirmed": True})

    def test_frame_download_corresponds_to_requested_frozen_image(self):
        frozen = self.vision.freeze()["selection"]
        original = self.vision.frame_png(frozen["frame_id"])
        self.now += 1_000_000
        self.publish(sequence=2, captured=self.now, boxes=[])
        self.assertNotEqual(self.vision.frame_png(), original)
        self.assertEqual(self.vision.frame_png(frozen["frame_id"]), original)
        with self.assertRaises(ValueError):
            self.vision.frame_png("nonexistent-frame")

    def test_marker_outside_workpiece_or_multiple_markers_clear_coordinates(self):
        self.calibrate()
        for index, (boxes, expected) in enumerate((( [(1, 30, 11, 40)], "OUTSIDE_WORKPIECE"),
                                                 ([(30, 30, 40, 40), (90, 70, 100, 80)], "AMBIGUOUS"))):
            with self.subTest(expected=expected):
                self.now += 1_000_000
                self.publish(sequence=3 + index, captured=self.now, boxes=boxes)
                state = self.vision.state()
                self.assertIsNone(state["position_mm"])
                self.assertEqual(state["marker"]["status"], expected)

    def test_regressed_or_reused_timestamp_is_rejected(self):
        self.calibrate()
        for captured in (NOW, NOW - 1):
            with self.subTest(captured=captured):
                self.publish(sequence=2, captured=captured, boxes=[])
                state = self.vision.state()
                self.assertFalse(state["preview_available"])
                self.assertIsNone(state["position_mm"])

    def test_atomic_publish_between_request_and_open_is_not_misclassified_as_future(self):
        self.vision.state()
        original_open = os.open
        def open_after_publish(*arguments, **keywords):
            self.now += 1_000_000
            self.publish(sequence=2, captured=self.now)
            return original_open(*arguments, **keywords)
        with mock.patch("utinspect.basic_vision.os.open", side_effect=open_after_publish):
            state = self.vision.state()
        self.assertTrue(state["preview_available"])
        self.assertEqual(state["frame"]["sequence"], 2)

    def test_reused_inode_mtime_and_size_cannot_hide_changed_frame_content(self):
        self.vision.state()
        same_metadata = self.path.stat()
        self.publish(sequence=2, captured=NOW - 1, boxes=[])
        with mock.patch("utinspect.basic_vision.os.fstat", return_value=same_metadata):
            state = self.vision.state()
        self.assertFalse(state["preview_available"])
        self.assertIsNone(state["position_mm"])

    @unittest.skipUnless(os.name == "posix", "POSIX symlink and FIFO checks")
    def test_symlink_and_fifo_inputs_are_rejected_without_blocking(self):
        self.path.unlink()
        target = self.root / "other.ppm"
        target.write_bytes(frame_bytes())
        self.path.symlink_to(target)
        self.assertFalse(self.vision.state()["preview_available"])
        self.path.unlink()
        os.mkfifo(self.path)
        self.assertFalse(self.vision.state()["preview_available"])


class BasicVisionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "frame.ppm"
        self.path.write_bytes(frame_bytes())
        self.token = "FixtureOnly_" + "8" * 40
        self.vision = BasicVision(self.path, allow_host_fixture=True, clock=lambda: NOW)
        self.session = MapSession(self.root / "session", mode="DEMO")
        self.server = create_server("127.0.0.1", 0, "127.0.0.1", self.token, self.session, vision=self.vision)
        self.worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02})
        self.worker.start()
        self.addCleanup(self.finish)

    def finish(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(3)
        self.assertFalse(self.worker.is_alive())

    def request(self, route, payload=None, *, authenticated=True, origin=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = "Basic " + base64.b64encode(("operator:" + self.token).encode()).decode()
        if origin is not None:
            headers["Origin"] = origin
        connection.request("GET" if payload is None else "POST", route, None if payload is None else json.dumps(payload), headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_all_vision_routes_require_existing_device_credentials(self):
        for route, payload in (("/vision", None), ("/api/vision/state", None), ("/api/vision/frame.png", None),
                               ("/api/vision/freeze", {}), ("/api/vision/calibrate", {}), ("/api/vision/clear", {})):
            with self.subTest(route=route):
                self.assertEqual(self.request(route, payload, authenticated=False)[0], 401)

    def test_image_web_page_and_device_discovery_preserve_source(self):
        self.assertEqual(self.request("/vision")[0], 200)
        status, headers, image = self.request("/api/vision/frame.png")
        self.assertEqual((status, headers["Content-Type"]), (200, "image/png"))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertTrue(image.startswith(b"\x89PNG"))
        status = json.loads(self.request("/api/device")[2])
        self.assertEqual(status["vision_endpoint"], "/vision")
        self.assertEqual(status["vision"]["source"], "HOST_FIXTURE")
        self.assertFalse(status["transmit_enabled"])
        self.assertFalse(status["motion_enabled"])

    def test_calibration_does_not_add_diagnostic_points_or_bypass_origin_policy(self):
        self.assertEqual(self.request("/api/vision/freeze", {}, origin="http://untrusted.invalid")[0], 403)
        selection = json.loads(self.request("/api/vision/freeze", {})[2])["selection"]
        status, _, body = self.request("/api/vision/calibrate", {"calibration_id": selection["calibration_id"], "corners": CORNERS, "fixed_confirmed": True})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["calibrated"])
        self.assertEqual(self.session.points, [])
        self.assertEqual(self.request("/api/vision/calibrate", {"corners": CORNERS})[0], 400)

    def test_no_frame_is_an_explicit_state_not_a_fake_preview(self):
        self.path.unlink()
        record = json.loads(self.request("/api/vision/state")[2])
        self.assertFalse(record["preview_available"])
        self.assertEqual(record["source"], "UNAVAILABLE")
        self.assertEqual(self.request("/api/vision/frame.png")[0], 400)


if __name__ == "__main__":
    unittest.main()
