import argparse
import ctypes
import json
import math
import os
import re
import socket
import struct
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from board_acceptance import BoardClient, validate_token


class RouteRow(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "destination", "mask", "policy", "next_hop", "interface_index", "kind", "protocol", "age",
        "next_hop_as", "metric1", "metric2", "metric3", "metric4", "metric5")]


class InterfaceRow(ctypes.Structure):
    _fields_ = [("name", ctypes.c_uint16 * 256)] + [(name, ctypes.c_uint32) for name in (
        "interface_index", "kind", "mtu", "speed", "physical_address_length")] + [
        ("physical_address", ctypes.c_ubyte * 8)] + [(name, ctypes.c_uint32) for name in (
        "admin_status", "operational_status", "last_change", "in_octets", "in_unicast", "in_non_unicast",
        "in_discards", "in_errors", "in_unknown", "out_octets", "out_unicast", "out_non_unicast",
        "out_discards", "out_errors", "out_queue", "description_length")] + [("description", ctypes.c_ubyte * 256)]


def native_direct_route(expected_index):
    if os.name != "nt":
        raise PermissionError("Native direct-link observation requires Windows")
    library = ctypes.WinDLL("iphlpapi")
    library.GetBestRoute.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(RouteRow)]
    library.GetBestRoute.restype = ctypes.c_uint32
    library.GetIfEntry.argtypes = [ctypes.POINTER(InterfaceRow)]
    library.GetIfEntry.restype = ctypes.c_uint32
    address = lambda value: int.from_bytes(socket.inet_aton(value), sys.byteorder)
    route = RouteRow()
    if library.GetBestRoute(address("192.168.77.2"), address("192.168.77.1"), ctypes.byref(route)):
        raise PermissionError("Board route lookup failed; no credentials sent")
    adapter = InterfaceRow(interface_index=expected_index)
    if library.GetIfEntry(ctypes.byref(adapter)):
        raise PermissionError("Pinned Ethernet interface lookup failed; no credentials sent")
    return {"interface_index": route.interface_index, "destination": route.destination, "mask": route.mask,
            "next_hop": route.next_hop, "route_kind": route.kind,
            "ethernet": adapter.kind == 6, "up": adapter.operational_status == 5,
            "admin_enabled": adapter.admin_status == 1,
            "expected_destination": address("192.168.77.0"), "expected_mask": address("255.255.255.0")}


def direct_link_guard():
    expected_index = int(os.environ.get("UT_BOARD_ETHERNET_IFINDEX", "0"))
    if expected_index <= 0:
        raise PermissionError("A parent-verified physical Ethernet interface must be pinned")
    record = native_direct_route(expected_index)
    if (record["interface_index"] != expected_index or record["destination"] != record["expected_destination"] or
            record["mask"] != record["expected_mask"] or record["next_hop"] != 0 or record["route_kind"] != 3 or
            not record["ethernet"] or not record["up"] or not record["admin_enabled"]):
        raise PermissionError("Unexpected direct Ethernet route; no credentials sent")


@contextmanager
def keep_system_awake():
    if os.name != "nt":
        raise OSError("This host-side monitor requires Windows")
    setter = ctypes.windll.kernel32.SetThreadExecutionState
    setter.argtypes = [ctypes.c_uint]
    setter.restype = ctypes.c_uint
    if not setter(0x80000001):
        raise OSError("Temporary system-awake request failed")
    try:
        yield
    finally:
        setter(0x80000000)


