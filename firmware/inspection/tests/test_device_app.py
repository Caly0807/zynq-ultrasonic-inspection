import base64
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from utinspect.device_app import create_server, main, runtime_status
from utinspect.mapping import MapSession, MapSpec
from utinspect.network import NetworkAccess, load_token, private_address


class DeviceServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.token = "TestDeviceCredential_" + "7" * 32
        self.session = MapSession(self.root / "session", MapSpec(grid_mm=20), mode="DEMO")
        self.server = create_server("127.0.0.1", 0, "127.0.0.1", self.token, self.session)
        self.worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02})
        self.worker.start()
        self.addCleanup(self.finish)

    def finish(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(3)
        self.assertFalse(self.worker.is_alive())

    def request(self, route="/api/state", payload=None, headers=None, authenticated=True):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        supplied = {"Content-Type": "application/json"}
        if authenticated:
            credentials = base64.b64encode(("operator:" + self.token).encode("ascii")).decode("ascii")
            supplied["Authorization"] = "Basic " + credentials
        supplied.update(headers or {})
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        connection.request("POST" if body is not None else "GET", route, body, supplied)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def point(self):
        return {"session_id": self.session.session_id, "submission_id": "lan_point_1",
                "position_mm": [27.5, 32.5], "position_confirmed": True}

    def test_credentials_required_for_page_state_and_exports(self):
        for route in ("/", "/api/state", "/api/device", "/export/session.json", "/export/map.csv"):
            with self.subTest(route=route):
                status, headers, _ = self.request(route, authenticated=False)
                self.assertEqual(status, 401)
                self.assertIn("Basic", headers["WWW-Authenticate"])
                self.assertEqual(headers["Cache-Control"], "no-store")

    def test_bad_credentials_cannot_acquire_a_point(self):
        for authorization in ("Basic !!!!", "Bearer " + self.token, "Basic ", "Basic b3BlcmF0b3I6YmFk"):
            with self.subTest(authorization=authorization):
                self.assertEqual(self.request("/api/point", self.point(), {"Authorization": authorization})[0], 401)
        self.assertEqual(self.session.points, [])

    def test_runtime_and_real_source_are_reported_without_hardware_claims(self):
        status, _, body = self.request()
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertEqual(state["source"], "DEMO")
        self.assertFalse(state["capture_adapter_configured"])
        self.assertEqual(state["device"]["browser_role"], "DISPLAY_AND_OPERATOR_INPUT_ONLY")
        for flag in ("live_capture_ready", "vision_active", "local_lcd_ready", "transmit_enabled", "motion_enabled"):
            self.assertIs(state["device"][flag], False)
        device = json.loads(self.request("/api/device")[2])
        self.assertEqual(device, self.server.device_status)

    def test_diagnosis_archives_and_replay_stay_in_service(self):
        first = self.request("/api/point", self.point())
        second = self.request("/api/point", self.point())
        self.assertEqual((first[0], second[0]), (200, 200))
        result = json.loads(first[2])
        self.assertFalse(result["replayed"])
        self.assertTrue(json.loads(second[2])["replayed"])
        self.assertEqual(result["point"]["source"], "DEMO")
        self.assertEqual(len(self.session.points), 1)
        self.assertEqual(len(list((self.session.root / "waveforms").glob("*.csv"))), 1)
        point_id = result["point"]["point_id"]
        waveform = json.loads(self.request("/api/waveform?point_id=" + point_id)[2])
        self.assertEqual(len(waveform["samples"]), 4096)
        self.assertEqual(waveform["source"], "DEMO")
        self.assertIn(b'"diagnosis"', self.request("/export/session.json")[2])

    def test_host_origin_and_cross_site_requests_are_rejected(self):
        for headers in ({"Host": "attacker.example"}, {"Origin": "https://attacker.example"},
                        {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/api/point", self.point(), headers)[0], 403)
        self.assertFalse(self.session.points)

    def test_exact_origin_is_accepted(self):
        origin = "http://127.0.0.1:" + str(self.server.server_address[1])
        self.assertEqual(self.request(headers={"Origin": origin})[0], 200)

    def test_non_json_and_transfer_encoding_are_rejected(self):
        for headers in ({"Content-Type": "text/plain"}, {"Transfer-Encoding": "chunked"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/api/point", self.point(), headers)[0], 400)
        self.assertFalse(self.session.points)

    def test_duplicate_host_is_rejected(self):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        connection.putrequest("GET", "/api/state")
        connection.putheader("Host", "attacker.example")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        connection.close()

    def test_peer_is_an_exact_ip_not_a_whole_subnet(self):
        access = NetworkAccess("127.0.0.1", "127.0.0.2", self.token)
        handler = type("Request", (), {"client_address": ("127.0.0.1", 1234)})()
        self.assertFalse(access.allowed(handler))

    def test_credential_and_hardware_paths_are_not_exported(self):
        for route in ("/operator.token", "/export/../../operator.token", "/api/hv", "/api/motor"):
            with self.subTest(route=route):
                self.assertEqual(self.request(route)[0], 404)


class DeviceConfigurationTests(unittest.TestCase):
    def test_minimal_service_does_not_import_camera_or_opencv(self):
        result = subprocess.run([sys.executable, "-S", "-c",
                                 "import sys; import utinspect.device_app; "
                                 "assert 'cv2' not in sys.modules and 'numpy' not in sys.modules; print('STDLIB_ONLY')"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("STDLIB_ONLY", result.stdout)

    def test_wildcard_public_and_link_local_bindings_are_rejected(self):
        for value in ("0.0.0.0", "8.8.8.8", "169.254.1.2", "224.0.0.1", "localhost", "::1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                private_address(value)
        self.assertEqual(private_address("192.168.77.2"), "192.168.77.2")

    def test_host_test_cannot_claim_board_execution(self):
        with patch("utinspect.device_app.platform.machine", return_value="AMD64"):
            self.assertEqual(runtime_status()["runtime"], "HOST_TEST_ONLY")
        with patch("utinspect.device_app.platform.machine", return_value="armv7l"), patch("utinspect.device_app.sys.platform", "linux"):
            self.assertEqual(runtime_status()["runtime"], "ARM_LINUX")

    def test_cli_rejects_invalid_settings_before_creating_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "must_not_exist"
            for options in (("--bind", "0.0.0.0"), ("--port", "80"),
                            ("--bind", "192.168.77.2", "--peer", "192.168.77.2")):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    main(["--data", str(output), *options])
                self.assertFalse(output.exists())

    def test_credential_is_generated_once_and_is_strong(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operator.token"
            original = load_token(path)
            self.assertGreaterEqual(len(original), 32)
            self.assertEqual(load_token(path), original)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_concurrent_services_share_one_complete_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operator.token"
            with ThreadPoolExecutor(max_workers=8) as workers:
                tokens = list(workers.map(lambda ignored: load_token(path), range(32)))
            self.assertEqual(len(set(tokens)), 1)
            self.assertEqual(list(path.parent.glob(".ut-credential-*")), [])

    def test_weak_or_world_readable_credentials_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operator.token"
            path.write_text("root", encoding="ascii")
            path.chmod(0o600)
            with self.assertRaises(ValueError):
                load_token(path)
            if os.name != "nt":
                path.write_text("7" * 48, encoding="ascii")
                path.chmod(0o644)
                with self.assertRaises(ValueError):
                    load_token(path)

    @unittest.skipIf(os.name == "nt", "POSIX credential symlink check")
    def test_symlink_credential_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original"
            load_token(original)
            link = Path(directory) / "operator.token"
            link.symlink_to(original)
            with self.assertRaises(ValueError):
                load_token(link)


if __name__ == "__main__":
    unittest.main()
