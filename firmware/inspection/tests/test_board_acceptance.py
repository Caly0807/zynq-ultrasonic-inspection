import contextlib
import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path

from utinspect.device_app import create_server
from utinspect.mapping import MapSession


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("board_acceptance", ROOT / "petalinux/board_acceptance.py")
ACCEPTANCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCEPTANCE)
TOKEN = "unit-test-credential-not-for-use-0123456789"


@contextlib.contextmanager
def service(mode="DEMO"):
    with tempfile.TemporaryDirectory() as directory:
        session = MapSession(Path(directory) / "session", mode=mode)
        session.bind_local_display(Path(directory) / "lcd")
        server = create_server("127.0.0.1", 0, "127.0.0.1", TOKEN, session)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield session, server
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)


class BoardAcceptanceTests(unittest.TestCase):
    def client(self, server, allow_host_test=True):
        return ACCEPTANCE.BoardClient("127.0.0.1", server.server_address[1], TOKEN, allow_host_test=allow_host_test)

    def test_read_only_probe_does_not_create_points_or_claim_hardware(self):
        with service() as (session, server):
            record = self.client(server).verify()
            self.assertEqual(session.points, [])
            self.assertTrue(record["host_test_only"])
            self.assertEqual(record["operations"], "READ_ONLY_HTTP")
            self.assertFalse(record["real_ultrasound_verified"])
            self.assertNotIn(TOKEN, json.dumps(record))

    def test_demo_transaction_is_idempotent_and_publishes_lcd_files(self):
        with service() as (session, server):
            record = self.client(server).verify(True)
            self.assertTrue(record["idempotency_verified"])
            self.assertEqual(len(session.points), 1)
            self.assertTrue((session.display_directory / "waveform.csv").is_file())
            self.assertIn(",DEMO,", (session.display_directory / "map.csv").read_text())
            self.assertFalse(record["camera_verified"])
            self.assertNotIn(TOKEN, json.dumps(record))

    def test_recorded_session_cannot_be_used_for_synthetic_post(self):
        with service("RECORDED") as (session, server):
            with self.assertRaisesRegex(ValueError, "explicitly synthetic"):
                self.client(server).verify(True)
            self.assertEqual(session.points, [])

    def test_host_runtime_cannot_be_presented_as_arm_acceptance(self):
        with service() as (session, server):
            client = self.client(server)
            client.allow_host_test = False
            with self.assertRaisesRegex(ValueError, "ARM Linux"):
                client.verify()
            self.assertEqual(session.points, [])

    def test_unsafe_state_aborts_before_any_post(self):
        with service() as (session, server):
            server.device_status["transmit_enabled"] = True
            with self.assertRaisesRegex(ValueError, "transmit or motion"):
                self.client(server).verify(True)
            self.assertEqual(session.points, [])

    def test_unexpected_address_and_redirect_are_rejected(self):
        for address in ("8.8.8.8", "192.168.77.3", "0.0.0.0", "localhost"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                ACCEPTANCE.BoardClient(address, 8766, TOKEN)
        with self.assertRaisesRegex(ValueError, "redirects"):
            ACCEPTANCE.NoRedirects().redirect_request(None, None, 302, "redirect", {}, "http://192.168.77.3")


if __name__ == "__main__":
    unittest.main()
