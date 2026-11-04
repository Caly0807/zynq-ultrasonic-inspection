import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "petalinux"))
sys.path.insert(0, str(ROOT / "firmware/inspection"))
from capture_acceptance_batch import FRAME_NOT_READY, ReadOnlyBoardClient, record_batch, verified_frame, waveform_csv
from utinspect.echo import load_csv


def frame(sequence=1, status="UNCALIBRATED"):
    samples = [128] * 4096
    return {"schema": "ut-live-waveform/1", "frame_id": f"adc:11111111-2222-3333-4444-555555555555:{sequence}",
            "trigger_index": 0, "sample_period_us": .04, "samples": samples,
            "diagnosis": {"status": status, "reason": "synthetic_test_only"},
            "record": {"schema": "ut-raw-capture/2", "source": "FPGA_ADC_PULSED", "mode": "PULSED",
                       "sequence": sequence, "sample_count": 4096, "sample_period_ns": 40, "gain_code": 128,
                       "monotonic_ns": sequence * 1000000, "minimum": 128, "maximum": 128,
                       "sample_sha256": hashlib.sha256(bytes(samples)).hexdigest(), "overrange": False,
                       "transmit_enabled_after_capture": False, "motion_enabled": False}}


def labels():
    return {"record_role": "HOLE_TARGET", "specimen_id": "SYNTHETIC", "placement_id": "p1", "hole_id": "d8",
            "nominal_reflector_depth_from_probe_face_mm": 8., "independent_recoupling_confirmed": False}


class Clock:
    def __init__(self):
        self.value = 0.

    def now(self):
        self.value += .01
        return self.value

    def sleep(self, duration):
        self.value += duration


class FakeClient:
    def __init__(self, frames, active=True, final_session="synthetic-session"):
        self.frames, self.active, self.calls = iter(frames), active, []
        self.device_reads, self.final_session = 0, final_session

    def read_json(self, route):
        self.calls.append(route)
        if route == "/api/device":
            self.device_reads += 1
            return {"runtime": "ARM_LINUX", "machine": "armv7l",
                    "service_session": "synthetic-session" if self.device_reads == 1 else self.final_session,
                    "real_capture": {"active": self.active, "state": "RUNNING" if self.active else "STOPPED"}}
        if route != "/api/live/waveform":
            raise AssertionError("Unexpected API operation")
        payload = next(self.frames)
        if isinstance(payload, Exception):
            raise payload
        return payload


