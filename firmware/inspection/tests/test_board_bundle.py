import hashlib
import importlib.util
import json
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SPECIFICATION = importlib.util.spec_from_file_location("package_board_service", ROOT / "petalinux/package_board_service.py")
BUNDLER = importlib.util.module_from_spec(SPECIFICATION)
SPECIFICATION.loader.exec_module(BUNDLER)


class BoardBundleTests(unittest.TestCase):
    def test_bundle_is_reproducible_and_every_payload_is_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "first.tar.gz", Path(directory) / "second.tar.gz"
            report = BUNDLER.build_bundle(first, with_vision=True)
            BUNDLER.build_bundle(second, with_vision=True)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(), report["sha256"])
            with tarfile.open(first) as archive:
                manifest = json.load(archive.extractfile("manifest.json"))
                for name, details in manifest["files"].items():
                    payload = archive.extractfile(name).read()
                    self.assertEqual(hashlib.sha256(payload).hexdigest(), details["sha256"])
                    self.assertEqual(len(payload), details["bytes"])

    def test_minimal_bundle_has_no_camera_dependencies_or_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "core.tar.gz"
            report = BUNDLER.build_bundle(path)
            self.assertFalse(report["with_vision"])
            self.assertFalse(report["board_execution_verified"])
            with tarfile.open(path) as archive:
                names = archive.getnames()
                self.assertIn("utinspect/device_app.py", names)
                self.assertIn("utinspect/basic_vision.py", names)
                self.assertIn("web/camera_basic.html", names)
                self.assertNotIn("utinspect/vision.py", names)
                self.assertNotIn("utinspect/camera.py", names)
                for name in names:
                    self.assertNotIn("..", Path(name).parts)
                    self.assertFalse(Path(name).is_absolute())
                    self.assertFalse(any(value in name for value in (".venv", ".token", "runs/", "__pycache__", ".exe")))

    def test_optional_vision_contains_existing_detectors_and_network_entrypoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vision.tar.gz"
            report = BUNDLER.build_bundle(path, with_vision=True)
            self.assertEqual(report["default_source"], "RECORDED")
            self.assertFalse(report["hardware_control"])
            with tarfile.open(path) as archive:
                for name in ("utinspect/device_vision.py", "utinspect/workpiece.py", "utinspect/camera.py", "web/workpiece.html"):
                    self.assertIn(name, archive.getnames())

    def test_live_bundle_is_explicit_never_auto_arms_and_contains_both_display_transports(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "live.tar.gz"
            report = BUNDLER.build_bundle(path, with_live=True)
            self.assertTrue(report["with_live"])
            self.assertTrue(report["hardware_control"])
            self.assertFalse(report["automatic_transmit_on_boot"])
            self.assertFalse(report["board_execution_verified"])
            with tarfile.open(path) as archive:
                for name in ("utinspect/live_capture.py", "utinspect/live_service.py", "web/realtime.html"):
                    self.assertIn(name, archive.getnames())


if __name__ == "__main__":
    unittest.main()
