import json
import math
import os
import stat
import struct
import threading
import time
from collections import deque
from pathlib import Path

from .echo import finite_number
from .records import atomic_json


STATUS = struct.Struct("<4sIQ12I12f")
PREVIEW = struct.Struct("<4sIQ")
PREVIEW_WIDTH, PREVIEW_HEIGHT = 320, 240
TIMING_BASIS = "DEQUEUE_TIMESTAMPS_NOT_SENSOR_SYNCHRONIZED"
REASONS = {"WAITING": 0, "READY": 1, "NO_CAMERA": 2, "NEED_CORNERS": 3,
           "NEED_LOCAL_LIVE": 4, "MARKER_LOST": 5, "HOLD_STILL": 6,
           "RECORDED": 7, "FINISHED": 8, "ERROR": 9, "UNCALIBRATED": 10,
           "ALREADY_SAMPLED": 11, "TIMING_MISMATCH": 12, "CALIBRATION_CHANGED": 13, "INVALID_ECHO": 14}
PHASES = {"IDLE": 0, "SCANNING": 1, "FINISHED": 2}


class LocalScan:
    def __init__(self, directory, session, vision, live, *, clock=time.monotonic_ns):
        self.directory, self.session = Path(directory), session
        self.vision, self.live, self.clock = vision, live, clock
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.worker = None
        self.phase, self.reason, self.error = "IDLE", "WAITING", ""
        self.corners = []
        self.selection_id = None
        self.calibration_key = None
        self.last_preview = 0
        self.last_position = None
        self.stable_since = 0
        self.stable_frames = deque(maxlen=3)
        self.recorded_cells = set()
        self.last_frame_id = None
        self.skew_ms = -1.0
        self.sequence = 0
        self.last_state = {}
        from .echo_setup import EchoSetup
        self.echo_setup = EchoSetup(directory, session, live, clock=clock)

    @staticmethod
    def _key(vision):
        frame = vision.get("frame", {})
        return (frame.get("stream_id"), frame.get("width"), frame.get("height"),
                tuple(tuple(corner) for corner in vision.get("corners") or []))

    def command(self, action, payload):
        if not isinstance(action, str) or not isinstance(payload, dict):
            raise ValueError("Local scan payload must be an object")
        with self.lock:
            if action.startswith("echo-"):
                if self.phase == "SCANNING":
                    raise ValueError("Finish the scan before changing acoustic setup")
                self.echo_setup.command(action, payload)
                return
            if action == "freeze" and not payload:
                if self.phase == "SCANNING":
                    raise ValueError("Finish scanning before changing camera calibration")
                state = self.vision.freeze()
                self.selection_id = state["selection"]["calibration_id"]
                self.corners = []
                self.reason = "NEED_CORNERS"
            elif action == "corner" and set(payload) == {"x", "y"}:
                if self.selection_id is None or len(self.corners) >= 4:
                    raise ValueError("Freeze the camera and select exactly four corners")
                if not all(finite_number(payload[name]) and 0 <= payload[name] < 1 for name in ("x", "y")):
                    raise ValueError("Corner must lie inside the camera preview")
                self.corners.append([float(payload["x"]), float(payload["y"])])
            elif action == "calibrate" and set(payload) == {"fixed_confirmed"} and payload["fixed_confirmed"] is True:
                if self.phase == "SCANNING" or len(self.corners) != 4:
                    raise ValueError("Select TL, TR, BR and BL before confirming a fixed camera")
                state = self.vision.state()
                selection = state.get("selection", {})
                if selection.get("calibration_id") != self.selection_id:
                    raise ValueError("Frozen image expired; select the corners again")
                self.vision.calibrate({"fixed_confirmed": True, "calibration_id": self.selection_id,
                                       "corners": [[position[0] * selection["width"], position[1] * selection["height"]]
                                                   for position in self.corners]})
                self.selection_id = None
                self.reason = "READY"
            elif (action == "scan-start" and set(payload) == {"tracking_confirmed", "normal_incidence_confirmed"} and
                  payload["tracking_confirmed"] is True and payload["normal_incidence_confirmed"] is True):
                state = self.vision.state()
                owner, frame = self.live.mapping_frame()
                if not state.get("preview_available") or state.get("source") != "USB_CAMERA" or not state.get("calibrated"):
                    raise ValueError("Calibrate the real fixed camera before starting a scan")
                if owner is None or not owner.startswith("lcd-") or frame is None:
                    raise ValueError("Explicitly start real acquisition from the local screen first")
                if self.phase == "FINISHED":
                    raise ValueError("This scan is finished; keep its results until a new session is created")
                if not self.session.spec.normal_incidence_confirmed:
                    from dataclasses import replace
                    with self.session.lock:
                        self.session.spec = replace(self.session.spec, normal_incidence_confirmed=True)
                self.phase, self.reason = "SCANNING", "HOLD_STILL"
                self.calibration_key = self._key(state)
                self.last_position, self.stable_since = None, 0
                self.stable_frames.clear()
            elif action == "scan-finish" and not payload:
                self.phase, self.reason = "FINISHED", "FINISHED"
                self.live.stop()
            elif action == "new-scan" and set(payload) == {"archive_confirmed"} and payload["archive_confirmed"] is True:
                self.live.stop()
                self.session.begin_new()
                self.recorded_cells.clear()
                self.last_frame_id = self.last_position = None
                self.stable_frames.clear()
                self.phase, self.reason = "IDLE", "READY"
            else:
                raise ValueError("Unknown or unconfirmed local scan command")
            self.error = ""

    def _mailbox(self):
        path = self.directory / "scan-command.json"
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
                                 getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        except FileNotFoundError:
            return
        with os.fdopen(descriptor, "rb") as source:
            information = os.fstat(source.fileno())
            if not stat.S_ISREG(information.st_mode) or information.st_size > 2048 or path.is_symlink():
                raise ValueError("Unsafe local scan command file")
            data = source.read(2049)
        path.unlink()
        request = json.loads(data)
        if not isinstance(request, dict) or set(request) != {"action", "payload", "monotonic_ns"}:
            raise ValueError("Invalid local scan command envelope")
        timestamp = request["monotonic_ns"]
        if type(timestamp) is not int or not 0 <= self.clock() - timestamp <= 500_000_000:
            raise ValueError("Expired local scan command; no action taken")
        self.command(request["action"], request["payload"])

    def _record(self, vision, now):
        if self.phase != "SCANNING":
            return
        owner, frame = self.live.mapping_frame()
        if not owner or not owner.startswith("lcd-") or frame is None:
            self.phase, self.reason = "IDLE", "NEED_LOCAL_LIVE"
            return
        if not vision.get("calibrated") or self._key(vision) != self.calibration_key:
            self.phase, self.reason = "IDLE", "CALIBRATION_CHANGED"
            return
        position = vision.get("position_mm")
        if (not vision.get("preview_available") or vision.get("source") != "USB_CAMERA" or
                vision.get("marker", {}).get("status") != "FOUND" or position is None):
            self.last_position, self.stable_since = None, 0
            self.stable_frames.clear()
            self.reason = "MARKER_LOST"
            return
        camera = vision["frame"]
        camera_time = camera["captured_monotonic_ns"]
        if not 0 <= now - camera_time <= 300_000_000:
            self.reason = "TIMING_MISMATCH"
            self.last_position = None
            return
        if (self.last_position is None or math.dist(self.last_position, position) > min(1.0, self.session.spec.grid_mm / 4) or
                self.session.spec.cell(*self.last_position) != self.session.spec.cell(*position)):
            self.last_position, self.stable_since = list(position), now
            self.stable_frames.clear()
        if camera["frame_id"] not in self.stable_frames:
            self.stable_frames.append(camera["frame_id"])
        stable_ms = (now - self.stable_since) / 1_000_000
        if stable_ms < 350 or len(self.stable_frames) < 3:
            self.reason = "HOLD_STILL"
            return
        self.skew_ms = abs(camera_time - frame.acquired_monotonic_ns) / 1_000_000
        if self.skew_ms > 200:
            self.reason = "TIMING_MISMATCH"
            return
        cell = self.session.spec.cell(*position)
        if cell in self.recorded_cells or frame.frame_id == self.last_frame_id:
            self.reason = "ALREADY_SAMPLED"
            return
        evidence = {"camera_frame_id": camera["frame_id"], "camera_monotonic_ns": camera_time,
                    "adc_monotonic_ns": frame.acquired_monotonic_ns, "skew_ms": self.skew_ms,
                    "stable_ms": min(stable_ms, 60_000), "timing_basis": TIMING_BASIS}
        point = self.session.add(frame, *position, position_confirmed=True, visual_evidence=evidence)
        self.recorded_cells.add(cell)
        self.last_frame_id = frame.frame_id
        self.reason = ("RECORDED" if point["status"] in ("NO_INDICATION", "SUSPECT") else
                       "UNCALIBRATED" if point["status"] == "UNCALIBRATED" else "INVALID_ECHO")

    def tick(self):
        with self.lock:
            now = self.clock()
            try:
                self._mailbox()
                self.echo_setup.tick()
                vision = self.vision.state()
                now = self.clock()
                self._record(vision, now)
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.error, self.reason = str(error), "ERROR"
                if self.phase == "SCANNING":
                    self.phase = "IDLE"
                    self.live.stop()
                vision = self.vision.state()
            if self.phase == "IDLE" and self.reason in {"WAITING", "READY", "NO_CAMERA", "NEED_CORNERS"}:
                self.reason = ("NO_CAMERA" if not vision.get("preview_available") else
                               "READY" if vision.get("calibrated") else "NEED_CORNERS")
            elif self.phase == "SCANNING" and not vision.get("preview_available") and self.reason != "ERROR":
                self.reason = "NO_CAMERA"
            snapshot = self.session.snapshot()
            now = self.clock()
            live = self.live.state()
            position = vision.get("position_mm")
            flags = (int(bool(vision.get("preview_available"))) | int(bool(vision.get("calibrated"))) << 1 |
                     int(position is not None) << 2 | int(self.selection_id is not None) << 3 |
                     int(bool(live["active"])) << 4 | int(bool(self.session.echo.calibrated)) << 5)
            source = vision.get("frame", {})
            corners = self.corners + [[-1.0, -1.0]] * (4 - len(self.corners))
            self.sequence = (self.sequence + 1) % 2 ** 32
            values = (PHASES[self.phase], REASONS[self.reason], source.get("width", 0), source.get("height", 0),
                      len(self.corners), snapshot["sampled_cells"], snapshot["total_cells"], snapshot["valid_cells"],
                      snapshot["suspect_cells"], snapshot["depth_point_count"], self.sequence, 0)
            floats = (*(position if position is not None else [-1.0, -1.0]), snapshot["grid_coverage_percent"], self.skew_ms,
                      *(coordinate for corner in corners for coordinate in corner))
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.directory / "scan-status.bin.tmp"
            temporary.write_bytes(STATUS.pack(b"UTS1", flags, now, *values, *floats))
            temporary.replace(self.directory / "scan-status.bin")
            self.last_state = {"schema": "ut-local-scan/1", "phase": self.phase, "reason": self.reason, "error": self.error,
                               "updated_monotonic_ns": now, "position_mm": position,
                               "sampled_cells": snapshot["sampled_cells"], "total_cells": snapshot["total_cells"],
                               "valid_cells": snapshot["valid_cells"], "coverage_percent": snapshot["grid_coverage_percent"],
                               "echo_calibrated": self.session.echo.calibrated, "position_accuracy_verified": False,
                               "vision_error": str(vision.get("error", ""))[:160],
                               "timing_basis": TIMING_BASIS, "no_network_required": True, "results_volatile": True}
            atomic_json(self.directory / "scan-status.json", self.last_state)
            if now - self.last_preview >= 500_000_000 and vision.get("preview_available"):
                try:
                    pixels, metadata = self.vision.local_preview()
                    temporary = self.directory / "scan-preview.bin.tmp"
                    temporary.write_bytes(PREVIEW.pack(b"UTP1", len(pixels), now) + pixels)
                    temporary.replace(self.directory / "scan-preview.bin")
                    self.last_preview = now
                except (OSError, ValueError):
                    (self.directory / "scan-preview.bin").unlink(missing_ok=True)
            return dict(self.last_state)

    def start_worker(self):
        if self.worker is not None:
            raise ValueError("Local scan worker already started")
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "scan-command.json").unlink(missing_ok=True)
        self.worker = threading.Thread(target=self._run, name="ut-local-scan", daemon=True)
        self.worker.start()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except (OSError, ValueError, RuntimeError) as error:
                with self.lock:
                    self.phase, self.reason, self.error = "IDLE", "ERROR", str(error)
            self.stop_event.wait(.2)

    def close(self):
        self.stop_event.set()
        if self.worker is not None:
            self.worker.join(timeout=3)
            if self.worker.is_alive():
                raise RuntimeError("Local scan worker did not stop")
