import importlib.util
import json
import os
import stat
import sys
import time
from pathlib import Path


CAMERA_STAGES = frozenset(("OPEN", "QUERY_CAPABILITIES", "CAPTURE_CAPABILITIES", "SET_FORMAT", "ACCEPT_FORMAT",
                          "ROW_STRIDE", "REQUEST_BUFFERS", "BUFFER_COUNT", "QUERY_BUFFER", "MAP_BUFFER",
                          "QUEUE_BUFFER", "ALLOCATE_RGB", "STREAM_ON", "WAIT_FRAME", "POLL_FRAME",
                          "DEQUEUE_BUFFER", "BUFFER_BOUNDS", "BAD_FRAME_LIMIT", "DROP_BAD_FRAME",
                          "REQUEUE_BUFFER", "OPEN_OUTPUT", "PUBLISH_FRAME", "SIGNAL"))
CAMERA_COUNTERS = ("errno", "width", "height", "stride", "pixel_format", "sequence", "fps_numerator",
                   "fps_denominator", "published_frames", "dropped_frames", "last_frame_ns", "updated_monotonic_ns")
TOUCH_COUNTERS = ("kind", "address", "probe_ack_mask", "reads", "errors", "rejected", "last_result",
                  "ft_chip_id", "ft_version", "goodix_product_code", "press_events", "release_events", "raw_x", "raw_y", "x", "y")
TOUCH_DETAIL_COUNTERS = ("probes", "raw_press_samples", "cancel_events", "last_poll_ms", "last_press_ms", "last_press_x", "last_press_y")
TOUCH_ORIENTATION_DIMENSIONS = ("raw_width", "raw_height", "screen_width", "screen_height")
TOUCH_ORIENTATION_FLAGS = ("swap_xy", "invert_x", "invert_y")


