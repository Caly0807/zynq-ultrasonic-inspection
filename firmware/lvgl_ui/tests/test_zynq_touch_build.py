import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
SPECIFICATION = importlib.util.spec_from_file_location("touch_builder", TOOLS / "build_zynq.py")
builder = importlib.util.module_from_spec(SPECIFICATION)
SPECIFICATION.loader.exec_module(builder)


class TouchArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.root = self.project / "firmware/lvgl_ui"
        self.build = self.root / "build/zynq_touch"
        names = ["build/zynq_touch/ut_lvgl.elf", "build/zynq_touch/md1213_ps_touch.bit", "build/zynq_touch/ps7_init.tcl",
                 "build/zynq_touch/artifacts.tcl", "src/ut_ui.c", "src/ut_map.c", "src/ut_board_demo.c", "src/ut_touch.c",
                 "src/ut_touch_input.c", "src/ut_i2c.c", "ports/zynq_touch.c", "ports/zynq_rgb_main.c", "ports/zynq_touch.h"]
        files = [self.root / name for name in names]
        for path in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(path.name, encoding="ascii")
        self.proof = {"platform": "touch", "xsa_sha256": builder.TOUCH_XSA_SHA256, "mode": "DEMO_HV_HARD_LOCKED_OFF",
                      "files": {str(path.relative_to(self.project)): builder.digest(path) for path in files}}
        self.save()
        self.addCleanup(patch.stopall)
        patch.object(builder, "PROJECT", self.project).start()
        patch.object(builder, "ROOT", self.root).start()
        patch.object(builder, "touch_platform").start()

    def save(self):
        (self.build / "artifacts.json").write_text(json.dumps(self.proof), encoding="utf-8")

    def test_matching_files_are_accepted(self):
        builder.verify_touch(self.build)

    def test_changed_elf_is_rejected(self):
        (self.build / "ut_lvgl.elf").write_text("wrong", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "Changed touch artifact"):
            builder.verify_touch(self.build)

    def test_changed_touch_input_filter_is_rejected(self):
        (self.root / "src/ut_touch_input.c").write_text("wrong", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "rebuild before downloading"):
            builder.verify_touch(self.build)

    def test_missing_driver_fingerprint_is_rejected(self):
        del self.proof["files"][str((self.root / "ports/zynq_touch.c").relative_to(self.project))]
        self.save()
        with self.assertRaisesRegex(ValueError, "Missing touch fingerprint"):
            builder.verify_touch(self.build)

    def test_non_hv_locked_mode_is_rejected(self):
        self.proof["mode"] = "LIVE"
        self.save()
        with self.assertRaisesRegex(ValueError, "HV-locked"):
            builder.verify_touch(self.build)

    def test_wrong_platform_is_rejected(self):
        self.proof["platform"] = "rgb"
        self.save()
        with self.assertRaisesRegex(ValueError, "HV-locked"):
            builder.verify_touch(self.build)

    def test_wrong_xsa_is_rejected(self):
        self.proof["xsa_sha256"] = "0" * 64
        self.save()
        with self.assertRaisesRegex(ValueError, "HV-locked"):
            builder.verify_touch(self.build)

    def test_outside_project_fingerprint_is_rejected(self):
        self.proof["files"]["../outside.txt"] = "0" * 64
        self.save()
        with self.assertRaisesRegex(ValueError, "Changed touch artifact"):
            builder.verify_touch(self.build)


class TouchLoaderGuardTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows PowerShell wrapper")
    def test_wrapper_without_physical_confirmation_exits_before_server_or_jtag(self):
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(TOOLS / "run_zynq_touch.ps1")],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Confirm physical HV OFF", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
