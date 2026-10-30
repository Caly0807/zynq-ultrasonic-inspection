import http.client
import json
import tempfile
import threading
import time
import unittest
from dataclasses import asdict
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from utinspect.visual_selftest import demo_workpiece_image, run_visual_selftest
from utinspect.workpiece import PlanarFace, ProbePoint, VisualSettings, WorkpieceSpec, find_probe, save_result
from utinspect.workpiece_app import WorkpieceSession, make_handler, run_workpiece
from utinspect.workpiece_config import read_profile


CORNERS = [(160, 80), (480, 80), (480, 400), (160, 400)]


def red_image():
    image = demo_workpiece_image(())
    cv2.circle(image, (320, 240), 7, (0, 0, 240), -1)
    return image


class FakeCamera:
    backend = "FAKE_CAMERA_UNIT_TEST_ONLY"

    def __init__(self):
        self.timestamp_ns = time.monotonic_ns()

    def release(self):
        pass


class VisualDetectorTests(unittest.TestCase):
    def face(self, image):
        return PlanarFace(CORNERS, image.shape, WorkpieceSpec(dimensions_confirmed=True))

    def test_round_probe_is_only_a_body_center_even_with_contact_requested(self):
        image = demo_workpiece_image()
        face = self.face(image)
        probe, reason, candidates = find_probe(image, face, VisualSettings(probe_mode="round"), True)
        self.assertEqual(reason, "body_center_not_contact")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(probe.method, "ROUND_BODY")
        np.testing.assert_allclose(probe.pixel, (320, 240), atol=4)
        self.assertFalse(probe.contact_on_plane)
        self.assertIsNone(face.position_mm(probe.pixel, probe.contact_on_plane))
        with self.assertRaises(ValueError):
            ProbePoint(probe.pixel, "ROUND_BODY", True)

    def test_multiple_round_objects_are_not_silently_ranked_as_the_probe(self):
        image = demo_workpiece_image(((255, 240), (385, 240)))
        probe, reason, candidates = find_probe(image, self.face(image), VisualSettings(probe_mode="round"))
        self.assertIsNone(probe)
        self.assertEqual(reason, "multiple_round_probes_ambiguous")
        self.assertGreaterEqual(len(candidates), 2)

    def test_round_missing_outside_and_blur_have_explicit_reasons(self):
        for image, expected in ((demo_workpiece_image(()), "round_probe_missing"),
                                (demo_workpiece_image(((70, 240),)), "round_probe_missing"),
                                (np.full((480, 640, 3), 120, np.uint8), "frame_blurred")):
            with self.subTest(reason=expected):
                probe, reason, _ = find_probe(image, self.face(image), VisualSettings(probe_mode="round"))
                self.assertIsNone(probe)
                self.assertEqual(reason, expected)

    def test_red_thresholds_are_tunable_and_manual_mode_does_not_detect(self):
        image = demo_workpiece_image(())
        color = cv2.cvtColor(np.uint8([[[0, 100, 200]]]), cv2.COLOR_HSV2BGR)[0, 0]
        cv2.circle(image, (320, 240), 7, tuple(int(value) for value in color), -1)
        face = self.face(image)
        self.assertIsNone(find_probe(image, face)[0])
        self.assertIsNotNone(find_probe(image, face, VisualSettings(red_saturation_min=80))[0])
        probe, reason, candidates = find_probe(image, face, VisualSettings(probe_mode="manual"))
        self.assertEqual((probe, reason, candidates), (None, "manual_mode", []))

    def test_invalid_settings_are_rejected(self):
        for values in ({"probe_mode": "guess"}, {"focus_min": float("nan")}, {"focus_min": True},
                       {"red_saturation_min": 130.0}, {"red_min_area_px": 0},
                       {"round_min_radius_fraction": 0.3, "round_max_radius_fraction": 0.2},
                       {"round_vote_threshold": 0}, {"round_max_radius_fraction": 0.5}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                VisualSettings(**values)

    def test_round_bodies_cannot_bind_live_diagnoses(self):
        image = demo_workpiece_image()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                save_result(directory, image, self.face(image), ProbePoint((320, 240), "ROUND_BODY"),
                            "LIVE_CAMERA", diagnosis={"source": "LIVE", "status": "SUSPECT", "calibrated": True,
                                                      "calibration_id": "UNIT_TEST_ONLY", "frame_id": "test-waveform"},
                            frame_id="test-camera-frame", frame_timestamp_ns=time.monotonic_ns())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_exported_annotation_and_top_view_visibly_keep_demo_provenance(self):
        image = demo_workpiece_image()
        with tempfile.TemporaryDirectory() as directory, patch("utinspect.workpiece.cv2.putText", wraps=cv2.putText) as draw:
            save_result(directory, image, self.face(image), ProbePoint((320, 240), "ROUND_BODY"),
                        image_source="DEMO", manual_mark=True)
        labels = [call.args[1] for call in draw.call_args_list]
        self.assertEqual(labels.count("DEMO / MANUAL_TEST_NOT_ULTRASOUND"), 2)


class VisualWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.image = red_image()
        self.profile = Path(self.directory.name) / "setup.json"
        self.session = WorkpieceSession(self.image, Path(self.directory.name) / "captures", WorkpieceSpec(dimensions_confirmed=True),
                                       FakeCamera(), profile_path=self.profile)
        self.addCleanup(self.session.close)

    def select_face(self):
        self.session.action({"action": "corners", "corners": CORNERS})

    def test_normal_updates_do_not_automatically_save_photographs_or_profiles(self):
        self.select_face()
        self.session._update(self.image, time.monotonic_ns())
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_frozen_manual_export_keeps_the_exact_old_frame_and_test_identity(self):
        self.select_face()
        self.session.action({"action": "freeze"})
        frame_id = self.session.state()["frame_id"]
        timestamp_ns = self.session.frozen_snapshot["timestamp_ns"]
        self.session.action({"action": "probe", "pixel": [320, 240]})
        future = time.monotonic_ns() + 10_000_000_000
        self.session._update(np.zeros_like(self.image), time.monotonic_ns())
        with patch("utinspect.workpiece_app.time.monotonic_ns", return_value=future):
            self.assertTrue(self.session.state()["manual_point_available"])
            record = self.session.action({"action": "manual_mark"})
        self.assertEqual(record["kind"], "MANUAL_TEST_NOT_ULTRASOUND")
        self.assertTrue(record["frozen_frame"])
        self.assertEqual(record["camera_frame_id"], frame_id)
        self.assertEqual(record["camera_received_monotonic_ns"], timestamp_ns)
        np.testing.assert_allclose(record["plane_position_mm"], (40, 40))
        raw = cv2.imdecode(np.fromfile(self.session.output / record["files"]["raw"], np.uint8), cv2.IMREAD_COLOR)
        np.testing.assert_array_equal(raw, self.image)
        self.session.action({"action": "resume"})
        self.assertFalse(self.session.state()["frozen"])
        self.assertFalse(self.session.state()["manual_point_available"])

    def test_freezing_before_corners_keeps_the_selected_image_pinned(self):
        self.session.action({"action": "freeze"})
        self.select_face()
        self.assertTrue(self.session.state()["frozen"])
        self.assertTrue(self.session.frozen_snapshot["frozen_for_test"])
        self.session.action({"action": "reset"})
        self.assertFalse(self.session.state()["frozen"])
        self.assertIsNone(self.session.state()["face"])

    def test_fresh_frozen_frame_is_still_ineligible_for_live_diagnosis(self):
        self.select_face()
        self.session.action({"action": "contact", "confirmed": True})
        self.session.action({"action": "freeze"})
        payload = {"event_id": "unit-test-frozen", "camera_frame_id": self.session.state()["frame_id"],
                   "diagnosis": {"source": "LIVE", "status": "SUSPECT", "calibrated": True,
                                 "calibration_id": "UNIT_TEST_ONLY", "frame_id": "test-waveform"}}
        with self.assertRaisesRegex(ValueError, "Frozen display frames"):
            self.session.diagnosis(payload)
        self.session.action({"action": "resume"})
        with self.assertRaisesRegex(ValueError, "Frozen display frames"):
            self.session.diagnosis(payload)

    def test_unfrozen_manual_selection_expires_instead_of_using_another_point(self):
        self.select_face()
        self.session.action({"action": "probe", "pixel": [300, 240]})
        with patch("utinspect.workpiece_app.time.monotonic_ns", return_value=time.monotonic_ns() + 3_000_000_000):
            self.assertFalse(self.session.state()["manual_point_available"])
            with self.assertRaisesRegex(ValueError, "Manual selection expired"):
                self.session.action({"action": "manual_mark"})

    def test_settings_keep_the_face_but_revoke_contact_and_old_frame_associations(self):
        self.select_face()
        self.session.action({"action": "contact", "confirmed": True})
        old_id = self.session.state()["frame_id"]
        self.session.action({"action": "settings", "settings": {"probe_mode": "round"}})
        self.assertIsNotNone(self.session.state()["face"])
        self.assertFalse(self.session.state()["contact_on_plane_confirmed"])
        with self.assertRaises(ValueError):
            self.session.action({"action": "contact", "confirmed": True})
        with self.assertRaisesRegex(ValueError, "geometry changed"):
            self.session._snapshot(old_id)

    def test_profile_roundtrip_never_implicitly_restores_geometry_or_contact(self):
        self.select_face()
        self.session.action({"action": "contact", "confirmed": True})
        self.session.action({"action": "profile_save"})
        self.session.action({"action": "profile_load"})
        self.assertTrue(self.session.spec.dimensions_confirmed)
        self.assertIsNone(self.session.face)
        self.assertFalse(self.session.contact_on_plane)
        self.assertTrue(self.session.state()["profile"]["face_reference_compatible"])
        with self.assertRaises(ValueError):
            self.session.action({"action": "restore_face"})
        self.session.action({"action": "restore_face", "confirmed": True})
        np.testing.assert_allclose(self.session.face.corners, CORNERS, atol=0.001)
        self.assertFalse(self.session.contact_on_plane)

    def test_saved_corners_cannot_be_restored_for_another_camera_or_resolution(self):
        self.select_face()
        self.session.action({"action": "profile_save"})
        self.session.camera.backend = "ANOTHER_FAKE_CAMERA"
        self.assertFalse(self.session.state()["profile"]["face_reference_compatible"])
        with self.assertRaises(ValueError):
            self.session.action({"action": "restore_face", "confirmed": True})
        self.session.camera.backend = FakeCamera.backend
        self.session._update(cv2.resize(self.image, (1280, 960)), time.monotonic_ns())
        self.assertFalse(self.session.state()["profile"]["face_reference_compatible"])
        with self.assertRaises(ValueError):
            self.session.action({"action": "restore_face", "confirmed": True})

    def test_malformed_profiles_and_unknown_settings_are_rejected(self):
        data = {"schema": 1, "workpiece": asdict(WorkpieceSpec()), "vision": asdict(VisualSettings()), "face_reference": None}
        for changes in ({"schema": True}, {"workpiece": {"length_mm": -1}}, {"vision": {"probe_mode": "unknown"}},
                        {"face_reference": {"image_size": [640, 480], "camera_identity": "test", "normalized_corners": [[0, 0]]}}):
            with self.subTest(changes=changes):
                payload = {**data, **changes}
                self.profile.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_profile(self.profile)
        with self.assertRaises(ValueError):
            self.session.action({"action": "settings", "settings": {"unexpected": 1}})

    def test_offline_selftest_exports_only_labelled_demo_images(self):
        report = run_visual_selftest(Path(self.directory.name) / "selftest")
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(report["source"], "DEMO")
        self.assertFalse(report["real_camera_accessed"])
        self.assertFalse(report["hardware_control"])
        self.assertEqual(len(report["steps"]), 5)
        self.assertTrue(all(record["image_source"] == "DEMO" for record in report["records"]))

    def test_profile_size_is_bounded_before_json_parsing(self):
        self.profile.write_bytes(b" " * 65537)
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            read_profile(self.profile)

    def test_shipped_profile_template_has_confirmed_dimensions_but_no_calibrated_geometry(self):
        spec, settings, reference = read_profile(Path(__file__).resolve().parents[1] / "workpiece_profile.example.json")
        self.assertEqual((spec.length_mm, spec.width_mm, spec.thickness_mm), (80, 80, 30))
        self.assertTrue(spec.dimensions_confirmed)
        self.assertEqual(settings.probe_mode, "round")
        self.assertIsNone(reference)

    def test_startup_uses_confirmed_defaults_and_saved_profile_takes_precedence(self):
        args = SimpleNamespace(port=8765, length_mm=80, width_mm=80, thickness_mm=30, dimensions_confirmed=True,
                               probe_mode="round", profile=str(self.profile), image="unit_test_only_not_camera.png",
                               out=str(Path(self.directory.name) / "not_saved"), preview=False)
        sessions = []
        def create_session(*arguments):
            session = WorkpieceSession(*arguments)
            sessions.append(session)
            return session
        with patch("utinspect.workpiece_app.np.fromfile", return_value=np.zeros(1, np.uint8)), \
                patch("utinspect.workpiece_app.cv2.imdecode", return_value=self.image), \
                patch("utinspect.workpiece_app.ThreadingHTTPServer"), \
                patch("utinspect.workpiece_app.WorkpieceSession", side_effect=create_session):
            run_workpiece(args)
            self.assertTrue(sessions[-1].spec.dimensions_confirmed)
            self.assertEqual(sessions[-1].settings.probe_mode, "round")
            self.select_face()
            self.session.action({"action": "profile_save"})
            run_workpiece(args)
            self.assertEqual(sessions[-1].settings.probe_mode, "red")
            self.assertIsNone(sessions[-1].face)
            self.assertIsNotNone(sessions[-1].saved_face_reference)


class VisualHttpWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.session = WorkpieceSession(red_image(), Path(self.directory.name) / "captures", WorkpieceSpec(dimensions_confirmed=True),
                                       profile_path=Path(self.directory.name) / "setup.json")
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

    def request(self, route, payload=None):
        with closing(http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)) as connection:
            body = json.dumps(payload) if payload is not None else None
            connection.request("POST" if body else "GET", route, body, {"Content-Type": "application/json"} if body else {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()

    def test_full_frozen_mark_and_profile_roundtrip_through_http(self):
        self.assertEqual(self.request("/api/action", {"action": "freeze"})[0], 200)
        self.assertEqual(self.request("/api/action", {"action": "corners", "corners": CORNERS})[0], 200)
        status, _, encoded = self.request("/api/state")
        state = json.loads(encoded)
        self.assertEqual((status, state["visual_api_version"], state["frozen"]), (200, 2, True))
        self.assertEqual(self.request("/api/action", {"action": "probe", "pixel": [320, 240], "frame_id": state["frame_id"]})[0], 200)
        status, _, encoded = self.request("/api/action", {"action": "manual_mark"})
        record = json.loads(encoded)
        self.assertEqual(status, 200)
        self.assertTrue(record["frozen_frame"])
        self.assertEqual(record["kind"], "MANUAL_TEST_NOT_ULTRASOUND")
        for filename in record["files"].values():
            self.assertEqual(self.request("/captures/" + filename)[0], 200)
        for action in ("profile_save", "profile_load"):
            self.assertEqual(self.request("/api/action", {"action": action})[0], 200)
        self.assertEqual(self.request("/api/action", {"action": "restore_face"})[0], 400)
        self.assertEqual(self.request("/api/action", {"action": "restore_face", "confirmed": True})[0], 200)

    def test_raw_endpoint_contains_no_added_annotations_and_keeps_the_frame_id(self):
        status, headers, data = self.request("/api/raw.jpg")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Camera-Frame-Id"], self.session.state()["frame_id"])
        raw = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        annotated = cv2.imdecode(np.frombuffer(self.request("/api/frame.jpg")[2], np.uint8), cv2.IMREAD_COLOR)
        self.assertGreater(float(np.mean(cv2.absdiff(raw[:33], annotated[:33]))), 1)
        self.assertEqual(headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
