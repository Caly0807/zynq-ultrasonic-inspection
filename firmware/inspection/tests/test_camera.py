import io
import queue
import subprocess
import threading
import time
import unittest
from unittest.mock import Mock

import numpy as np

from utinspect.camera import Camera, read_exact


class ShortReadStream(io.BytesIO):
    def read(self, size=-1):
        return super().read(min(size, 3))


class ExactReadTests(unittest.TestCase):
    def test_short_reads_are_combined(self):
        self.assertEqual(read_exact(ShortReadStream(b"123456789"), 9), b"123456789")

    def test_incomplete_frame_is_not_returned(self):
        self.assertIsNone(read_exact(ShortReadStream(b"12345"), 6))

    def test_eof_returns_none(self):
        self.assertIsNone(read_exact(io.BytesIO(), 3))


class NamedCameraTests(unittest.TestCase):
    def camera(self, stderr=b"", return_code=1):
        camera = Camera.__new__(Camera)
        camera.width, camera.height = 1280, 720
        camera.timestamp_ns = 0
        camera._device_name = "PC Camera"
        camera._ffmpeg_executable = "test-only-ffmpeg.exe"
        camera._reader_error = None
        camera._capture = camera._thread = None
        camera._stopped = threading.Event()
        camera._frames = queue.Queue(maxsize=2)
        camera._log = io.BytesIO(stderr)
        camera._process = Mock()
        camera._process.poll.return_value = return_code
        camera._process.stdout = io.BytesIO()
        self.addCleanup(camera.release)
        return camera

    def test_stream_end_reports_the_real_ffmpeg_error(self):
        camera = self.camera(b"[dshow] Unable to BindToObject for PC Camera\r\r\n")
        camera._frames.put((0, None))
        with self.assertRaises(OSError) as raised:
            camera.read()
        message = str(raised.exception)
        for expected in ("Named camera stream ended", "PC Camera", "1280x720 @ 10 fps",
                         "test-only-ffmpeg.exe", "exit code: 1", "Unable to BindToObject"):
            self.assertIn(expected, message)

    def test_stream_end_waits_briefly_for_the_exit_code(self):
        camera = self.camera(return_code=None)
        process = camera._process
        process.wait.return_value = 3
        camera._frames.put((0, None))
        with self.assertRaisesRegex(OSError, "exit code: 3"):
            camera.read()
        process.wait.assert_called_once_with(timeout=0.5)

    def test_stream_end_does_not_wait_indefinitely(self):
        camera = self.camera(return_code=None)
        camera._process.wait.side_effect = subprocess.TimeoutExpired("test-only-ffmpeg.exe", 0.5)
        camera._frames.put((0, None))
        with self.assertRaisesRegex(OSError, "exit code: running"):
            camera.read()
        camera._process.wait.side_effect = None

    def test_timeout_includes_stderr_without_waiting_for_exit(self):
        camera = self.camera(b"Requested format cannot be set", return_code=None)
        with self.assertRaisesRegex(OSError, "Requested format cannot be set"):
            camera.read(timeout=0)
        camera._process.wait.assert_not_called()

    def test_empty_stderr_and_reader_error_are_reported(self):
        camera = self.camera()
        camera._reader_error = "test-only broken pipe"
        camera._frames.put((0, None))
        with self.assertRaises(OSError) as raised:
            camera.read()
        self.assertIn("FFmpeg stderr:\n(empty)", str(raised.exception))
        self.assertIn("Frame reader: test-only broken pipe", str(raised.exception))

    def test_error_output_keeps_only_a_bounded_log_tail(self):
        camera = self.camera(b"discarded-prefix" + b"z" * 9000 + b"\nlast camera error")
        camera._frames.put((0, None))
        with self.assertRaises(OSError) as raised:
            camera.read()
        message = str(raised.exception)
        self.assertNotIn("discarded-prefix", message)
        self.assertIn("last camera error", message)
        self.assertLess(len(message), 9000)

    def test_fresh_frame_and_timestamp_are_preserved(self):
        camera = self.camera()
        image = np.zeros((2, 2, 3), np.uint8)
        timestamp_ns = time.monotonic_ns()
        camera._frames.put((timestamp_ns, image))
        success, result = camera.read()
        self.assertTrue(success)
        self.assertIs(result, image)
        self.assertEqual(camera.timestamp_ns, timestamp_ns)

    def test_release_cleans_up_the_process_and_logs_idempotently(self):
        camera = self.camera(return_code=None)
        process, log, stream = camera._process, camera._log, camera._process.stdout
        camera.release()
        camera.release()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=2)
        process.kill.assert_not_called()
        self.assertTrue(log.closed)
        self.assertTrue(stream.closed)
        with self.assertRaisesRegex(OSError, "Camera was closed"):
            camera.read()

    def test_closing_while_waiting_does_not_access_the_released_process(self):
        camera = self.camera()
        def close_before_delivery(timeout):
            camera.release()
            return 0, None
        camera._frames = Mock()
        camera._frames.get.side_effect = close_before_delivery
        with self.assertRaisesRegex(OSError, "Camera was closed"):
            camera.read()


if __name__ == "__main__":
    unittest.main()
