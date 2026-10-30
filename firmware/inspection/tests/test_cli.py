import importlib
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch


cli = importlib.import_module("utinspect.__main__")


@unittest.skipUnless(os.name == "nt", "The PC command wrapper is Windows-only")
class WindowsPcEntryTests(unittest.TestCase):
    def run_entry(self, *arguments):
        root = Path(__file__).resolve().parents[1]
        return subprocess.run([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(root / "run_pc.cmd"),
                               *arguments], cwd=root.parent, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30)

    def test_wrapper_resolves_the_base_interpreter_and_displays_help(self):
        result = self.run_entry("--help")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PC_PYTHON=", result.stdout)
        self.assertIn("workpiece", result.stdout)

    def test_wrapper_preserves_argument_errors(self):
        result = self.run_entry("not-a-command")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("invalid choice", result.stderr)


@unittest.skipUnless(os.name == "nt", "The bundled desktop LVGL executable is Windows-only")
class DesktopLaunchTests(unittest.TestCase):
    def test_scan_summary_selects_the_scan_review_without_launching_a_process(self):
        summary = Path("test_only_not_hardware/summary.csv")
        with patch.object(Path, "is_file", return_value=True), patch.object(cli.subprocess, "Popen") as launch:
            cli.show_lvgl(summary)
        arguments = launch.call_args.args[0]
        self.assertEqual(arguments[1:], ["--scan", str(summary.resolve())])

    def test_single_waveform_keeps_the_existing_csv_entry(self):
        waveform = Path("test_only_not_hardware/latest.csv")
        with patch.object(Path, "is_file", return_value=True), patch.object(cli.subprocess, "Popen") as launch:
            cli.show_lvgl(waveform)
        self.assertEqual(launch.call_args.args[0][1:], ["--csv", str(waveform.resolve())])

    def test_missing_desktop_executable_is_rejected(self):
        with patch.object(Path, "is_file", return_value=False), patch.object(cli.subprocess, "Popen") as launch:
            with self.assertRaises(ValueError):
                cli.show_lvgl(Path("test_only_not_hardware/summary.csv"))
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
