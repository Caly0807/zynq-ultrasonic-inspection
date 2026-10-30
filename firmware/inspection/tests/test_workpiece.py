import http.client
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from utinspect.workpiece import PlanarFace, ProbePoint, WorkpieceSpec, draw_point, find_red_probe, find_workpieces, order_corners, save_result, sharpness
from utinspect.workpiece_app import WorkpieceSession, make_handler


CORNERS = [(160, 80), (480, 80), (480, 400), (160, 400)]


def fixture_image(points=((320, 240),)):
    image = np.full((480, 640, 3), 28, np.uint8)
    cv2.fillConvexPoly(image, np.array(CORNERS, np.int32), (170, 170, 170))
    cv2.polylines(image, [np.array(CORNERS, np.int32)], True, (235, 235, 235), 3)
    for point in points:
        cv2.circle(image, point, 6, (0, 0, 240), -1)
    return image


def suspect(source="RECORDED", **changes):
    return {"source": source, "status": "DEMO_SUSPECT" if source == "DEMO" else "SUSPECT",
            "calibrated": True, "calibration_id": "TEST_ONLY_NOT_A_PHYSICAL_CALIBRATION",
            "frame_id": "synthetic-acquisition-1", **changes}


class FakeCamera:
    def __init__(self):
        self.timestamp_ns = time.monotonic_ns()

    def release(self):
        pass


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.image = fixture_image()
        self.spec = WorkpieceSpec(dimensions_confirmed=True)
        self.face = PlanarFace(CORNERS, self.image.shape, self.spec)

    def test_dimensions_and_contact_confirmation_are_validated(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                WorkpieceSpec(length_mm=value)
        with self.assertRaises(ValueError):
            WorkpieceSpec(dimensions_confirmed="yes")
        with self.assertRaises(ValueError):
            ProbePoint((320, 240), "RED_STICKER", "false")

    def test_corner_order_and_invalid_faces(self):
        np.testing.assert_allclose(order_corners([CORNERS[index] for index in (2, 0, 3, 1)]), CORNERS)
        for corners in ([(1, 1)] * 4, [(0, 0), (1, 1), (2, 2), (3, 3)],
                        [(-1, 80), *CORNERS[1:]]):
            with self.subTest(corners=corners), self.assertRaises(ValueError):
                PlanarFace(corners, self.image.shape, self.spec)

    def test_perspective_mapping_uses_the_selected_plane(self):
        face = PlanarFace([(140, 90), (500, 130), (450, 400), (180, 370)], self.image.shape, self.spec)
        normalized = np.array([[[0.25, 0.75]]], np.float32)
        pixel = cv2.perspectiveTransform(normalized, np.linalg.inv(face.transform))[0, 0]
        np.testing.assert_allclose(face.normalized(pixel), (0.25, 0.75), atol=1e-6)
        np.testing.assert_allclose(face.position_mm(pixel, True), (20, 60), atol=1e-4)

    def test_millimetres_require_confirmed_dimensions_and_contact(self):
        self.assertIsNone(self.face.position_mm((320, 240)))
        self.assertIsNone(self.face.position_mm((100, 20), True))
        unconfirmed = PlanarFace(CORNERS, self.image.shape, WorkpieceSpec())
        self.assertIsNone(unconfirmed.position_mm((320, 240), True))
        np.testing.assert_allclose(self.face.position_mm((320, 240), True), (40, 40))
        self.assertEqual(self.face.describe()["accuracy"], "UNCALIBRATED_PLANAR_ESTIMATE")

    def test_geometric_candidate_is_not_generated_from_a_flat_blurred_frame(self):
        self.assertGreater(sharpness(self.image), 12)
        self.assertTrue(find_workpieces(self.image))
        blurred = np.full_like(self.image, 128)
        self.assertEqual(find_workpieces(blurred), [])

    def test_red_probe_rejects_missing_outside_and_ambiguous_stickers(self):
        probe, reason = find_red_probe(self.image, self.face, True)
        self.assertEqual(reason, "ok")
        np.testing.assert_allclose(probe.pixel, (320, 240))
        self.assertTrue(probe.contact_on_plane)
        for points, expected in (((), "red_sticker_missing"), (((30, 30),), "red_sticker_missing"),
                                 (((280, 240), (360, 240)), "multiple_red_stickers_ambiguous")):
            with self.subTest(points=points):
                probe, reason = find_red_probe(fixture_image(points), self.face)
                self.assertIsNone(probe)
                self.assertEqual(reason, expected)

    def test_saved_manual_images_and_json_keep_their_identity(self):
        probe = ProbePoint((320, 240), "MANUAL_CONTACT", True)
        with tempfile.TemporaryDirectory() as directory:
            record = save_result(directory, self.image, self.face, probe, manual_mark=True)
            self.assertEqual(record["kind"], "MANUAL_TEST_NOT_ULTRASOUND")
            self.assertEqual(record["plane_position_mm"], (40, 40))
            self.assertFalse(record["transmit_enabled_by_this_software"])
            self.assertFalse(record["motor_outputs_implemented"])
            for name in ("raw", "annotated", "top"):
                image = cv2.imdecode(np.fromfile(Path(directory) / record["files"][name], np.uint8), cv2.IMREAD_COLOR)
                self.assertIsNotNone(image)
            saved = json.loads((Path(directory) / record["files"]["json"]).read_text(encoding="utf-8"))
            self.assertEqual(saved["kind"], record["kind"])
            self.assertEqual(saved["probe"]["method"], "MANUAL_CONTACT")

    def test_mark_export_does_not_overlap_probe_and_event_labels(self):
        probe = ProbePoint((320, 240), "MANUAL_CONTACT", True)
        with tempfile.TemporaryDirectory() as directory, patch("utinspect.workpiece.draw_point", wraps=draw_point) as draw:
            save_result(directory, self.image, self.face, probe, manual_mark=True)
            labels = [call.args[2] for call in draw.call_args_list]
            self.assertEqual(labels, ["MANUAL_TEST_NOT_ULTRASOUND", "MANUAL_TEST_NOT_ULTRASOUND"])

    def test_diagnosis_cannot_disguise_manual_demo_or_invalid_identifiers(self):
        probe = ProbePoint((320, 240), "RED_STICKER", True)
        invalid = [suspect("DEMO", status="SUSPECT"), suspect(status="DEMO_SUSPECT"),
                   suspect(calibration_id=None), suspect(frame_id=None), suspect(calibrated=False)]
        with tempfile.TemporaryDirectory() as directory:
            for diagnosis in invalid:
                with self.subTest(diagnosis=diagnosis), self.assertRaises(ValueError):
                    save_result(directory, self.image, self.face, probe, diagnosis=diagnosis)
            with self.assertRaises(ValueError):
                save_result(directory, self.image, self.face, probe, diagnosis=suspect(), manual_mark=True)
            record = save_result(directory, self.image, self.face, probe, diagnosis=suspect("DEMO"))
            self.assertEqual(record["kind"], "DEMO_SUSPECT")
            self.assertEqual(record["image_source"], "RECORDED")


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.image = fixture_image()
        self.session = WorkpieceSession(self.image, self.directory.name, WorkpieceSpec(dimensions_confirmed=True))
        self.addCleanup(self.session.close)

    def select_face(self):
        self.session.action({"action": "corners", "corners": CORNERS, "frame_id": self.session.state()["frame_id"]})

    def live_session(self):
        self.session.camera = FakeCamera()
        self.session.image_source = "LIVE_CAMERA"
        self.select_face()
        self.session.action({"action": "contact", "confirmed": True})

    def event(self, source="RECORDED", **changes):
        return {"event_id": "synthetic-event-1", "camera_frame_id": self.session.state()["frame_id"],
                "diagnosis": suspect(source), **changes}

    def test_geometry_changes_refresh_state_and_revoke_contact_confirmation(self):
        self.select_face()
        self.assertIsNotNone(self.session.state()["probe"])
        self.assertIsNone(self.session.state()["plane_position_mm"])
        self.session.action({"action": "contact", "confirmed": True})
        np.testing.assert_allclose(self.session.state()["plane_position_mm"], (40, 40))
        old_frame = self.session.state()["frame_id"]
        self.select_face()
        self.assertFalse(self.session.state()["contact_on_plane_confirmed"])
        self.assertIsNone(self.session.state()["plane_position_mm"])
        with self.assertRaisesRegex(ValueError, "geometry changed"):
            self.session.diagnosis(self.event(camera_frame_id=old_frame))

    def test_spec_reset_clears_face_manual_point_and_marks(self):
        self.select_face()
        self.session.action({"action": "probe", "pixel": [320, 240]})
        self.session.action({"action": "manual_mark"})
        self.session.action({"action": "spec", "length_mm": 80, "width_mm": 80, "thickness_mm": 30})
        state = self.session.state()
        self.assertIsNone(state["face"])
        self.assertFalse(state["manual_point_available"])
        self.assertFalse(self.session.marks)
        self.assertEqual(state["history"][0]["kind"], "MANUAL_TEST_NOT_ULTRASOUND")

    def test_diagnosis_requires_an_exact_cached_camera_frame(self):
        self.select_face()
        for frame_id in (None, "", "missing-frame"):
            with self.subTest(frame_id=frame_id), self.assertRaises(ValueError):
                self.session.diagnosis(self.event(camera_frame_id=frame_id))
        old_frame = self.session.state()["frame_id"]
        for _ in range(31):
            self.session._update(self.image, time.monotonic_ns())
        with self.assertRaisesRegex(ValueError, "cached"):
            self.session.diagnosis(self.event(camera_frame_id=old_frame))

    def test_live_rejects_stale_future_blurred_and_offline_frames(self):
        self.live_session()
        for timestamp_ns in (time.monotonic_ns() - 3_000_000_000, time.monotonic_ns() + 3_000_000_000):
            with self.subTest(timestamp_ns=timestamp_ns):
                self.session._update(self.image, timestamp_ns)
                with self.assertRaises(ValueError):
                    self.session.diagnosis(self.event("LIVE"))
        self.session._update(np.full_like(self.image, 128), time.monotonic_ns())
        with self.assertRaisesRegex(ValueError, "blurred"):
            self.session.diagnosis(self.event("LIVE"))
        self.session._update(self.image, time.monotonic_ns())
        self.session.error = "synthetic disconnect"
        with self.assertRaises(ValueError):
            self.session.diagnosis(self.event("LIVE"))

    def test_live_rejects_manual_points_and_unconfirmed_contact(self):
        self.live_session()
        self.session.action({"action": "contact", "confirmed": False})
        with self.assertRaises(ValueError):
            self.session.diagnosis(self.event("LIVE"))
        with self.assertRaises(ValueError):
            save_result(self.directory.name, self.image, self.session.face,
                        ProbePoint((320, 240), "MANUAL_CONTACT", True), "LIVE_CAMERA", suspect("LIVE"),
                        frame_id="synthetic-frame", frame_timestamp_ns=time.monotonic_ns())

    def test_live_manual_point_cannot_be_reused_after_its_frame_expires(self):
        self.live_session()
        self.session.action({"action": "probe", "pixel": [320, 240]})
        with patch("utinspect.workpiece_app.time.monotonic_ns", return_value=time.monotonic_ns() + 3_000_000_000):
            with self.assertRaises(ValueError):
                self.session.action({"action": "manual_mark"})

    def test_valid_synthetic_live_event_is_traceable_and_not_counted_twice(self):
        self.live_session()
        event = self.event("LIVE")
        record = self.session.diagnosis(event)
        self.assertEqual(record["kind"], "LIVE_SUSPECT")
        self.assertEqual(record["event_id"], event["event_id"])
        saved = json.loads((Path(self.directory.name) / record["files"]["json"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["event_id"], event["event_id"])
        self.assertEqual(self.session.state()["live_suspect_count"], 1)
        with self.assertRaises(ValueError):
            self.session.diagnosis(event)
        with self.assertRaises(ValueError):
            self.session.diagnosis({**event, "event_id": "different-event-same-acquisition"})
        self.assertEqual(self.session.state()["live_suspect_count"], 1)

    def test_recorded_and_demo_events_do_not_increment_live_count(self):
        self.select_face()
        for source in ("RECORDED", "DEMO"):
            record = self.session.diagnosis(self.event(source, event_id=source))
            self.assertEqual(record["kind"], source + "_SUSPECT")
        self.assertEqual(self.session.state()["live_suspect_count"], 0)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.session = WorkpieceSession(fixture_image(), self.directory.name, WorkpieceSpec())
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.session, lambda: None))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.session.close()
        self.directory.cleanup()

    def request(self, method, route, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        self.addCleanup(connection.close)
        connection.request(method, route, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def test_serves_page_state_and_frame_with_matching_identity(self):
        status, _, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"UT STUDIO / WORKPIECE LOCALIZATION", page)
        status, _, data = self.request("GET", "/api/state")
        state = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual(state["source"], "RECORDED")
        status, headers, image = self.request("GET", "/api/frame.jpg")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Camera-Frame-Id"], state["frame_id"])
        self.assertIsNotNone(cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR))
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_rejects_foreign_host_origin_non_json_and_non_object(self):
        for headers in ({"Host": "attacker.invalid"}, {"Origin": "https://attacker.invalid"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("GET", "/api/state", headers=headers)[0], 403)
        self.assertEqual(self.request("POST", "/api/action", "{}", {"Content-Type": "text/plain"})[0], 403)
        for body in ("[]", "{", '{"action":"spec","length_mm":null}'):
            with self.subTest(body=body):
                self.assertEqual(self.request("POST", "/api/action", body, {"Content-Type": "application/json"})[0], 400)

    def test_capture_files_are_served_but_path_traversal_is_rejected(self):
        status, _, data = self.request("POST", "/api/action", '{"action":"capture"}', {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        record = json.loads(data)
        self.assertEqual(self.request("GET", "/captures/" + record["files"]["annotated"])[0], 200)
        for route in ("/captures/../outside.json", "/captures/%2e%2e%2foutside.json", "/captures/missing.png"):
            with self.subTest(route=route):
                self.assertEqual(self.request("GET", route)[0], 404)

    def test_rejected_non_json_bodies_return_complete_errors_without_actions(self):
        with patch.object(self.session, "action", side_effect=AssertionError("Rejected body must not perform an action")):
            for attempt in range(30):
                with self.subTest(attempt=attempt):
                    status, headers, body = self.request("POST", "/api/action", '{"action":"reset"}', {"Content-Type": "text/plain"})
                    self.assertEqual(status, 403)
                    self.assertEqual(int(headers["Content-Length"]), len(body))
                    self.assertIn("JSON requests required", json.loads(body)["error"])


if __name__ == "__main__":
    unittest.main()