def collect_sample(client, previous=None, *, verify_png=False, require_local_controls=False, touch_error_baseline=(0, 0)):
    if (not isinstance(touch_error_baseline, (tuple, list)) or len(touch_error_baseline) != 2 or
            any(type(value) is not int or not 0 <= value < 2 ** 32 for value in touch_error_baseline)):
        raise ValueError("Touch baseline requires explicit bounded error and rejection counters")
    device = client.read_json("/api/device")
    capture = device.get("real_capture", {})
    if (device.get("transmit_enabled") is not False or device.get("motion_enabled") is not False or
            capture.get("active") is not False or capture.get("capture_in_flight") is not False):
        raise PermissionError("Operator activity or safety-state change; read-only monitor stops")
    if device.get("runtime") != "ARM_LINUX" or device.get("machine") != "armv7l":
        raise ValueError("Expected the board ARM Linux runtime")
    if capture.get("state") != "STOPPED":
        raise ValueError("Expected acquisition STOPPED during camera-only monitoring")
    session = device.get("service_session")
    if not isinstance(session, str) or not session:
        raise ValueError("Missing board service identity")
    vision = client.read_json("/api/vision/state")
    if vision.get("preview_available") is not True or vision.get("source") != "USB_CAMERA":
        raise ValueError("Fresh real USB camera data unavailable")
    frame = vision.get("frame", {})
    age = frame.get("age_ms")
    if isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age) or not 0 <= age <= 2500:
        raise ValueError("Camera frame is stale or has invalid age")
    sequence, frame_id = frame.get("sequence"), frame.get("frame_id")
    if type(sequence) is not int or not 0 <= sequence < 2 ** 32:
        raise ValueError("Invalid camera sequence")
    if not isinstance(frame_id, str) or not re.fullmatch(r"USB_CAMERA-[0-9]+-[0-9]+-[0-9]+", frame_id):
        raise ValueError("Invalid camera frame identity")
    width, height = frame.get("width"), frame.get("height")
    if type(width) is not int or type(height) is not int or not 160 <= width <= 1280 or not 120 <= height <= 720:
        raise ValueError("Invalid camera geometry")
    if previous is not None:
        if previous["service_session"] != session:
            raise ValueError("Board service restarted during the observation")
        if previous["frame_id"] == frame_id or previous["sequence"] == sequence:
            raise ValueError("Camera frame counter did not advance")
    if verify_png:
        image = client.request("/api/vision/frame.png?frame_id=" + frame_id)
        if len(image) < 33 or image[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" or struct.unpack_from(">II", image, 16) != (width, height):
            raise ValueError("Camera PNG header or dimensions do not match the selected frame")
    services = device.get("services", {})
    diagnostics = services.get("camera_diagnostics", {})
    local_controls = {}
    if require_local_controls:
        lcd = services.get("lcd_diagnostics", {})
        echo = services.get("echo_setup_diagnostics", {})
        scan = services.get("local_scan_diagnostics", {})
        age_lcd = lcd.get("diagnostic_age_ms")
        touch = lcd.get("touch", {})
        if (services.get("lcd_process_alive") is not True or lcd.get("available") is not True or
                lcd.get("mode") != "LCD_TRANSPORT" or type(age_lcd) is not int or not 0 <= age_lcd <= 2000 or
                touch.get("bus_open") is not True or
                any(type(touch.get(key)) is not int or touch[key] != baseline
                    for key, baseline in zip(("errors", "rejected"), touch_error_baseline)) or
                touch.get("last_result", 0) not in (0, 1) or
                type(touch.get("reads")) is not int or touch["reads"] <= 0):
            raise ValueError("Local LCD or touch telemetry is unavailable, stale or faulty")
        if echo.get("available") is not True or scan.get("available") is not True:
            raise ValueError("Local acoustic and scan status must remain available")
        if echo.get("phase") != "IDLE" or scan.get("phase") != "IDLE" or scan.get("sampled_cells") != 0:
            raise PermissionError("Local operator setup or scan activity; read-only monitor stops")
        if previous is not None and touch["reads"] <= previous.get("touch_reads", -1):
            raise ValueError("Local touch read counter did not advance")
        local_controls = {"local_controls_checked": True, "touch_reads": touch["reads"], "touch_errors": touch["errors"],
                          "touch_rejected": touch["rejected"], "touch_error_baseline": list(touch_error_baseline),
                          "touch_error_delta": touch["errors"] - touch_error_baseline[0],
                          "lcd_flushes": lcd.get("flushes"), "touch_last_result": touch.get("last_result"),
                          "lcd_diagnostic_age_ms": age_lcd, "echo_phase": echo["phase"], "scan_phase": scan["phase"]}
    return {"service_session": session, "frame_id": frame_id, "sequence": sequence,
            "width": width, "height": height, "age_ms": age, "png_header_checked": verify_png,
            "marker_status": vision.get("marker", {}).get("status"),
            "calibrated": vision.get("calibrated") is True,
            "published_frames": diagnostics.get("published_frames"),
            "dropped_frames": diagnostics.get("dropped_frames"),
            "lcd_process_alive": services.get("lcd_process_alive") is True,
            "transmit_enabled": False, "motion_enabled": False, "capture_state": "STOPPED", **local_controls}


def write_summary(path, record):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def monitor(client, output, duration, interval, *, require_local_controls=False, touch_error_baseline=(0, 0)):
    output.mkdir(parents=True, exist_ok=True)
    if (output / "summary.json").exists() or (output / "samples.jsonl").exists():
        raise FileExistsError("Refusing to overwrite an earlier monitoring record")
    started = time.monotonic()
    summary = {"schema": "ut-camera-readonly-monitor/1", "status": "RUNNING", "pid": os.getpid(),
               "started_utc": datetime.now(timezone.utc).isoformat(), "requested_seconds": duration,
               "interval_seconds": interval, "passed_samples": 0, "failed_samples": 0,
               "png_headers_checked": 0, "maximum_frame_age_ms": 0,
               "operations": "GET_ONLY_NO_IMAGE_SAVED_NO_TX_NO_MOTION_NO_FLASH_NO_SD",
               "temporary_system_awake": True, "camera_stability_verified": False,
               "real_echo_verified": False, "physical_lcd_verified": False,
               "position_accuracy_verified": False}
    summary["local_controls_required"] = require_local_controls
    summary["touch_error_baseline"] = list(touch_error_baseline)
    summary["historical_touch_errors_present"] = bool(any(touch_error_baseline))
    summary["touch_observation_basis"] = "NO_NEW_ERRORS_FROM_EXPLICIT_COUNTER_BASELINE_NOT_ERROR_FREE_SINCE_BOOT"
    summary_path = output / "summary.json"
    previous, consecutive_errors = None, 0
    with (output / "samples.jsonl").open("x", encoding="utf-8") as log:
        try:
            while time.monotonic() - started < duration:
                record = {"host_utc": datetime.now(timezone.utc).isoformat(),
                          "elapsed_seconds": round(time.monotonic() - started, 3)}
                fatal = False
                try:
                    direct_link_guard()
                    sample = collect_sample(client, previous, verify_png=summary["passed_samples"] % 6 == 0,
                                            require_local_controls=require_local_controls, touch_error_baseline=touch_error_baseline)
                    record.update({"ok": True, **sample})
                    previous = sample
                    consecutive_errors = 0
                    summary["passed_samples"] += 1
                    summary["png_headers_checked"] += int(sample["png_header_checked"])
                    summary["maximum_frame_age_ms"] = max(summary["maximum_frame_age_ms"], sample["age_ms"])
                    summary["last_sample"] = sample
                except Exception as error:
                    record.update({"ok": False, "error_type": type(error).__name__})
                    summary["failed_samples"] += 1
                    consecutive_errors += 1
                    fatal = isinstance(error, PermissionError) or consecutive_errors >= 3
                log.write(json.dumps(record, ensure_ascii=False) + "\n")
                log.flush()
                summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
                write_summary(summary_path, summary)
                if fatal:
                    summary["status"] = "STOPPED_ON_NETWORK_SAFETY_CHANGE_OR_REPEATED_FAILURE"
                    break
                time.sleep(max(0, min(interval, duration - (time.monotonic() - started))))
            else:
                summary["status"] = "COMPLETED" if summary["failed_samples"] == 0 else "COMPLETED_WITH_ERRORS"
        except KeyboardInterrupt:
            summary["status"] = "INTERRUPTED"
        finally:
            summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
            summary["finished_utc"] = datetime.now(timezone.utc).isoformat()
            summary["camera_stability_verified"] = (summary["status"] == "COMPLETED" and summary["passed_samples"] >= 2)
            summary["temporary_system_awake"] = False
            write_summary(summary_path, summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bounded board camera observation; GET only, no image recording or board controls")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=float, default=3600)
    parser.add_argument("--interval-seconds", type=float, default=10)
    parser.add_argument("--require-local-controls", action="store_true")
    parser.add_argument("--touch-error-baseline", nargs=2, type=int, default=(0, 0), metavar=("ERRORS", "REJECTED"))
    arguments = parser.parse_args(argv)
    if not 2 <= arguments.duration_seconds <= 28800 or not 1 <= arguments.interval_seconds <= 60:
        raise ValueError("Use a bounded duration of 2..28800 seconds and interval of 1..60 seconds")
    if any(not 0 <= value < 2 ** 32 for value in arguments.touch_error_baseline):
        raise ValueError("Touch baseline counters must be unsigned 32-bit values")
    if any(arguments.touch_error_baseline) and not arguments.require_local_controls:
        raise ValueError("A nonzero touch baseline requires local-control observation")
    token = validate_token(os.environ.pop("UT_BOARD_OPERATOR_TOKEN", ""))
    client = BoardClient("192.168.77.2", 8766, token)
    token = None
    with keep_system_awake():
        summary = monitor(client, arguments.output, arguments.duration_seconds, arguments.interval_seconds,
                          require_local_controls=arguments.require_local_controls, touch_error_baseline=arguments.touch_error_baseline)
    print(json.dumps({"status": summary["status"], "passed_samples": summary["passed_samples"],
                      "failed_samples": summary["failed_samples"]}))
    return 0 if summary["camera_stability_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
