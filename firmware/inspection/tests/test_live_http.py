import base64
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from utinspect.device_app import create_server
from utinspect.live_service import LiveAcquisition
from utinspect.mapping import MapSession


class LiveHTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.session = MapSession(root / "map", mode="LIVE")
        self.live = LiveAcquisition(root / "lcd")
        self.live.available = True
        self.token = "FixtureCredential_" + "1" * 32
        self.server = create_server("127.0.0.1", 0, "127.0.0.1", self.token, self.session, live=self.live)
        self.worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.worker.start()
        self.addCleanup(self.finish)

    def finish(self):
        self.live.close()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2)

    def request(self, route, payload=None, authenticated=True):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = "Basic " + base64.b64encode(("operator:" + self.token).encode()).decode()
        connection.request("GET" if payload is None else "POST", route,
                           None if payload is None else json.dumps(payload), headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_unauthenticated_view_or_start_cannot_control_capture(self):
        for route, payload in (("/live", None), ("/api/live", None), ("/api/live/start", {})):
            with self.subTest(route=route):
                self.assertEqual(self.request(route, payload, False)[0], 401)
        self.assertFalse(self.live.state()["active"])

    def test_live_page_and_stopped_state_are_visible_without_arming(self):
        status, body = self.request("/live")
        self.assertEqual(status, 200)
        self.assertIn(b"/api/live/keepalive", body)
        status, body = self.request("/api/live")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["state"], "STOPPED")
        status, body = self.request("/api/state")
        self.assertTrue(json.loads(body)["capture_adapter_configured"])
        self.assertEqual(self.session.points, [])

    def test_start_requires_confirmations_stop_and_expired_heartbeat_never_restart(self):
        self.assertEqual(self.request("/api/live/start", {})[0], 400)
        payload = {"owner": "pc-" + "1" * 32, "gain": 128, "probe_coupled": True, "safe_to_pulse": True}
        self.assertEqual(self.request("/api/live/start", payload)[0], 200)
        status, body = self.request("/api/device")
        self.assertTrue(json.loads(body)["transmit_enabled"])
        self.assertEqual(self.request("/api/live/stop", {})[0], 200)
        self.assertEqual(self.request("/api/live/keepalive", {"owner": payload["owner"]})[0], 400)
        self.assertFalse(self.live.state()["active"])


if __name__ == "__main__":
    unittest.main()
