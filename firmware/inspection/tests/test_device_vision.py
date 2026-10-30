import base64
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from utinspect.device_vision import create_vision_server, main
from utinspect.visual_selftest import demo_workpiece_image
from utinspect.workpiece import WorkpieceSpec
from utinspect.workpiece_app import WorkpieceSession


class DeviceVisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        image = demo_workpiece_image()
        self.session = WorkpieceSession(image, self.root / "captures", WorkpieceSpec())
        self.token = "VisionDeviceCredential_" + "8" * 32
        self.server = create_vision_server("127.0.0.1", 0, "127.0.0.1", self.token, self.session)
        self.worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02})
        self.worker.start()
        self.addCleanup(self.finish)

    def finish(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(3)
        self.session.close()
        self.assertFalse(self.worker.is_alive())

    def request(self, route="/api/state", authenticated=True, headers=None, payload=None):
        supplied = {"Content-Type": "application/json"}
        if authenticated:
            supplied["Authorization"] = "Basic " + base64.b64encode(("operator:" + self.token).encode("ascii")).decode("ascii")
        supplied.update(headers or {})
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        connection.request("POST" if payload is not None else "GET", route,
                           json.dumps(payload).encode("utf-8") if payload is not None else None, supplied)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_frames_and_state_require_device_login(self):
        for route in ("/", "/api/state", "/api/device", "/api/frame.jpg", "/api/top.png"):
            with self.subTest(route=route):
                status, headers, _ = self.request(route, authenticated=False)
                self.assertEqual(status, 401)
                self.assertIn("Basic", headers["WWW-Authenticate"])

    def test_recorded_picture_does_not_become_live_camera(self):
        state = json.loads(self.request()[2])
        self.assertEqual(state["device"]["image_source"], "RECORDED")
        self.assertFalse(state["device"]["vision_active"])
        self.assertFalse(state["device"]["live_capture_ready"])
        self.assertFalse(state["device"]["transmit_enabled"])
        self.assertEqual(json.loads(self.request("/api/device")[2]), self.server.device_status)

    def test_authenticated_vision_runs_existing_image_pipeline(self):
        status, headers, body = self.request("/api/frame.jpg")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        self.assertTrue(body.startswith(b"\xff\xd8"))
        self.assertEqual(self.request("/api/top.png")[0], 200)

    def test_cross_origin_and_unauthenticated_actions_do_not_change_geometry(self):
        geometry = self.session.geometry_id
        self.assertEqual(self.request("/api/action", authenticated=False, payload={})[0], 401)
        self.assertEqual(self.request("/api/action", headers={"Origin": "http://attacker.example"}, payload={})[0], 403)
        self.assertEqual(self.session.geometry_id, geometry)

    def test_device_cli_cannot_open_a_windows_camera(self):
        with patch("utinspect.device_vision.sys.platform", "win32"), patch("utinspect.device_vision.Camera") as camera:
            with self.assertRaisesRegex(ValueError, "Linux"):
                main(["--data", str(self.root / "must_not_exist"), "--camera-index", "0"])
            camera.assert_not_called()
        self.assertFalse((self.root / "must_not_exist").exists())


if __name__ == "__main__":
    unittest.main()