class AcceptanceBatchTests(unittest.TestCase):
    def record(self, client, output, count=2):
        clock = Clock()
        return record_batch(client, output, labels(), frame_count=count, clock=clock.now, sleeper=clock.sleep)

    def test_recorder_never_starts_stopped_acquisition(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "stopped"
            client = FakeClient([], active=False)
            with self.assertRaisesRegex(ValueError, "never starts"):
                self.record(client, output)
            self.assertFalse(output.exists())
            self.assertEqual(client.calls, ["/api/device"])

    def test_invalid_quality_is_saved_not_cherry_picked(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "batch"
            result = self.record(FakeClient([frame(1, "INVALID"), frame(2)]), output)
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["diagnoses"], {"INVALID": 1, "UNCALIBRATED": 1})
            metadata = json.loads((output / "acquisition.json").read_text(encoding="utf-8"))
            self.assertFalse(metadata["known_labels_used_for_prediction"])
            self.assertFalse(metadata["raw_binary_header_hash_verified"])
            self.assertIn("NOT_INDEPENDENT", metadata["repeat_protocol"])
            for entry in metadata["frames"]:
                data = (output / entry["file"]).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
                waveform = load_csv(output / entry["file"])
                self.assertEqual(len(waveform.samples), 4096)
                self.assertEqual(waveform.gain, 128)
                self.assertEqual(waveform.source, "RECORDED")

    def test_repeated_frames_do_not_count_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.record(FakeClient([frame(1), frame(1), frame(2)]), Path(directory) / "batch")
            self.assertEqual(result["frames_saved"], 2)

    def test_fresh_frame_gap_retries_only_read_operations(self):
        error = HTTPError("http://192.168.77.2:8766/api/live/waveform", 400, "Bad Request", {},
                          io.BytesIO(json.dumps({"error": FRAME_NOT_READY}).encode()))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "batch"
            client = FakeClient([frame(1), error, frame(1), frame(2)])
            result = self.record(client, output)
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["transient_read_retries"], 1)
            self.assertEqual(result["frames_saved"], 2)
            self.assertTrue(set(client.calls) <= {"/api/device", "/api/live/waveform"})
            metadata = json.loads((output / "acquisition.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["http_read_errors"], [{"status_code": 400, "reason": FRAME_NOT_READY}])

    def test_unknown_and_authorization_errors_are_not_retried(self):
        for status, body in ((400, b'{"error":"unknown failure"}'), (400, b'not json'),
                             (400, b'x' * 4097), (401, b'{}'), (403, b'{}'), (500, b'{}')):
            with self.subTest(status=status, length=len(body)), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "batch"
                error = HTTPError("http://192.168.77.2:8766/api/live/waveform", status, "Failure", {}, io.BytesIO(body))
                client = FakeClient([frame(1), error, frame(2)])
                with self.assertRaises(HTTPError):
                    self.record(client, output)
                metadata = json.loads((output / "acquisition.json").read_text(encoding="utf-8"))
                self.assertEqual(metadata["status"], "ABORTED")
                self.assertEqual(len(metadata["frames"]), 1)
                self.assertEqual(metadata["transient_read_retries"], 0)
                self.assertEqual(metadata["http_read_errors"][0]["status_code"], status)
                self.assertEqual(client.device_reads, 1)

    def test_restart_during_fresh_frame_gap_aborts(self):
        error = HTTPError("http://192.168.77.2:8766/api/live/waveform", 400, "Bad Request", {},
                          io.BytesIO(json.dumps({"error": FRAME_NOT_READY}).encode()))
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient([frame(1), error], final_session="restarted")
            with self.assertRaisesRegex(ValueError, "restarted"):
                self.record(client, Path(directory) / "batch")

    def test_stopped_capture_is_not_rearmed_after_fresh_frame_gap(self):
        class StoppingClient(FakeClient):
            def read_json(self, route):
                try:
                    return super().read_json(route)
                except HTTPError:
                    self.active = False
                    raise

        error = HTTPError("http://192.168.77.2:8766/api/live/waveform", 400, "Bad Request", {},
                          io.BytesIO(json.dumps({"error": FRAME_NOT_READY}).encode()))
        with tempfile.TemporaryDirectory() as directory:
            client = StoppingClient([frame(1), error])
            with self.assertRaisesRegex(ValueError, "stopped"):
                self.record(client, Path(directory) / "batch")
            self.assertTrue(set(client.calls) <= {"/api/device", "/api/live/waveform"})

    def test_persistent_fresh_frame_gap_obeys_deadline(self):
        class WaitingClient(FakeClient):
            def read_json(self, route):
                if route == "/api/live/waveform":
                    self.calls.append(route)
                    raise HTTPError("http://192.168.77.2:8766/api/live/waveform", 400, "Bad Request", {},
                                    io.BytesIO(json.dumps({"error": FRAME_NOT_READY}).encode()))
                return super().read_json(route)

        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            client = WaitingClient([])
            result = record_batch(client, Path(directory) / "batch", labels(), frame_count=2,
                                  duration=1., clock=clock.now, sleeper=clock.sleep)
            self.assertEqual(result["status"], "INCOMPLETE")
            self.assertEqual(result["frames_saved"], 0)
            self.assertGreater(result["transient_read_retries"], 0)
            self.assertLess(clock.value, 2.)

    def test_gain_change_aborts_and_preserves_first_frame(self):
        changed = frame(2)
        changed["record"]["gain_code"] = 129
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "batch"
            with self.assertRaisesRegex(ValueError, "gain"):
                self.record(FakeClient([frame(1), changed]), output)
            metadata = json.loads((output / "acquisition.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "ABORTED")
            self.assertEqual(len(metadata["frames"]), 1)
            self.assertTrue((output / "frame_001.csv").exists())

    def test_service_restart_invalidates_batch_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "restarted"):
                self.record(FakeClient([frame(1)], final_session="new-service"), Path(directory) / "batch", 1)

    def test_test_passive_corrupt_and_noninteger_frames_are_rejected(self):
        cases = []
        for field, value in (("mode", "TEST"), ("mode", "PASSIVE"), ("gain_code", True), ("sample_sha256", "0" * 64)):
            payload = frame()
            payload["record"][field] = value
            cases.append(payload)
        payload = frame()
        payload["samples"][0] = True
        cases.append(payload)
        payload = frame()
        payload["frame_id"] = None
        cases.append(payload)
        for payload in cases:
            with self.subTest(payload=payload["record"].get("mode")), self.assertRaises(ValueError):
                verified_frame(payload)

    def test_nominal_hole_depth_does_not_change_waveform_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            original_labels = labels()
            changed_labels = {**original_labels, "nominal_reflector_depth_from_probe_face_mm": 22.}
            for name, recording_labels in (("d8", original_labels), ("d22", changed_labels)):
                clock = Clock()
                record_batch(FakeClient([frame(1)]), Path(directory) / name, recording_labels,
                             frame_count=1, clock=clock.now, sleeper=clock.sleep)
            first = Path(directory) / "d8"
            second = Path(directory) / "d22"
            self.assertEqual((first / "frame_001.csv").read_bytes(), (second / "frame_001.csv").read_bytes())
            before = json.loads((first / "acquisition.json").read_text(encoding="utf-8"))
            after = json.loads((second / "acquisition.json").read_text(encoding="utf-8"))
            self.assertNotEqual(before["nominal_reflector_depth_from_probe_face_mm"], after["nominal_reflector_depth_from_probe_face_mm"])
            self.assertEqual(before["frames"][0]["diagnosis"], after["frames"][0]["diagnosis"])

    def test_duplicate_identity_with_changed_content_is_rejected(self):
        changed = copy.deepcopy(frame(1))
        changed["record"]["monotonic_ns"] += 1
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "different content"):
                self.record(FakeClient([frame(1), changed]), Path(directory) / "batch")

    def test_client_forbids_control_routes_before_network(self):
        client = object.__new__(ReadOnlyBoardClient)
        for route, payload in (("/api/live/start", None), ("/api/live/waveform", {}), ("/api/live/keepalive", {})):
            with self.subTest(route=route), self.assertRaises(PermissionError):
                client.request(route, payload)


if __name__ == "__main__":
    unittest.main()
