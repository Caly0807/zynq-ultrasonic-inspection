import base64
import http.client
import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from utinspect.board_capture import decode_status, decode_test_frame, HEADER, STATUS
from utinspect.device_app import create_server, runtime_status
from utinspect.mapping import MapSession


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("transport_board_acceptance", ROOT / "petalinux/board_acceptance.py")
ACCEPTANCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCEPTANCE)


class TransportApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.session = MapSession(self.root / "session", mode="DEMO")
        self.token = "unit-test-not-board-credential-" + "7" * 32
        status = dict(runtime_status(), runtime="ARM_LINUX")
        with patch("utinspect.device_app.runtime_status", return_value=status):
            self.server = create_server("127.0.0.1", 0, "127.0.0.1", self.token, self.session, self.root)
        self.worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02})
        self.worker.start()
        self.addCleanup(self.finish)
        self.payload = {"hv_off_confirmed": True, "test": "FPGA_RAMP_4096"}
        self.transport = decode_status(STATUS.pack(0x314C5455, 1, 800, 480, 2400, 1, 0x200, 1))
        self.transport["selftest"] = decode_test_frame(HEADER.pack(b"UTC1", 1, 2, 0x304, 40, 4096, 123456789) + bytes(range(256)) * 16)

    def finish(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(3)

    def request(self, payload, authenticated=True, origin=None):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = "Basic " + base64.b64encode(("operator:" + self.token).encode()).decode()
        if origin:
            headers["Origin"] = origin
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        connection.request("POST", "/api/transport/selftest", json.dumps(payload), headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_unconfirmed_or_expanded_payload_never_opens_hardware(self):
        with patch("utinspect.device_app.inspect_transport") as inspector:
            for payload in ({}, {"hv_off_confirmed": False, "test": "FPGA_RAMP_4096"},
                            {"hv_off_confirmed": 1, "test": "FPGA_RAMP_4096"},
                            {"hv_off_confirmed": True, "test": "LIVE"}, dict(self.payload, device="/dev/other")):
                with self.subTest(payload=payload):
                    self.assertEqual(self.request(payload)[0], 400)
            inspector.assert_not_called()

    def test_authentication_and_origin_checks_precede_hardware_access(self):
        with patch("utinspect.device_app.inspect_transport") as inspector:
            self.assertEqual(self.request(self.payload, authenticated=False)[0], 401)
            self.assertEqual(self.request(self.payload, origin="http://untrusted.invalid")[0], 403)
            inspector.assert_not_called()

    def test_repeated_cross_origin_post_denials_preserve_the_http_response(self):
        with patch("utinspect.device_app.inspect_transport") as inspector:
            for iteration in range(30):
                with self.subTest(iteration=iteration):
                    self.assertEqual(self.request(self.payload, origin="http://untrusted.invalid")[0], 403)
            inspector.assert_not_called()

    def test_fixed_test_path_never_creates_a_measurement(self):
        with patch("utinspect.device_app.inspect_transport", return_value=self.transport) as inspector:
            status, body = self.request(self.payload)
        self.assertEqual(status, 200)
        inspector.assert_called_once_with(selftest=True)
        self.assertEqual(json.loads(body)["selftest"]["source"], "FPGA_DMA_TEST_NOT_ULTRASOUND")
        self.assertFalse(self.session.points)

    def test_host_runtime_cannot_trigger_even_when_credentials_are_valid(self):
        self.server.device_status["runtime"] = "HOST_TEST_ONLY"
        with patch("utinspect.device_app.inspect_transport") as inspector:
            self.assertEqual(self.request(self.payload)[0], 400)
            inspector.assert_not_called()

    def test_acceptance_requires_hv_confirmation_then_checks_payload_without_new_points(self):
        client = ACCEPTANCE.BoardClient("127.0.0.1", self.server.server_address[1], self.token, allow_host_test=True)
        with patch("utinspect.device_app.inspect_transport", return_value=self.transport) as inspector:
            with self.assertRaises(ValueError):
                client.dma_selftest()
            inspector.assert_not_called()
            result = client.dma_selftest(True)
        self.assertEqual(result["selftest"]["sample_count"], 4096)
        self.assertFalse(self.session.points)


if __name__ == "__main__":
    unittest.main()
