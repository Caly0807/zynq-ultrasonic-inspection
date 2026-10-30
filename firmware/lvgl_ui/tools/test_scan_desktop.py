import csv
import math
import os
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build/desktop"
HEADER = ["point_id", "stage_x_mm", "world_x_mm", "world_y_mm", "world_z_mm", "source", "status", "depth_mm", "waveform"]


def create_fixture(directory, source="DEMO", missing_point=None):
    directory = Path(directory)
    (directory / "waveforms").mkdir(parents=True, exist_ok=True)
    with (directory / "summary.csv").open("w", encoding="utf-8-sig", newline="") as summary:
        writer = csv.writer(summary)
        writer.writerow(HEADER)
        for point in range(11):
            suspect = point in (4, 5, 6)
            waveform = f"waveforms/{point:05d}.csv" if point != missing_point else f"waveforms/not_created_{point:05d}.csv"
            status = ("DEMO_" if source == "DEMO" else "") + ("SUSPECT" if suspect else "NO_INDICATION")
            writer.writerow([f"SYNTHETIC_TEST_NOT_HARDWARE_{point}", point * 5, 70 + point * 5, 0, 0,
                             source, status, "16.52" if suspect else "", waveform])
            if point == missing_point:
                continue
            with (directory / waveform).open("w", encoding="ascii", newline="") as output:
                samples = csv.writer(output)
                samples.writerow(["Sample in Buffer", "Sample in Window", "TRIGGER", "ad_data_r[7:0]",
                                  "ad_otr_r", "md1213_ina_OBUF", "gain_val[7:0]", "Sample Period ns", "Source Kind"])
                samples.writerow(["Radix - UNSIGNED", "UNSIGNED", "UNSIGNED", "HEX", "HEX", "HEX", "HEX", "UNSIGNED", "ASCII"])
                for index in range(4096):
                    time_us = index * 0.04
                    value = 128 + 65 * math.exp(-((time_us - 10.2) / 0.14) ** 2) * math.cos((time_us - 10.2) * 30)
                    if suspect:
                        value += 55 * math.exp(-((time_us - 5.6) / 0.14) ** 2) * math.cos((time_us - 5.6) * 30)
                    samples.writerow([index, index, int(index == 0), f"{max(0, min(255, round(value))):02X}",
                                      0, int(index == 0), "80", 40, source])
    return directory / "summary.csv"


def render(arguments, output, expected_source, expected_count):
    command = [str(BUILD / "ut_lvgl.exe"), *map(str, arguments), "--screenshot", str(output)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=20)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    assert f"source={expected_source} / samples={expected_count}" in result.stdout, result.stdout
    assert output.is_file() and output.stat().st_size > 800 * 480 * 4


def main():
    demo = create_fixture(BUILD / "scan_smoke/demo")
    historical = create_fixture(BUILD / "scan_smoke/synthetic_live_metadata", "LIVE")
    missing = create_fixture(BUILD / "scan_smoke/missing_waveform", missing_point=4)
    render(["--scan", demo, "--point", "4"], BUILD / "preview_scan_test.bmp", 3, 4096)
    render(["--scan", historical, "--point", "4"], BUILD / "preview_scan_recorded_test.bmp", 1, 4096)
    render(["--scan", missing, "--point", "4"], BUILD / "preview_scan_missing_test.bmp", 0, 0)
    render(["--scan-review"], BUILD / "preview_scan_empty_test.bmp", 0, 0)
    raw = historical.parent / "waveforms/00004.csv"
    for timestamp in (time.time(), time.time() + 86400, 1):
        os.utime(raw, (timestamp, timestamp))
        render(["--csv", raw, "--watch"], BUILD / "preview_csv_watch_test.bmp", 1, 4096)
    render(["--csv", demo.parent / "waveforms/00004.csv", "--watch"], BUILD / "preview_csv_demo_watch_test.bmp", 3, 4096)
    for arguments in (["--scan", str(demo), "--point", "11"], ["--scan", str(demo), "--watch"], ["--point", "1"],
                      ["--watch"], ["--demo", "--watch"], ["--csv", str(raw), "--demo"], ["--csv", str(raw), "--scan", str(demo)]):
        result = subprocess.run([str(BUILD / "ut_lvgl.exe"), *arguments], capture_output=True, timeout=10)
        assert result.returncode == 2, arguments
    print("PASS: headless LVGL scan render, exact waveform, file watch cannot promote fresh/future/old files to LIVE, DEMO retention, missing-frame clearing, empty review and source isolation; synthetic fixtures only")


if __name__ == "__main__":
    main()
