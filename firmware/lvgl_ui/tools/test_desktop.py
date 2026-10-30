from pathlib import Path
import shutil
import subprocess
from test_scan_desktop import create_fixture

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "desktop"
compiler = shutil.which("gcc")
flags = ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-finput-charset=UTF-8", "-DLV_CONF_INCLUDE_SIMPLE", "-DLV_LVGL_H_INCLUDE_SIMPLE", "-I" + str(ROOT), "-I" + str(ROOT / "src"), "-I" + str(ROOT / "vendor/lvgl")]
subprocess.run([compiler, *flags, str(ROOT / "tests/test_model.c"), str(ROOT / "src/ut_model.c"), "-lm", "-o", str(BUILD / "test_model.exe")], check=True)
subprocess.run([str(BUILD / "test_model.exe"), str(ROOT.parents[1] / "fpga/build/md1213_final/live.csv"), str(BUILD / "roundtrip.csv")], check=True)
subprocess.run([compiler, *flags, str(ROOT / "tests/test_scan.c"), str(ROOT / "src/ut_scan.c"), "-lm", "-o", str(BUILD / "test_scan.exe")], check=True)
scan_fixture = create_fixture(BUILD / "scan_model_fixture")
subprocess.run([str(BUILD / "test_scan.exe"), str(scan_fixture), str(BUILD / "scan_test.csv")], check=True)
subprocess.run([compiler, *flags, str(ROOT / "tests/test_map.c"), str(ROOT / "src/ut_map.c"), "-lm", "-o", str(BUILD / "test_map.exe")], check=True)
subprocess.run([str(BUILD / "test_map.exe")], check=True)
subprocess.run([compiler, *flags, str(ROOT / "tests/test_rgb.c"), str(ROOT / "src/ut_rgb.c"), "-o", str(BUILD / "test_rgb.exe")], check=True)
subprocess.run([str(BUILD / "test_rgb.exe")], check=True)
subprocess.run([compiler, *flags, str(ROOT / "tests/test_ui.c"), str(ROOT / "src/ut_model.c"), str(ROOT / "src/ut_scan.c"), str(ROOT / "src/ut_map.c"), str(ROOT / "src/ut_ui.c"), str(ROOT / "assets/ut_font.c"), str(BUILD / "liblvgl.a"), "-lm", "-o", str(BUILD / "test_ui.exe")], check=True)
subprocess.run([str(BUILD / "test_ui.exe")], check=True)
