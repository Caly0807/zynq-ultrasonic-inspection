import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utinspect import board_capture
from utinspect.board_health import CAMERA_COUNTERS, TOUCH_COUNTERS, TOUCH_DETAIL_COUNTERS, camera_diagnostics, lcd_diagnostics, process_alive, service_health
from utinspect.mapping import MapSession, synthetic_map_waveform


class LinuxTransportTests(unittest.TestCase):
    def frame(self, **changes):
        values = {"magic": b"UTC1", "version": 1, "sequence": 7, "flags": 0x304,
                  "period": 40, "count": 4096, "timestamp": 123456789}
        values.update(changes)
        return board_capture.HEADER.pack(*values.values()) + bytes(range(256)) * 16

    def test_c_and_python_header_sizes_match_reviewed_abi(self):
        self.assertEqual(board_capture.HEADER.size, 32)
        self.assertEqual(board_capture.STATUS.size, 32)
        self.assertEqual(board_capture.FRAME_BYTES, 4128)

    def test_test_frame_cannot_be_reported_as_live_measurement(self):
        record = board_capture.decode_test_frame(self.frame())
        self.assertEqual(record["source"], "FPGA_DMA_TEST_NOT_ULTRASOUND")
        self.assertFalse(record["measurement_authorized"])
        self.assertFalse(record["transmit_enabled"])

    def test_truncated_or_appended_frame_is_rejected(self):
        for data in (self.frame()[:-1], self.frame() + b"extra"):
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                board_capture.decode_test_frame(data)

    def test_unreviewed_header_or_unsafe_status_is_rejected(self):
        changes = ({"magic": b"BAD!"}, {"version": 2}, {"period": 80}, {"count": 4095}, {"timestamp": 0},
                   {"flags": 4}, {"flags": 0x305}, {"flags": 0x30C}, {"flags": 0x344}, {"flags": 0x384})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                board_capture.decode_test_frame(self.frame(**change))

    def test_corrupt_sample_is_rejected(self):
        data = bytearray(self.frame())
        data[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "FPGA ramp"):
            board_capture.decode_test_frame(data)

    def test_status_validates_geometry_and_never_claims_board_acceptance(self):
        record = board_capture.decode_status(struct.pack("<8I", 0x314C5455, 1, 800, 480, 2400, 1, 0x200, 3))
        self.assertFalse(record["live_capture_authorized"])
        self.assertFalse(record["board_boot_verified_by_this_status"])
        with self.assertRaises(ValueError):
            board_capture.decode_status(struct.pack("<8I", 0x314C5455, 1, 800, 480, 3200, 1, 0, 0))

    def test_windows_and_unconfirmed_selftest_never_open_devices(self):
        with mock.patch.object(board_capture.sys, "platform", "win32"), mock.patch.object(board_capture.os, "open") as opened:
            with self.assertRaises(ValueError):
                board_capture.inspect_transport()
            opened.assert_not_called()
        with mock.patch.object(board_capture, "inspect_transport") as inspected, self.assertRaises(ValueError):
            board_capture.main(["--selftest"])
        inspected.assert_not_called()


