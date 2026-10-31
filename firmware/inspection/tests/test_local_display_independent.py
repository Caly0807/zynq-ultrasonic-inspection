import base64
import http.client
import tempfile
import threading
import unittest
from pathlib import Path

from utinspect.device_app import create_server
from utinspect.mapping import MapSession


class LocalDisplayIndependentTests(unittest.TestCase):
    def test_unassigned_board_address_does_not_prevent_startup_but_access_policy_stays_pinned(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = MapSession(Path(temporary) / "session", mode="DEMO")
            token = "LocalDisplayFixture_" + "5" * 32
            server = create_server("192.168.77.2", 0, "127.0.0.1", token, session,
                                   local_display_independent=True)
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
            worker.start()
            port = server.server_address[1]
            try:
                for host, origin, authenticated, expected in (
                        (f"192.168.77.2:{port}", None, False, 401),
                        (f"192.168.77.2:{port}", None, True, 200),
                        (f"127.0.0.1:{port}", None, True, 403),
                        (f"192.168.77.2:{port}", "http://attacker.example", True, 403)):
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                    headers = {"Host": host}
                    if origin:
                        headers["Origin"] = origin
                    if authenticated:
                        headers["Authorization"] = "Basic " + base64.b64encode(("operator:" + token).encode()).decode()
                    connection.request("GET", "/api/device", headers=headers)
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(response.status, expected)
                    connection.close()
            finally:
                server.shutdown()
                server.server_close()
                worker.join(3)
            self.assertFalse(worker.is_alive())
