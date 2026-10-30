import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from utinspect.board_health import camera_diagnostics


ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(os.name == "posix" and shutil.which("gcc"), "Native V4L2 fixture runs in Linux/WSL with gcc")
class NativeCameraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.helper = cls.root / "ut_uvc_capture"
        cls.fixture = cls.root / "uvc_fixture.so"
        subprocess.run(["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                        str(ROOT / "firmware/lvgl_ui/ports/ut_uvc_capture.c"), "-o", str(cls.helper)], check=True)
        subprocess.run(["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                        str(Path(__file__).parent / "native/uvc_fixture.c"), "-ldl", "-o", str(cls.fixture)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_camera(self, scenario=None, device="/dev/ut-uvc-fixture"):
        directory = self.root / self._testMethodName
        directory.mkdir()
        environment = dict(os.environ)
        if scenario is not None:
            environment.update(LD_PRELOAD=str(self.fixture), UT_UVC_FIXTURE=scenario)
        result = subprocess.run([str(self.helper), device, str(directory)], env=environment,
                                capture_output=True, text=True, timeout=5)
        status = json.loads((directory / "status.json").read_text())
        self.assertTrue(camera_diagnostics(directory / "status.json")["available"])
        return result, status, directory

    def test_open_error_is_retained_without_a_fake_frame(self):
        result, status, directory = self.run_camera(device="/dev/utinspect-definitely-absent-fixture")
        self.assertEqual(result.returncode, 1)
        self.assertEqual((status["state"], status["stage"], status["errno"]), ("FAILED", "OPEN", 2))
        self.assertFalse((directory / "frame.ppm").exists())

    def test_unsupported_device_has_a_distinct_ioctl_stage(self):
        result, status, directory = self.run_camera(device="/dev/null")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(status["stage"], "QUERY_CAPABILITIES")
        self.assertGreater(status["errno"], 0)
        self.assertFalse((directory / "frame.ppm").exists())

    def test_recoverable_error_and_short_frame_are_requeued_then_real_payload_published(self):
        result, status, directory = self.run_camera("recover")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status["state"], "STOPPED")
        self.assertEqual(status["dropped_frames"], 2)
        self.assertEqual(status["published_frames"], 1)
        self.assertEqual(status["sequence"], 3)
        self.assertTrue((directory / "frame.ppm").read_bytes().startswith(b"P6\n# UTCAM1 3 "))

    def test_repeated_bad_frames_have_a_bounded_failure(self):
        result, status, directory = self.run_camera("badframes")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(status["stage"], "BAD_FRAME_LIMIT")
        self.assertEqual(status["dropped_frames"], 20)
        self.assertEqual(status["published_frames"], 0)
        self.assertFalse((directory / "frame.ppm").exists())

    def test_invalid_buffer_index_is_never_dereferenced(self):
        result, status, directory = self.run_camera("bounds")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(status["stage"], "BUFFER_BOUNDS")
        self.assertFalse((directory / "frame.ppm").exists())

    def test_mjpeg_is_reported_not_misread_as_yuyv(self):
        result, status, directory = self.run_camera("mjpeg")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(status["stage"], "ACCEPT_FORMAT")
        self.assertTrue(status["supports_mjpeg"])
        self.assertEqual(status["pixel_format"], int.from_bytes(b"MJPG", "little"))
        self.assertFalse((directory / "frame.ppm").exists())


if __name__ == "__main__":
    unittest.main()
