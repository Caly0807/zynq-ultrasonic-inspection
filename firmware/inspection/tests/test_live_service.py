import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from utinspect.live_capture import HEADER, RawCapture
from utinspect.live_service import LiveAcquisition
from utinspect.live_result import RESULT


OWNER = "pc-" + "1" * 32


class FakeCapture:
    def __init__(self):
        self.calls = 0
        self.closed = False
        self.boot_id = "ac13211e-481c-4b1e-a7dc-0bb3612565ae"

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        self.closed = True

    def capture_once(self, mode, gain, *, operator_ready):
        assert mode == "PULSED" and operator_ready is True
        self.calls += 1
        stamp = time.monotonic_ns()
        raw = HEADER.pack(b"UTC2", 2, self.calls, 0x204, 40, 4096, gain, 3, stamp) + bytes([128]) * 4096
        return RawCapture(raw, self.calls, 0x204, gain, mode, stamp)


class LiveServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.fake = FakeCapture()
        self.service = LiveAcquisition(self.directory.name, factory=lambda: self.fake)
        self.service.start_worker()
        self.addCleanup(self.service.close)
        with self.service.condition:
            self.assertTrue(self.service.condition.wait_for(lambda: self.service.available, 1))

    def payload(self, **changes):
        return {"owner": OWNER, "gain": 128, "probe_coupled": True, "safe_to_pulse": True, **changes}

    def test_boot_and_status_are_passive_until_explicit_operator_start(self):
        state = self.service.state()
        self.assertEqual(state["state"], "STOPPED")
        self.assertFalse(state["active"])
        self.assertFalse(state["fresh"])
        self.assertTrue(state["stop_settled"])
        self.assertEqual(self.fake.calls, 0)
        self.assertFalse((Path(self.directory.name) / "live.utc2").exists())

    def test_waveform_snapshot_is_read_only_consistent_and_never_uses_a_stopped_frame(self):
        with self.assertRaisesRegex(ValueError, "No fresh"):
            self.service.waveform_snapshot()
        self.assertEqual(self.fake.calls, 0)
        self.service.start(self.payload())
        self.service.capture("snapshot-fixture", threading.Event())
        with self.service.condition:
            calls, deadline = self.fake.calls, self.service.lease_deadline
            snapshot = self.service.waveform_snapshot()
            self.assertEqual(snapshot["samples"], list(self.service.latest.samples))
            self.assertEqual(snapshot["record"], self.service.latest.record())
            self.assertEqual(snapshot["frame_id"], self.service.latest_waveform.frame_id)
            self.assertEqual(self.fake.calls, calls)
            self.assertEqual(self.service.lease_deadline, deadline)
        self.service.stop()
        with self.assertRaisesRegex(ValueError, "No fresh"):
            self.service.waveform_snapshot()

    def test_start_rejects_missing_confirmations_unsafe_gain_and_invalid_owner(self):
        for change in ({"probe_coupled": False}, {"safe_to_pulse": False}, {"gain": 225},
                       {"gain": True}, {"owner": "pc"}, {"owner": "lcd-../unsafe"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.service.start(self.payload(**change))
        self.assertEqual(self.fake.calls, 0)

    def test_single_owner_and_expired_keepalive_cannot_arm_or_take_over(self):
        self.service.start(self.payload())
        with self.assertRaises(ValueError):
            self.service.start(self.payload(owner="lcd-" + "2" * 32))
        with self.assertRaises(ValueError):
            self.service.keepalive({"owner": "pc-" + "2" * 32})
        self.service.stop()
        with self.assertRaises(ValueError):
            self.service.keepalive({"owner": OWNER})
        self.assertFalse(self.service.state()["active"])

    def test_live_capture_is_shared_fresh_and_stops_without_fake_fallback(self):
        self.service.start(self.payload())
        waveform = self.service.capture("manual-map-point", threading.Event())
        self.assertEqual(waveform.source, "LIVE")
        self.assertEqual(len(waveform.samples), 4096)
        state = self.service.state()
        self.assertTrue(state["fresh"])
        self.assertEqual(state["record"]["source"], "FPGA_ADC_PULSED")
        self.assertFalse(state["diagnosis"]["calibrated"])
        self.assertEqual((Path(self.directory.name) / "live.utc2").read_bytes(), self.service.latest.raw)
        result = RESULT.unpack((Path(self.directory.name) / "live-result.bin").read_bytes())
        self.assertEqual(result[2], state["record"]["sequence"])
        self.assertEqual(result[3], 0)
        self.assertFalse(result[4] & 1)
        self.service.stop()
        self.assertFalse(self.service.state()["fresh"])
        self.assertFalse((Path(self.directory.name) / "live.utc2").exists())
        self.assertFalse((Path(self.directory.name) / "live-result.bin").exists())
        with self.assertRaises(ValueError):
            self.service.capture("cannot-reuse-old-frame", threading.Event())

    def test_operator_timeout_cannot_be_renewed_after_expiry(self):
        self.service.start(self.payload())
        with self.service.condition:
            self.service.lease_deadline = time.monotonic_ns() - 1
        with self.assertRaises(ValueError):
            self.service.keepalive({"owner": OWNER})
        self.assertFalse(self.service.state()["active"])

    def test_stopped_point_does_not_commit_a_waveform(self):
        self.service.start(self.payload())
        stop = threading.Event()
        stop.set()
        with self.assertRaisesRegex(ValueError, "stopped"):
            self.service.capture("stopped-point", stop)

    def test_stale_local_command_does_not_arm_transmission(self):
        path = Path(self.directory.name) / "live-command.json"
        path.write_text('{"action":"start","payload":{},"monotonic_ns":1}')
        time.sleep(0.12)
        self.assertEqual(self.fake.calls, 0)
        self.assertFalse(self.service.state()["active"])

    def test_local_screen_controls_capture_without_any_http_client(self):
        owner = "lcd-" + "2" * 32
        path = Path(self.directory.name) / "live-command.json"
        def send(action, payload):
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"action": action, "payload": payload, "monotonic_ns": time.monotonic_ns()}))
            temporary.replace(path)
        send("start", self.payload(owner=owner))
        with self.service.condition:
            self.assertTrue(self.service.condition.wait_for(lambda: self.service.latest is not None, 1))
        self.assertEqual(self.service.owner, owner)
        first = self.fake.calls
        for iteration in range(3):
            send("keepalive", {"owner": owner})
            time.sleep(0.15)
        self.assertGreater(self.fake.calls, first)
        self.assertTrue(self.service.state()["fresh"])
        send("stop", {})
        with self.service.condition:
            self.assertTrue(self.service.condition.wait_for(lambda: self.service.owner is None, 1))
        self.assertFalse(self.service.state()["active"])


if __name__ == "__main__":
    unittest.main()
