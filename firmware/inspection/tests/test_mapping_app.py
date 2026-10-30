import hashlib
import http.client
import json
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path

from utinspect.echo import EchoConfig, load_csv, write_lvgl_csv
from utinspect.mapping import MapSession, MapSpec, embedded_csv, synthetic_map_waveform
from utinspect.mapping_app import MAX_BODY, make_map_handler


class CaptureStub:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []
        self.entered = threading.Event()
        self.release = None

    def capture(self, request_id, stopped):
        self.calls.append(request_id)
        self.entered.set()
        if self.release is not None and not self.release.wait(3):
            raise TimeoutError("Synthetic capture test timed out")
        return self.frame


class MappingHttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.spec = MapSpec(normal_incidence_confirmed=True)
        self.echo = replace(EchoConfig(), calibrated=True, calibration_id="SYNTHETIC_TEST_NOT_MEASUREMENT")
        self.servers = []
        self.counter = 0

    def start(self, mode="DEMO", capture=None):
        self.counter += 1
        session = MapSession(self.root / str(self.counter), self.spec, self.echo, mode)
        stopped = threading.Event()
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_map_handler(session, capture, stopped))
        server.daemon_threads = True
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02})
        worker.start()

        def finish():
            server.shutdown()
            server.server_close()
            worker.join(3)
            self.assertFalse(worker.is_alive())

        self.addCleanup(finish)
        self.servers.append(server)
        return session, server.server_address[1], stopped

    def request(self, port, route="/api/state", payload=None, headers=None, raw=None, method=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        body = raw if raw is not None else json.dumps(payload).encode("utf-8") if payload is not None else None
        connection.request(method or ("POST" if body is not None else "GET"), route, body,
                           {"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def point(self, port, **changes):
        payload = {"position_mm": [27.5, 32.5], "position_confirmed": True, **changes}
        status, _, data = self.request(port, "/api/point", payload)
        return status, json.loads(data)

    def csv(self, source):
        path = self.root / (uuid.uuid4().hex + ".csv")
        frame = replace(synthetic_map_waveform(27.5, 32.5, self.spec), source=source, gain=192, overrange=True)
        write_lvgl_csv(path, frame)
        return path.read_text(encoding="utf-8")

    def test_state_and_html_are_readable_without_camera_or_hardware(self):
        _, port, _ = self.start()
        status, headers, data = self.request(port)
        self.assertEqual(status, 200)
        state = json.loads(data)
        self.assertEqual(state["sampled_cells"], 0)
        self.assertFalse(state["hardware_control"])
        self.assertFalse(state["capture_adapter_configured"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn(b'"boot"', self.request(port, "/")[2])

    def test_explicit_confirmation_bounds_and_json_shape_are_required(self):
        session, port, _ = self.start()
        for values in ({"position_confirmed": False}, {"position_confirmed": 1},
                       {"position_mm": [81, 0]}, {"position_mm": [True, 0]}, {"position_mm": [1]}):
            with self.subTest(values=values):
                self.assertEqual(self.point(port, **values)[0], 400)
        for raw in (b"[]", b'{"position_mm":[NaN,0]}', b"{", b"null"):
            self.assertEqual(self.request(port, "/api/point", raw=raw)[0], 400)
        self.assertFalse(session.points)

    def test_origin_host_size_and_routes_are_checked(self):
        session, port, _ = self.start()
        self.assertEqual(self.request(port, headers={"Host": "untrusted.example"})[0], 403)
        self.assertEqual(self.request(port, headers={"Origin": "https://untrusted.example"})[0], 403)
        self.assertEqual(self.request(port, headers={"Origin": f"http://127.0.0.1:{port}"})[0], 200)
        self.assertEqual(self.request(port, "/missing")[0], 404)
        self.assertEqual(self.request(port, "/missing", {})[0], 404)
        self.assertEqual(self.request(port, "/api/point", raw=b"{}", headers={"Content-Length": str(MAX_BODY + 1)})[0], 413)
        self.assertEqual(self.request(port, "/api/point", method="POST")[0], 413)
        self.assertFalse(session.points)

    def test_browser_session_binding_rejects_stale_or_mistyped_sessions(self):
        session, port, _ = self.start()
        for identifier in ("another-session", None, 123, False):
            with self.subTest(identifier=identifier):
                status, result = self.point(port, session_id=identifier)
                self.assertEqual(status, 400)
                self.assertIn("Session changed", result["error"])
        self.assertFalse(session.points)
        self.assertEqual(self.point(port, session_id=session.session_id)[0], 200)

    def test_stale_session_cannot_trigger_live_adapter(self):
        frame = replace(synthetic_map_waveform(27.5, 32.5, self.spec), source="LIVE")
        capture = CaptureStub(frame)
        session, port, _ = self.start("LIVE", capture)
        self.assertEqual(self.point(port, session_id="stale-browser-session")[0], 400)
        self.assertFalse(capture.calls)
        self.assertFalse(session.points)

    def test_demo_point_exports_keep_the_same_waveform_and_provenance(self):
        session, port, _ = self.start()
        status, result = self.point(port)
        self.assertEqual(status, 200)
        point = result["point"]
        self.assertEqual(point["source"], "DEMO")
        self.assertIsNone(point["hole_height_mm"])
        self.assertAlmostEqual(point["reflector_depth_mm"] + point["distance_from_bottom_mm"], 30)
        route = "/api/waveform?point_id=" + point["point_id"]
        waveform = json.loads(self.request(port, route)[2])
        self.assertEqual(len(waveform["samples"]), 4096)
        self.assertEqual(waveform["source"], "DEMO")
        exported = self.request(port, "/export/waveform.csv?point_id=" + point["point_id"])
        self.assertEqual(hashlib.sha256(exported[2]).hexdigest(), point["waveform_sha256"])
        self.assertIn("attachment", exported[1]["Content-Disposition"])
        self.assertEqual(self.request(port, "/export/map.csv")[2].decode("ascii"), embedded_csv(session.snapshot()))
        self.assertEqual(json.loads(self.request(port, "/export/session.json")[2])["point_count"], 1)
        self.assertIn(b"ut-manual-map/1", self.request(port, "/export/report.html")[2])
        (session.root / point["waveform"]).write_text("tampered", encoding="ascii")
        self.assertEqual(self.request(port, route)[0], 400)
        self.assertEqual(self.request(port, "/export/waveform.csv?point_id=" + point["point_id"])[0], 400)

    def test_recorded_upload_cannot_upgrade_to_live_or_repeat_a_frame(self):
        session, port, _ = self.start("RECORDED")
        text = self.csv("LIVE")
        status, result = self.point(port, csv=text)
        self.assertEqual(status, 200)
        self.assertEqual(result["point"]["source"], "RECORDED")
        stored = session.waveform_csv(result["point"]["point_id"])
        self.assertIn(b"C0", stored)
        original = load_csv(session.root / result["point"]["waveform"])
        self.assertEqual(original.gain, 192)
        self.assertTrue(original.overrange)
        self.assertEqual(self.point(port, csv=text, position_mm=[40, 40])[0], 400)
        self.assertEqual(len(session.points), 1)
        self.assertEqual(self.point(port, csv=self.csv("DEMO"))[0], 400)
        self.assertEqual(self.point(port, diagnosis={"status": "SUSPECT", "depth_mm": 12})[0], 400)

    def test_demo_does_not_accept_external_waveform_uploads(self):
        session, port, _ = self.start()
        self.assertEqual(self.point(port, csv=self.csv("LIVE"))[0], 400)
        self.assertFalse(session.points)

    def test_live_requires_fresh_capture_and_never_uploaded_csv(self):
        session, port, _ = self.start("LIVE")
        self.assertEqual(self.point(port)[0], 400)
        self.assertFalse(session.points)
        frame = replace(synthetic_map_waveform(27.5, 32.5, self.spec), source="LIVE",
                        acquired_monotonic_ns=time.monotonic_ns())
        capture = CaptureStub(frame)
        session, port, _ = self.start("LIVE", capture)
        self.assertEqual(self.point(port, position_confirmed=False)[0], 400)
        self.assertFalse(capture.calls)
        self.assertEqual(self.point(port, csv=self.csv("LIVE"))[0], 400)
        self.assertFalse(capture.calls)
        self.assertEqual(self.point(port)[0], 200)
        self.assertEqual(len(capture.calls), 1)
        self.assertEqual(session.points[0]["source"], "LIVE")
        stale = CaptureStub(replace(frame, acquired_monotonic_ns=time.monotonic_ns() - 3_000_000_000))
        session, port, _ = self.start("LIVE", stale)
        self.assertEqual(self.point(port)[0], 400)
        self.assertFalse(session.points)

    def test_second_capture_is_refused_while_another_is_pending(self):
        frame = replace(synthetic_map_waveform(27.5, 32.5, self.spec), source="LIVE",
                        acquired_monotonic_ns=time.monotonic_ns())
        capture = CaptureStub(frame)
        capture.release = threading.Event()
        _, port, _ = self.start("LIVE", capture)
        results = []
        worker = threading.Thread(target=lambda: results.append(self.point(port)))
        worker.start()
        try:
            self.assertTrue(capture.entered.wait(2))
            self.assertEqual(self.point(port)[0], 409)
        finally:
            capture.release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0][0], 200)

    def test_stop_sets_a_latch_and_shuts_down_the_temporary_server(self):
        _, port, stopped = self.start()
        self.assertEqual(self.request(port, "/api/stop", {})[0], 200)
        self.assertTrue(stopped.is_set())

    def test_committed_submission_replays_without_creating_another_demo_point(self):
        session, port, _ = self.start()
        with patch("utinspect.mapping_app.synthetic_map_waveform", wraps=synthetic_map_waveform) as generator:
            status, first = self.point(port, submission_id="browser-submission-1")
            self.assertEqual(status, 200)
            self.assertFalse(first["replayed"])
            status, repeated = self.point(port, submission_id="browser-submission-1")
            self.assertEqual(status, 200)
            self.assertTrue(repeated["replayed"])
            self.assertEqual(first["point"], repeated["point"])
            self.assertEqual(generator.call_count, 1)
        self.assertEqual(len(session.points), 1)
        archived = json.loads((session.root / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(archived["points"][0]["submission_id"], "browser-submission-1")

    def test_submission_id_cannot_be_reused_with_different_coordinates_or_csv(self):
        session, port, _ = self.start()
        self.assertEqual(self.point(port, submission_id="same-position")[0], 200)
        status, result = self.point(port, submission_id="same-position", position_mm=[2.5, 2.5])
        self.assertEqual(status, 409)
        self.assertEqual(result["submission_state"], "CONFLICT")
        self.assertEqual(len(session.points), 1)
        session, port, _ = self.start("RECORDED")
        text = self.csv("LIVE")
        self.assertEqual(self.point(port, submission_id="same-csv", csv=text)[0], 200)
        self.assertEqual(self.point(port, submission_id="same-csv", csv=text)[0], 200)
        self.assertEqual(self.point(port, submission_id="same-csv", csv=text + "\n")[0], 409)
        self.assertEqual(len(session.points), 1)

    def test_live_submission_replay_never_requests_another_capture(self):
        frame = replace(synthetic_map_waveform(27.5, 32.5, self.spec), source="LIVE", acquired_monotonic_ns=time.monotonic_ns())
        capture = CaptureStub(frame)
        session, port, _ = self.start("LIVE", capture)
        status, first = self.point(port, submission_id="live-once", session_id=session.session_id)
        self.assertEqual(status, 200)
        capture.frame = replace(frame, acquired_monotonic_ns=1)
        status, repeated = self.point(port, submission_id="live-once", session_id=session.session_id)
        self.assertEqual(status, 200)
        self.assertTrue(repeated["replayed"])
        self.assertEqual(repeated["point"], first["point"])
        self.assertEqual(len(capture.calls), 1)

    def test_uncertain_capture_or_commit_is_not_retried_with_the_same_submission(self):
        for fail_commit in (False, True):
            with self.subTest(fail_commit=fail_commit):
                frame = replace(synthetic_map_waveform(27.5, 32.5, self.spec), source="LIVE", acquired_monotonic_ns=time.monotonic_ns())
                capture = CaptureStub(frame)
                session, port, _ = self.start("LIVE", capture)
                target, failure = (session, "_commit") if fail_commit else (capture, "capture")
                with patch.object(target, failure, side_effect=TimeoutError("Synthetic uncertain completion")) as failing:
                    status, result = self.point(port, submission_id="uncertain-once")
                    self.assertEqual(status, 400)
                    self.assertEqual(result["submission_state"], "UNCONFIRMED")
                    status, result = self.point(port, submission_id="uncertain-once")
                    self.assertEqual(status, 409)
                    self.assertEqual(result["submission_state"], "UNCONFIRMED")
                    self.assertEqual(failing.call_count, 1)
                self.assertFalse(session.points)
                self.assertFalse(session.submission_ids)
                self.assertEqual(len(capture.calls), int(fail_commit))

    def test_invalid_submission_ids_and_capacity_fail_before_capture(self):
        frame = replace(synthetic_map_waveform(27.5, 32.5, self.spec), source="LIVE", acquired_monotonic_ns=time.monotonic_ns())
        capture = CaptureStub(frame)
        _, port, _ = self.start("LIVE", capture)
        for value in (None, True, 1, [], {}, "", "x" * 97, "../capture", "汉字", "bad\nvalue"):
            with self.subTest(value=value):
                self.assertEqual(self.point(port, submission_id=value)[0], 400)
        for limit in ("MAX_POINTS", "MAX_SUBMISSIONS"):
            with patch("utinspect.mapping_app." + limit, 0):
                status, result = self.point(port, submission_id="limit-check")
                self.assertEqual(status, 400)
                self.assertEqual(result["submission_state"], "REJECTED")
        self.assertFalse(capture.calls)

    def test_client_disconnect_does_not_lose_the_committed_receipt(self):
        frame = replace(synthetic_map_waveform(27.5, 32.5, self.spec), source="LIVE", acquired_monotonic_ns=time.monotonic_ns())
        capture = CaptureStub(frame)
        capture.release = threading.Event()
        self.addCleanup(capture.release.set)
        session, port, _ = self.start("LIVE", capture)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("POST", "/api/point", json.dumps({"position_mm": [27.5, 32.5], "position_confirmed": True,
                                                             "submission_id": "disconnected-client"}), {"Content-Type": "application/json"})
        self.assertTrue(capture.entered.wait(2))
        connection.close()
        capture.release.set()
        for attempt in range(100):
            status, result = self.point(port, submission_id="disconnected-client")
            if status == 200:
                break
            self.assertEqual(status, 409)
            time.sleep(.01)
        self.assertEqual(status, 200)
        self.assertTrue(result["replayed"])
        self.assertEqual(len(session.points), 1)
        self.assertEqual(len(capture.calls), 1)

    def test_pending_same_submission_does_not_request_another_capture(self):
        frame = replace(synthetic_map_waveform(27.5, 32.5, self.spec), source="LIVE", acquired_monotonic_ns=time.monotonic_ns())
        capture = CaptureStub(frame)
        capture.release = threading.Event()
        self.addCleanup(capture.release.set)
        session, port, _ = self.start("LIVE", capture)
        answers = []
        worker = threading.Thread(target=lambda: answers.append(self.point(port, submission_id="in-progress")))
        worker.start()
        try:
            self.assertTrue(capture.entered.wait(2))
            self.assertEqual(self.point(port, submission_id="in-progress")[0], 409)
            self.assertEqual(len(capture.calls), 1)
        finally:
            capture.release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(answers[0][0], 200)
        self.assertEqual(self.point(port, submission_id="in-progress")[0], 200)
        self.assertEqual(len(capture.calls), 1)
        self.assertEqual(len(session.points), 1)
