from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build/desktop"


def main():
    compiler = shutil.which("gcc")
    if not compiler:
        raise SystemExit("MinGW gcc must be on PATH")
    flags = ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-finput-charset=UTF-8", "-DLV_CONF_INCLUDE_SIMPLE",
             "-DLV_LVGL_H_INCLUDE_SIMPLE", "-I" + str(ROOT), "-I" + str(ROOT / "src"), "-I" + str(ROOT / "vendor/lvgl")]
    cases = {
        "i2c": ["src/ut_i2c.c"],
        "touch": ["src/ut_touch.c", "src/ut_touch_input.c"],
        "touch_ui": ["src/ut_touch.c", "src/ut_touch_input.c", "src/ut_model.c", "src/ut_map.c", "src/ut_scan.c",
                     "src/ut_board_demo.c", "src/ut_ui.c", "assets/ut_font.c", "build/desktop/liblvgl.a"],
        "zynq_touch_port": ["ports/zynq_touch.c", "src/ut_touch.c", "src/ut_touch_input.c", "build/desktop/liblvgl.a"],
    }
    for name, sources in cases.items():
        executable = BUILD / ("test_" + name + ".exe")
        extra = ["-I" + str(ROOT / "tests/mocks/zynq_touch"), "-I" + str(ROOT / "ports")] if name == "zynq_touch_port" else []
        subprocess.run([compiler, *flags, *extra, str(ROOT / f"tests/test_{name}.c"), *[str(ROOT / source) for source in sources], "-lm", "-o", str(executable)], check=True, timeout=120)
        subprocess.run([str(executable)], check=True, timeout=30)
    print("TOUCH_OFFLINE_SUITE_PASSED / NO_GPIO_OR_JTAG_ACCESS")


if __name__ == "__main__":
    main()