class BoardHealthTests(unittest.TestCase):
    def test_touch_details_keep_short_presses_and_reject_partial_or_untyped_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            touch = dict.fromkeys((*TOUCH_COUNTERS, *TOUCH_DETAIL_COUNTERS), 0)
            touch.update(bus_open=True, pressed=False, input_armed=True, last_packet=[0] * 7,
                         raw_press_samples=3, press_events=1, release_events=1,
                         last_press_x=410, last_press_y=220, last_press_packet=[0, 0, 1, 129, 154, 0, 220], secret="not allowed")
            record = dict(schema="ut-lcd-status/1", mode="LCD_TRANSPORT", updated_monotonic_ns=0,
                          flushes=25, map_points=0, waveform_source=0, touch=touch)
            path.write_text(json.dumps(record))
            result = lcd_diagnostics(path)
            self.assertTrue(result["available"])
            self.assertEqual(result["touch"]["last_press_x"], 410)
            self.assertEqual(result["touch"]["raw_press_samples"], 3)
            self.assertNotIn("secret", result["touch"])
            for field, value in (("input_armed", 1), ("last_press_ms", True), ("raw_press_samples", -1),
                                 ("last_press_packet", [False] * 7), ("last_press_packet", [0] * 6)):
                invalid = {**record, "touch": {**touch, field: value}}
                path.write_text(json.dumps(invalid))
                self.assertFalse(lcd_diagnostics(path)["available"])
            del touch["input_armed"]
            path.write_text(json.dumps(record))
            self.assertFalse(lcd_diagnostics(path)["available"])

    def test_lcd_touch_diagnostics_preserve_raw_identity_without_claiming_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            touch = dict.fromkeys(TOUCH_COUNTERS, 0)
            touch.update(bus_open=True, pressed=False, last_packet=[255] * 7)
            record = dict(schema="ut-lcd-status/1", mode="LCD_TRANSPORT", updated_monotonic_ns=0,
                          flushes=25, map_points=0, waveform_source=0, touch=touch, secret="never expose")
            path.write_text(json.dumps(record))
            result = lcd_diagnostics(path)
            self.assertTrue(result["available"])
            self.assertEqual(result["touch"]["last_packet"], [255] * 7)
            self.assertNotIn("secret", result)
            self.assertNotIn("physical_touch_verified", result)
            touch["last_packet"] = [255] * 8
            path.write_text(json.dumps(record))
            self.assertFalse(lcd_diagnostics(path)["available"])
            touch["last_packet"] = [False] * 7
            path.write_text(json.dumps(record))
            self.assertFalse(lcd_diagnostics(path)["available"])

    def test_camera_diagnostics_expose_only_fixed_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            record = dict.fromkeys(CAMERA_COUNTERS, 0)
            record.update(schema="ut-uvc-status/1", state="FAILED", stage="STREAM_ON", errno=28,
                          supports_yuyv=True, supports_mjpeg=False, secret="private-credential-never-exposed")
            path.write_text(json.dumps(record))
            result = camera_diagnostics(path)
            self.assertTrue(result["available"])
            self.assertEqual((result["stage"], result["errno"]), ("STREAM_ON", 28))
            self.assertNotIn("secret", result)
            self.assertNotIn("private-credential", json.dumps(result))
            self.assertEqual(result["basis"], "HELPER_STATE_NOT_PHYSICAL_ACCEPTANCE")
            for changes in ({"state": "secret"}, {"stage": "untrusted text"}, {"errno": True},
                            {"width": -1}, {"sequence": 2 ** 64}, {"supports_yuyv": 1}):
                with self.subTest(changes=changes):
                    path.write_text(json.dumps(dict(record, **changes)))
                    self.assertFalse(camera_diagnostics(path)["available"])

    def test_missing_malformed_or_oversized_camera_status_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            self.assertFalse(camera_diagnostics(path)["available"])
            for text in ("{", "[]", "{}", " " * 4097):
                path.write_text(text)
                self.assertFalse(camera_diagnostics(path)["available"])

    def test_recycled_pid_is_not_accepted_as_the_display_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "123").mkdir()
            (root / "display.pid").write_text("123")
            (root / "123/cmdline").write_bytes(b"/usr/bin/unrelated\0")
            self.assertFalse(process_alive(root / "display.pid", "ut_lvgl_linux", root))
            (root / "123/cmdline").write_bytes(b"/usr/bin/ut_lvgl_linux\0--map\0/run/map.csv\0")
            self.assertTrue(process_alive(root / "display.pid", "ut_lvgl_linux", root))

    def test_python_module_pid_is_validated_without_disclosing_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "123").mkdir()
            (root / "vision.pid").write_text("123")
            (root / "123/cmdline").write_bytes(b"/usr/bin/python3\0-m\0utinspect.device_vision\0")
            self.assertTrue(process_alive(root / "vision.pid", "utinspect.device_vision", root))
            self.assertFalse(process_alive(root / "vision.pid", "utinspect.other", root))

    def test_health_files_never_claim_physical_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "lcd").mkdir()
            (root / "lcd/map.csv").write_text("fixture")
            record = service_health(root)
            self.assertTrue(record["lcd_bridge_map_present"])
            self.assertFalse(record["lcd_bridge_waveform_present"])
            self.assertFalse(record["physical_touch_verified"])
            self.assertFalse(record["real_capture_verified"])


class LocalDisplayBridgeTests(unittest.TestCase):
    def test_board_session_publishes_map_and_source_preserving_waveform(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = MapSession(root / "session", mode="DEMO")
            session.bind_local_display(root / "lcd")
            self.assertTrue((root / "lcd/map.csv").is_file())
            self.assertFalse((root / "lcd/waveform.csv").exists())
            point = session.add(synthetic_map_waveform(5, 5, session.spec), 5, 5, True)
            self.assertEqual((root / "lcd/waveform.csv").read_bytes(), (session.root / point["waveform"]).read_bytes())
            self.assertIn(",DEMO,", (root / "lcd/map.csv").read_text())
            self.assertEqual(point["source"], "DEMO")

    def test_new_session_removes_previous_waveform(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "lcd").mkdir()
            (root / "lcd/waveform.csv").write_text("stale")
            session = MapSession(root / "session", mode="RECORDED")
            session.bind_local_display(root / "lcd")
            self.assertFalse((root / "lcd/waveform.csv").exists())
            self.assertIn(",RECORDED,", (root / "lcd/map.csv").read_text())

    def test_failed_lcd_copy_does_not_undo_authoritative_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            session = MapSession(root / "session", mode="DEMO")
            session.bind_local_display(root / "lcd")
            replace = Path.replace
            def fail_display(path, destination):
                if path.parent == root / "lcd":
                    raise OSError("display write failed")
                return replace(path, destination)
            with mock.patch.object(Path, "replace", fail_display):
                session.add(synthetic_map_waveform(5, 5, session.spec), 5, 5, True)
            self.assertEqual(len(session.points), 1)
            self.assertEqual(len(json.loads((session.root / "session.json").read_text())["points"]), 1)
            self.assertIn("LCD publication failed", session.bridge_error)


if __name__ == "__main__":
    unittest.main()
