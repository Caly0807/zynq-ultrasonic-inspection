import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


TOOLS = Path(__file__).resolve().parents[1] / "tools"
SPECIFICATION = importlib.util.spec_from_file_location("build_zynq", TOOLS / "build_zynq.py")
builder = importlib.util.module_from_spec(SPECIFICATION)
SPECIFICATION.loader.exec_module(builder)


class RgbArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.build = self.project / "build"
        self.build.mkdir()
        self.source = self.project / "source.c"
        self.source.write_text("original", encoding="ascii")
        self.files = [self.source]
        for name in ("ut_lvgl.elf", "md1213_ps_rgb.bit", "ps7_init.tcl", "artifacts.tcl"):
            artifact = self.build / name
            artifact.write_text(name, encoding="ascii")
            self.files.append(artifact)
        self.proof = {"platform": "rgb", "xsa_sha256": builder.RGB_XSA_SHA256,
                      "files": {str(filename.relative_to(self.project)): builder.digest(filename) for filename in self.files}}
        self.save()
        self.addCleanup(patch.stopall)
        patch.object(builder, "PROJECT", self.project).start()
        patch.object(builder, "rgb_platform").start()

    def save(self):
        (self.build / "artifacts.json").write_text(json.dumps(self.proof), encoding="utf-8")

    def test_matching_files_are_accepted(self):
        builder.verify_rgb(self.build)

    def test_changed_elf_is_rejected(self):
        (self.build / "ut_lvgl.elf").write_text("wrong ELF", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "Changed RGB artifact"):
            builder.verify_rgb(self.build)

    def test_changed_source_is_rejected(self):
        self.source.write_text("changed", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "rebuild before downloading"):
            builder.verify_rgb(self.build)

    def test_wrong_platform_is_rejected(self):
        self.proof["platform"] = "legacy"
        self.save()
        with self.assertRaisesRegex(ValueError, "Not a verified RGB build"):
            builder.verify_rgb(self.build)

    def test_wrong_xsa_is_rejected(self):
        self.proof["xsa_sha256"] = "0" * 64
        self.save()
        with self.assertRaisesRegex(ValueError, "Not a verified RGB build"):
            builder.verify_rgb(self.build)

    def test_missing_fingerprint_is_rejected(self):
        del self.proof["files"][str((self.build / "artifacts.tcl").relative_to(self.project))]
        self.save()
        with self.assertRaisesRegex(ValueError, "Missing artifact fingerprint"):
            builder.verify_rgb(self.build)


if __name__ == "__main__":
    unittest.main()