def private_status(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Diagnostic status cannot be a symbolic link")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        information = os.fstat(stream.fileno())
        if not stat.S_ISREG(information.st_mode) or information.st_size > 4096:
            raise ValueError("Diagnostic status must be a bounded regular file")
        record = json.loads(stream.read(4097))
    if not isinstance(record, dict):
        raise ValueError("Diagnostic status must be an object")
    return record


def lcd_diagnostics(path):
    unavailable = {"available": False, "basis": "TRANSPORT_AND_INPUT_TELEMETRY_NOT_PHYSICAL_ACCEPTANCE"}
    try:
        record = private_status(path)
        if record.get("schema") != "ut-lcd-status/1" or record.get("mode") not in ("LCD_TRANSPORT", "HEADLESS_ONLY"):
            return unavailable
        counters = ("updated_monotonic_ns", "flushes", "map_points", "waveform_source")
        if any(type(record.get(key)) is not int or not 0 <= record[key] < 2 ** 64 for key in counters):
            return unavailable
        touch = record.get("touch")
        if not isinstance(touch, dict) or any(type(touch.get(key)) is not int or not 0 <= touch[key] < 2 ** 32 for key in TOUCH_COUNTERS):
            return unavailable
        if any(type(touch.get(key)) is not bool for key in ("bus_open", "pressed")):
            return unavailable
        packet = touch.get("last_packet")
        if not isinstance(packet, list) or len(packet) != 7 or any(type(value) is not int or not 0 <= value <= 255 for value in packet):
            return unavailable
        safe = {key: record[key] for key in ("schema", "mode", *counters)}
        safe["touch"] = {key: touch[key] for key in (*TOUCH_COUNTERS, "bus_open", "pressed", "last_packet")}
        details = (*TOUCH_DETAIL_COUNTERS, "input_armed", "last_press_packet")
        if any(key in touch for key in details):
            if any(type(touch.get(key)) is not int or not 0 <= touch[key] < 2 ** 32 for key in TOUCH_DETAIL_COUNTERS):
                return unavailable
            pressed_packet = touch.get("last_press_packet")
            if (type(touch.get("input_armed")) is not bool or not isinstance(pressed_packet, list) or len(pressed_packet) != 7 or
                    any(type(value) is not int or not 0 <= value <= 255 for value in pressed_packet)):
                return unavailable
            safe["touch"].update({key: touch[key] for key in details})
        orientation = (*TOUCH_ORIENTATION_DIMENSIONS, *TOUCH_ORIENTATION_FLAGS)
        if any(key in touch for key in orientation):
            if any(type(touch.get(key)) is not int or not 0 <= touch[key] < 2 ** 16 for key in TOUCH_ORIENTATION_DIMENSIONS):
                return unavailable
            if any(type(touch.get(key)) is not bool for key in TOUCH_ORIENTATION_FLAGS):
                return unavailable
            safe["touch"].update({key: touch[key] for key in orientation})
        age = time.monotonic_ns() - record["updated_monotonic_ns"]
        safe.update(available=True, basis=unavailable["basis"], diagnostic_age_ms=age // 1000000 if age >= 0 else None)
        return safe
    except (OSError, UnicodeError, ValueError, TypeError):
        return unavailable


def camera_diagnostics(path):
    unavailable = {"available": False, "basis": "HELPER_STATE_NOT_PHYSICAL_ACCEPTANCE"}
    try:
        record = private_status(path)
        if record.get("schema") != "ut-uvc-status/1":
            return unavailable
        if record.get("state") not in ("STARTING", "RUNNING", "RECOVERING", "FAILED", "STOPPED") or record.get("stage") not in CAMERA_STAGES:
            return unavailable
        if any(type(record.get(key)) is not int or not 0 <= record[key] < 2 ** 64 for key in CAMERA_COUNTERS):
            return unavailable
        if any(type(record.get(key)) is not bool for key in ("supports_yuyv", "supports_mjpeg")):
            return unavailable
        safe = {key: record[key] for key in ("schema", "state", "stage", *CAMERA_COUNTERS, "supports_yuyv", "supports_mjpeg")}
        age = time.monotonic_ns() - record["updated_monotonic_ns"]
        safe.update(available=True, basis=unavailable["basis"], diagnostic_age_ms=age // 1000000 if age >= 0 else None)
        return safe
    except (OSError, UnicodeError, ValueError, TypeError):
        return unavailable


def process_alive(pid_path, expected, proc_root=Path("/proc")):
    try:
        text = Path(pid_path).read_text(encoding="ascii").strip()
        if not text.isdecimal() or not 0 < int(text) < 2 ** 31:
            return False
        with (proc_root / text / "cmdline").open("rb") as stream:
            command = stream.read(4096).rstrip(b"\0").decode("utf-8", errors="replace").split("\0")
        if expected in ("ut_lvgl_linux", "ut_uvc_capture"):
            return Path(command[0]).name == expected
        return any(command[index:index + 2] == ["-m", expected] for index in range(len(command) - 1))
    except (OSError, UnicodeError, ValueError):
        return False


def service_health(directory):
    root = Path(directory)
    try:
        opencv = importlib.util.find_spec("cv2") is not None
        numpy = importlib.util.find_spec("numpy") is not None
    except (ImportError, ValueError):
        opencv = numpy = False
    return {"schema": "ut-service-health/1", "basis": "PROCESS_AND_FILES_NOT_PHYSICAL_ACCEPTANCE",
            "lcd_process_alive": sys.platform == "linux" and process_alive("/run/utinspect-lvgl.pid", "ut_lvgl_linux"),
            "vision_process_alive": sys.platform == "linux" and process_alive("/run/utinspect-vision.pid", "utinspect.device_vision"),
            "basic_camera_process_alive": sys.platform == "linux" and process_alive("/run/utinspect-uvc.pid", "ut_uvc_capture"),
            "lcd_bridge_map_present": (root / "lcd/map.csv").is_file(),
            "lcd_bridge_waveform_present": (root / "lcd/waveform.csv").is_file(),
            "kernel_transport_present": sys.platform == "linux" and Path("/dev/ut_board").is_char_device(),
            "camera_node_present": sys.platform == "linux" and Path("/dev/video0").is_char_device(),
            "camera_diagnostics": camera_diagnostics(root / "vision/status.json"),
            "lcd_diagnostics": lcd_diagnostics(root / "lcd/status.json"),
            "opencv_module_present": opencv, "numpy_module_present": numpy,
            "physical_lcd_verified": False, "physical_touch_verified": False,
            "real_capture_verified": False, "persistent_storage_verified": False}
