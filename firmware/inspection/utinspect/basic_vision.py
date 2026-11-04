import math
import os
import secrets
import stat
import struct
import threading
import time
import zlib
from collections import deque
from dataclasses import dataclass
from pathlib import Path


MAX_FRAME_BYTES = 1280 * 720 * 3 + 256
MAX_AGE_NS = 2_500_000_000
SELECTION_AGE_NS = 120_000_000_000
POSITION_AGE_NS = 300_000_000
PROBE_STABLE_NS = 350_000_000


@dataclass(frozen=True)
class CameraFrame:
    width: int
    height: int
    sequence: int
    captured_ns: int
    stream_id: int
    source: str
    pixels: bytes
    native_marker: tuple | None = None

    @property
    def frame_id(self):
        return f"{self.source}-{self.stream_id}-{self.sequence}-{self.captured_ns}"

    def metadata(self, now):
        return {"frame_id": self.frame_id, "width": self.width, "height": self.height,
                "sequence": self.sequence, "age_ms": round((now - self.captured_ns) / 1_000_000, 1),
                "captured_monotonic_ns": self.captured_ns, "stream_id": self.stream_id,
                "marker_backend": "NATIVE_C" if self.native_marker is not None else "PYTHON_FALLBACK"}


def parse_frame(data, now_ns, allow_host_fixture=False):
    if len(data) > MAX_FRAME_BYTES:
        raise ValueError("Camera frame exceeds the bounded buffer")
    lines = data.split(b"\n", 4)
    if len(lines) != 5 or lines[0] != b"P6" or lines[3] != b"255":
        raise ValueError("Expected the native camera PPM format")
    tags = lines[1].split()
    dimensions = lines[2].split()
    if len(tags) not in (5, 10) or tags[0] != b"#" or len(dimensions) != 2:
        raise ValueError("Invalid camera frame metadata")
    if tags[1] in (b"UTCAM1", b"UTCAM2", b"UTCAM3"):
        source = "USB_CAMERA"
    elif tags[1] == b"UTHOST1" and allow_host_fixture:
        source = "HOST_FIXTURE"
    else:
        raise ValueError("Unsupported camera source; host fixtures are disabled on the board")
    if (tags[1] in (b"UTCAM2", b"UTCAM3")) != (len(tags) == 10):
        raise ValueError("Native camera metadata version and length disagree")
    values = tags[2:5] + dimensions
    if any(not value.isdigit() or len(value) > 20 for value in values):
        raise ValueError("Invalid camera frame integers")
    sequence, captured, stream_id, width, height = map(int, values)
    if not 0 <= sequence < 2 ** 32 or not 0 < stream_id <= captured < 2 ** 63:
        raise ValueError("Invalid camera sequence or stream identity")
    if not 160 <= width <= 1280 or not 120 <= height <= 720 or width % 2:
        raise ValueError("Unsupported camera geometry")
    if len(lines[4]) != width * height * 3:
        raise ValueError("Truncated or oversized camera pixel data")
    if not 0 <= now_ns - captured <= MAX_AGE_NS:
        raise ValueError("Camera frame is stale or belongs to a different monotonic clock")
    native_marker = None
    if tags[1] in (b"UTCAM2", b"UTCAM3"):
        if any(not value.isdigit() or len(value) > 12 for value in tags[5:]):
            raise ValueError("Invalid native marker integers")
        status, candidates, pixel_x, pixel_y, area = map(int, tags[5:])
        if (status not in ((1, 2, 3, 4, 5) if tags[1] == b"UTCAM3" else (1, 2, 3, 4)) or candidates > width * height or area > width * height or
                pixel_x >= width * 1000 or pixel_y >= height * 1000 or
                status == 1 and (candidates != 1 or not 6 <= area <= width * height * .12) or
                status == 2 and candidates != 0 or status == 3 and candidates < 2 or
                status == 5 and (candidates != 1 or not 9 <= area <= width * height * .005) or
                status == 4 and (candidates != 1 or area <= width * height * .12)):
            raise ValueError("Invalid native marker geometry or classification")
        native_marker = (status, candidates, pixel_x / 1000.0, pixel_y / 1000.0, area)
    return CameraFrame(width, height, sequence, captured, stream_id, source, lines[4], native_marker)


def png_bytes(frame):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    stride = frame.width * 3
    rows = b"".join(b"\0" + frame.pixels[start:start + stride] for start in range(0, len(frame.pixels), stride))
    geometry = struct.pack(">IIBBBBB", frame.width, frame.height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", geometry) + chunk(b"IDAT", zlib.compress(rows, 1)) + chunk(b"IEND", b"")


def solve_homography(corners, width, height):
    if not isinstance(corners, list) or len(corners) != 4:
        raise ValueError("Select exactly four workpiece corners")
    for corner in corners:
        if not isinstance(corner, list) or len(corner) != 2 or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in corner):
            raise ValueError("Corner coordinates must be finite pixel pairs")
        if not 0 <= corner[0] < width or not 0 <= corner[1] < height:
            raise ValueError("Corner lies outside the displayed frame")
    crosses = []
    for index in range(4):
        first, second, third = corners[index], corners[(index + 1) % 4], corners[(index + 2) % 4]
        if math.dist(first, second) < 12:
            raise ValueError("Workpiece corners are too close")
        crosses.append((second[0] - first[0]) * (third[1] - second[1]) - (second[1] - first[1]) * (third[0] - second[0]))
    area = sum(corners[index][0] * corners[(index + 1) % 4][1] - corners[(index + 1) % 4][0] * corners[index][1] for index in range(4)) / 2
    if min(crosses) <= 1e-6 or area < max(256, width * height * .005):
        raise ValueError("Use a sufficiently large convex quadrilateral in clockwise image order")
    rows = []
    for (pixel_x, pixel_y), (target_x, target_y) in zip(corners, ((0, 0), (1, 0), (1, 1), (0, 1))):
        image_x, image_y = pixel_x / width, pixel_y / height
        rows.append([image_x, image_y, 1, 0, 0, 0, -target_x * image_x, -target_x * image_y, target_x])
        rows.append([0, 0, 0, image_x, image_y, 1, -target_y * image_x, -target_y * image_y, target_y])
    for column in range(8):
        pivot = max(range(column, 8), key=lambda index: abs(rows[index][column]))
        if abs(rows[pivot][column]) < 1e-10:
            raise ValueError("Degenerate workpiece calibration")
        rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        rows[column] = [value / divisor for value in rows[column]]
        for index in range(8):
            if index != column:
                factor = rows[index][column]
                rows[index] = [value - factor * pivot_value for value, pivot_value in zip(rows[index], rows[column])]
    return [rows[index][-1] for index in range(8)]


def project_point(transform, pixel, width, height, length_mm, width_mm):
    image_x, image_y = pixel[0] / width, pixel[1] / height
    denominator = transform[6] * image_x + transform[7] * image_y + 1
    if abs(denominator) < 1e-9:
        raise ValueError("Position falls on a singular projection")
    position = [(transform[0] * image_x + transform[1] * image_y + transform[2]) / denominator * length_mm,
                (transform[3] * image_x + transform[4] * image_y + transform[5]) / denominator * width_mm]
    if not all(math.isfinite(value) for value in position):
        raise ValueError("Invalid projected position")
    return position


def unproject_point(transform, position, width, height, length_mm, width_mm):
    target_x, target_y = position[0] / length_mm, position[1] / width_mm
    first = transform[0] - target_x * transform[6], transform[1] - target_x * transform[7]
    second = transform[3] - target_y * transform[6], transform[4] - target_y * transform[7]
    right_x, right_y = target_x - transform[2], target_y - transform[5]
    determinant = first[0] * second[1] - first[1] * second[0]
    if abs(determinant) < 1e-10:
        raise ValueError("Position falls on a singular inverse projection")
    pixel = [(right_x * second[1] - first[1] * right_y) / determinant * width,
             (first[0] * right_y - right_x * second[0]) / determinant * height]
    if not all(math.isfinite(value) for value in pixel):
        raise ValueError("Invalid inverse projection")
    return pixel


def _center_dot_marker(frame):
    step = max(1, math.ceil(max(frame.width, frame.height) / 240))
    columns, rows = frame.width // step, frame.height // step
    mask = bytearray(columns * rows)
    for row in range(rows):
        for column in range(columns):
            offset = ((row * step + step // 2) * frame.width + column * step + step // 2) * 3
            mask[row * columns + column] = max(frame.pixels[offset:offset + 3]) <= 80
    candidates = []
    for start in range(len(mask)):
        if not mask[start]:
            continue
        mask[start] = 0
        queue = deque([start])
        count = sum_x = sum_y = 0
        minimum_x, minimum_y, maximum_x, maximum_y = columns, rows, 0, 0
        while queue:
            row, column = divmod(queue.popleft(), columns)
            count += 1
            sum_x += column
            sum_y += row
            minimum_x, maximum_x = min(minimum_x, column), max(maximum_x, column)
            minimum_y, maximum_y = min(minimum_y, row), max(maximum_y, row)
            for next_row, next_column in ((row - 1, column), (row + 1, column), (row, column - 1), (row, column + 1)):
                if 0 <= next_row < rows and 0 <= next_column < columns:
                    index = next_row * columns + next_column
                    if mask[index]:
                        mask[index] = 0
                        queue.append(index)
        extent_x, extent_y = maximum_x - minimum_x + 1, maximum_y - minimum_y + 1
        area = count * step * step
        if (count < 3 or not 9 <= area <= frame.width * frame.height * .005 or
                max(extent_x, extent_y) > min(extent_x, extent_y) * 2 or count * 4 < extent_x * extent_y):
            continue
        center = [sum_x / count * step + step // 2, sum_y / count * step + step // 2]
        red_ring = 0
        for direction_y in (-1, 0, 1):
            for direction_x in (-1, 0, 1):
                if direction_x == direction_y == 0:
                    continue
                sample_x = int(center[0]) + direction_x * (extent_x * step // 2 + step * 2)
                sample_y = int(center[1]) + direction_y * (extent_y * step // 2 + step * 2)
                if 0 <= sample_x < frame.width and 0 <= sample_y < frame.height:
                    offset = (sample_y * frame.width + sample_x) * 3
                    red, green, blue = frame.pixels[offset:offset + 3]
                    red_ring += (red >= 110 and red - green >= 65 and red - blue >= 55 and
                                 red * 100 > green * 170 and red * 100 > blue * 170)
        if red_ring >= 6:
            candidates.append({"pixel": center, "area_pixels": area})
    if len(candidates) > 1:
        return {"status": "AMBIGUOUS", "candidate_count": len(candidates), "pixel": None}
    if candidates:
        return {"status": "FOUND", "candidate_count": 1, "point_kind": "CENTER_BLACK_DOT", **candidates[0]}
    return None


def red_marker(frame):
    if frame.native_marker is not None:
        status, candidates, pixel_x, pixel_y, area = frame.native_marker
        result = {"status": {1: "FOUND", 2: "NOT_FOUND", 3: "AMBIGUOUS", 4: "TOO_LARGE", 5: "FOUND"}[status],
                  "candidate_count": candidates, "pixel": [pixel_x, pixel_y] if status in (1, 5) else None}
        if status in (1, 5):
            result["area_pixels"] = area
        if status == 5:
            result["point_kind"] = "CENTER_BLACK_DOT"
        return result
    dot = _center_dot_marker(frame)
    if dot is not None:
        return dot
    step = max(1, math.ceil(max(frame.width, frame.height) / 240))
    columns, rows = frame.width // step, frame.height // step
    mask = bytearray(columns * rows)
    for row in range(rows):
        offset = ((row * step + step // 2) * frame.width + step // 2) * 3
        for column in range(columns):
            red, green, blue = frame.pixels[offset:offset + 3]
            mask[row * columns + column] = (red >= 110 and red - green >= 65 and red - blue >= 55 and
                                           red * 100 > green * 170 and red * 100 > blue * 170 and
                                           (green - blue) * 100 <= (red - blue) * 15)
            offset += step * 3
    candidates = []
    for start in range(len(mask)):
        if not mask[start]:
            continue
        mask[start] = 0
        queue = deque([start])
        count = sum_x = sum_y = 0
        minimum_x, minimum_y, maximum_x, maximum_y = columns, rows, 0, 0
        while queue:
            current = queue.popleft()
            row, column = divmod(current, columns)
            count += 1
            sum_x += column
            sum_y += row
            minimum_x, maximum_x = min(minimum_x, column), max(maximum_x, column)
            minimum_y, maximum_y = min(minimum_y, row), max(maximum_y, row)
            for next_row, next_column in ((row - 1, column), (row + 1, column), (row, column - 1), (row, column + 1)):
                if 0 <= next_row < rows and 0 <= next_column < columns:
                    target = next_row * columns + next_column
                    if mask[target]:
                        mask[target] = 0
                        queue.append(target)
        if count >= max(6, columns * rows * .00015):
            extent_x, extent_y = maximum_x - minimum_x + 1, maximum_y - minimum_y + 1
            if max(extent_x, extent_y) * 10 <= min(extent_x, extent_y) * 18 and count * 100 >= extent_x * extent_y * 50:
                candidates.append({"pixel": [sum_x / count * step + step // 2, sum_y / count * step + step // 2], "area_pixels": count * step * step})
    if len(candidates) != 1:
        return {"status": "NOT_FOUND" if not candidates else "AMBIGUOUS", "candidate_count": len(candidates), "pixel": None}
    marker = candidates[0]
    if marker["area_pixels"] > frame.width * frame.height * .12:
        return {"status": "TOO_LARGE", "candidate_count": 1, "pixel": None}
    return {"status": "FOUND", "candidate_count": 1, **marker}


class BasicVision:
    def __init__(self, frame_path, length_mm=80, width_mm=80, *, allow_host_fixture=False, clock=time.monotonic_ns,
                 require_center_dot=False):
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0 for value in (length_mm, width_mm)):
            raise ValueError("Invalid workpiece dimensions")
        self.path = Path(frame_path)
        self.length_mm, self.width_mm = length_mm, width_mm
        self.allow_host_fixture, self.clock = allow_host_fixture, clock
        self.require_center_dot = require_center_dot
        self.lock = threading.RLock()
        self.frames = {}
        self.latest = None
        self.selection = None
        self.selection_created_ns = 0
        self.calibration_id = None
        self.surface_calibration_id = None
        self.transform = None
        self.corners = None
        self.marker_key = None
        self.marker = None
        self.png_key = None
        self.png = None
        self._reset_probe()

    def _reset_probe(self):
        self.probe_samples = []
        self.probe_transform = None
        self.registration_id = None
        self.marker_history = deque(maxlen=8)

    def _probe_targets(self):
        inset_x, inset_y = self.length_mm / 8, self.width_mm / 8
        return [[inset_x, inset_y], [self.length_mm - inset_x, inset_y],
                [self.length_mm - inset_x, self.width_mm - inset_y], [inset_x, self.width_mm - inset_y],
                [self.length_mm / 2, self.width_mm / 2]]

    def _track_marker(self, frame, marker, now):
        if self.transform is None or marker.get("status") != "FOUND" or now - frame.captured_ns > POSITION_AGE_NS:
            self.marker_history.clear()
            return
        while self.marker_history and frame.captured_ns - self.marker_history[0][1] > 1_500_000_000:
            self.marker_history.popleft()
        if not self.marker_history or self.marker_history[-1][0] != frame.frame_id:
            self.marker_history.append((frame.frame_id, frame.captured_ns, list(marker["pixel"])))

    def _probe_stable(self):
        if len(self.marker_history) < 3 or self.marker_history[-1][1] - self.marker_history[0][1] < PROBE_STABLE_NS:
            return False
        positions = [project_point(self.transform, entry[2], self.latest.width, self.latest.height,
                                   self.length_mm, self.width_mm) for entry in self.marker_history]
        return all(math.dist(position, positions[-1]) <= 1.0 for position in positions)

    def _probe_state(self):
        count = len(self.probe_samples)
        return {"complete": count == 5, "point_count": count, "total_points": 5,
                "registration_id": self.registration_id, "contact_targets_mm": self._probe_targets(),
                "next_contact_mm": self._probe_targets()[count] if count < 5 else None,
                "next_surface_pixel": unproject_point(self.transform, self._probe_targets()[count], self.latest.width, self.latest.height,
                                                      self.length_mm, self.width_mm) if self.transform is not None and count < 5 else None,
                "stable": self._probe_stable(), "center_error_mm": self.probe_samples[-1].get("error_mm") if count == 5 else None,
                "max_center_error_mm": 2.0, "pose": "FIXED_MARKER_HEIGHT_AND_NORMAL_PROBE_ORIENTATION",
                "persistence": "MEMORY_ONLY_RECALIBRATE_AFTER_CAMERA_BLOCK_OR_MARKER_MOVES"}

    def _current(self):
        descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            information = os.fstat(stream.fileno())
            if not stat.S_ISREG(information.st_mode) or information.st_size > MAX_FRAME_BYTES or self.path.is_symlink():
                raise ValueError("Unsafe camera frame file")
            data = stream.read(MAX_FRAME_BYTES + 1)
            frame = parse_frame(data, self.clock(), self.allow_host_fixture)
            if self.latest and frame.stream_id == self.latest.stream_id and frame.captured_ns <= self.latest.captured_ns and frame != self.latest:
                raise ValueError("Camera timestamp regressed or a frame identity was reused")
            if self.latest and (frame.stream_id, frame.source, frame.width, frame.height) != (self.latest.stream_id, self.latest.source, self.latest.width, self.latest.height):
                self.transform = self.corners = self.selection = self.calibration_id = None
                self.surface_calibration_id = None
                self.frames.clear()
                self._reset_probe()
            self.latest = frame
            self.frames[frame.frame_id] = frame
            while len(self.frames) > 4:
                del self.frames[next(iter(self.frames))]
        now = self.clock()
        if self.latest is None or not 0 <= now - self.latest.captured_ns <= MAX_AGE_NS:
            raise ValueError("Camera frame is stale; reconnect the camera before using coordinates")
        return self.latest, now

    def state(self):
        with self.lock:
            result = {"schema": "ut-basic-vision/1", "backend": "V4L2_YUYV_STDLIB", "preview_available": False,
                      "source": "UNAVAILABLE", "calibrated": self.transform is not None, "position_mm": None,
                      "timing_basis": "HELPER_DEQUEUE_MONOTONIC_NOT_SENSOR_EXPOSURE_OR_ULTRASOUND_TRIGGER",
                      "workpiece_mm": [self.length_mm, self.width_mm], "physical_accuracy_verified": False,
                      "coordinate_use": "VISUAL_AID_ONLY_RECONFIRM_PROBE_BEFORE_EACH_ACQUISITION",
                      "coordinate_basis": "RED_MARKER_SURFACE_PROJECTION_NOT_CONTACT_POINT",
                      "internal_damage_detection": False, "automatic_workpiece_detection": False}
            try:
                frame, now = self._current()
                if self.marker_key != frame.frame_id:
                    self.marker, self.marker_key = red_marker(frame), frame.frame_id
                now = self.clock()
                if not 0 <= now - frame.captured_ns <= MAX_AGE_NS:
                    raise ValueError("Camera processing exceeded the frame freshness limit")
                marker = dict(self.marker)
                if self.require_center_dot and marker["status"] == "FOUND" and marker.get("point_kind") != "CENTER_BLACK_DOT":
                    marker["status"] = "CENTER_DOT_MISSING"
                if marker.get("point_kind") == "CENTER_BLACK_DOT":
                    result["coordinate_basis"] = "BLACK_DOT_SURFACE_PROJECTION_NOT_CONTACT_POINT"
                self._track_marker(frame, marker, now)
                if marker["status"] == "FOUND" and now - frame.captured_ns > POSITION_AGE_NS:
                    marker["status"] = "STALE_POSITION"
                if marker["status"] == "FOUND" and self.transform is not None:
                    position = project_point(self.transform, marker["pixel"], frame.width, frame.height, self.length_mm, self.width_mm)
                    result["surface_projection_mm"] = [round(value, 3) for value in position]
                    if self.registration_id is not None:
                        position = self._probe_position(marker["pixel"], frame)
                        result["coordinate_basis"] = "REGISTERED_PROBE_CONTACT_ESTIMATE"
                    if -1e-7 <= position[0] <= self.length_mm + 1e-7 and -1e-7 <= position[1] <= self.width_mm + 1e-7:
                        position = [min(self.length_mm, max(0, position[0])), min(self.width_mm, max(0, position[1]))]
                        result["position_mm"] = [round(value, 3) for value in position]
                        if self.registration_id is not None:
                            result["contact_pixel"] = unproject_point(self.transform, position, frame.width, frame.height,
                                                                      self.length_mm, self.width_mm)
                    else:
                        marker["status"] = "OUTSIDE_WORKPIECE"
                result.update(preview_available=True, source=frame.source, frame=frame.metadata(now), marker=marker,
                              calibrated=self.transform is not None, corners=self.corners, surface_calibration_id=self.surface_calibration_id)
                if self.selection is not None and 0 <= now - self.selection_created_ns <= SELECTION_AGE_NS:
                    result["selection"] = {"calibration_id": self.calibration_id, **self.selection.metadata(now)}
            except (OSError, ValueError) as error:
                self.marker_history.clear()
                result["error"] = str(error)
            result["probe_registration"] = self._probe_state()
            return result

    def _probe_position(self, pixel, frame):
        position = project_point(self.probe_transform, pixel, frame.width, frame.height,
                                 self.length_mm * .75, self.width_mm * .75)
        return [position[0] + self.length_mm / 8, position[1] + self.width_mm / 8]

    def capture_probe_reference(self, payload):
        with self.lock:
            if (not isinstance(payload, dict) or set(payload) != {"point_index", "contact_confirmed", "fixed_pose_confirmed"} or
                    type(payload["point_index"]) is not int or payload["point_index"] != len(self.probe_samples) or
                    payload["point_index"] >= 5 or payload["contact_confirmed"] is not True or payload["fixed_pose_confirmed"] is not True):
                raise ValueError("Confirm the next contact point and keep the probe upright with its marker fixed")
            state = self.state()
            if (not state.get("preview_available") or not state.get("calibrated") or self.selection is not None or
                    state.get("marker", {}).get("status") != "FOUND" or state["frame"]["age_ms"] > 300 or not self._probe_stable()):
                raise ValueError("Calibrate the surface, resume live preview, and hold one fresh red marker still for at least 350 ms")
            pixel = [sum(entry[2][axis] for entry in self.marker_history) / len(self.marker_history) for axis in range(2)]
            sample = {"contact_mm": self._probe_targets()[len(self.probe_samples)], "marker_pixel": pixel,
                      "camera_frame_ids": [entry[0] for entry in self.marker_history]}
            if len(self.probe_samples) == 3:
                transform = solve_homography([entry["marker_pixel"] for entry in self.probe_samples] + [pixel],
                                             self.latest.width, self.latest.height)
                self.probe_transform = transform
            elif len(self.probe_samples) == 4:
                error = math.dist(self._probe_position(pixel, self.latest), sample["contact_mm"])
                if error > 2.0:
                    raise ValueError(f"Independent center check failed ({error:.2f} mm > 2 mm); reset probe registration and check the marks and pose")
                sample["error_mm"] = round(error, 3)
                self.registration_id = secrets.token_hex(16)
            self.probe_samples.append(sample)
            self.marker_history.clear()
            return self.state()

    def reset_probe_reference(self):
        with self.lock:
            self._reset_probe()
            return self.state()

    def reference_overlay(self, specimen, h1_end):
        from .specimen_reference import hole_reference
        with self.lock:
            reference = hole_reference(specimen, h1_end, self.length_mm, self.width_mm)
            state = self.state()
            reference["projected"] = bool(state.get("preview_available") and state.get("calibrated"))
            reference["camera_frame_id"] = state.get("frame", {}).get("frame_id")
            reference["surface_calibration_id"] = state.get("surface_calibration_id")
            if reference["projected"]:
                for hole in reference["holes"]:
                    hole["surface_pixel"] = unproject_point(self.transform, hole["position_mm"], self.latest.width, self.latest.height,
                                                             self.length_mm, self.width_mm)
            return reference

    def frame_png(self, frame_id=None):
        with self.lock:
            frame, now = self._current()
            if frame_id is not None and frame_id != frame.frame_id:
                frame = self.selection if self.selection and self.selection.frame_id == frame_id else self.frames.get(frame_id)
                if frame is None or not 0 <= now - frame.captured_ns <= SELECTION_AGE_NS:
                    raise ValueError("Requested camera image has expired; refresh the preview")
            if self.png_key != frame.frame_id:
                self.png, self.png_key = png_bytes(frame), frame.frame_id
            return self.png

    def local_preview(self, width=320, height=240):
        with self.lock:
            current, now = self._current()
            frame = self.selection if self.selection is not None and 0 <= now - self.selection_created_ns <= SELECTION_AGE_NS else current
            output = bytearray(width * height * 4)
            for row in range(height):
                source_row = min(frame.height - 1, row * frame.height // height)
                source = frame.pixels[source_row * frame.width * 3:(source_row + 1) * frame.width * 3]
                if frame.width % width == 0:
                    stride = frame.width // width * 3
                    red, green, blue = source[0::stride], source[1::stride], source[2::stride]
                else:
                    columns = [column * frame.width // width * 3 for column in range(width)]
                    red = bytes(source[column] for column in columns)
                    green = bytes(source[column + 1] for column in columns)
                    blue = bytes(source[column + 2] for column in columns)
                start, end = row * width * 4, (row + 1) * width * 4
                output[start:end:4] = blue
                output[start + 1:end:4] = green
                output[start + 2:end:4] = red
            return bytes(output), frame.metadata(now)

    def freeze(self):
        with self.lock:
            frame, now = self._current()
            self.selection = frame
            self.selection_created_ns = now
            self.calibration_id = secrets.token_hex(16)
            return self.state()

    def resume(self):
        with self.lock:
            self.selection = self.calibration_id = None
            self.marker_history.clear()
            return self.state()

    def calibrate(self, payload):
        with self.lock:
            frame, now = self._current()
            if payload.get("fixed_confirmed") is not True:
                raise ValueError("Confirm the camera and steel block are fixed before calibration")
            if not self.selection or payload.get("calibration_id") != self.calibration_id or not 0 <= now - self.selection_created_ns <= SELECTION_AGE_NS:
                raise ValueError("Freeze a fresh image and select all four corners again")
            if (frame.stream_id, frame.source, frame.width, frame.height) != (self.selection.stream_id, self.selection.source, self.selection.width, self.selection.height):
                raise ValueError("Camera stream changed; restart calibration")
            corners = payload.get("corners")
            transform = solve_homography(corners, frame.width, frame.height)
            self.transform, self.corners = transform, [list(corner) for corner in corners]
            self.surface_calibration_id = secrets.token_hex(16)
            self._reset_probe()
            self.selection = self.calibration_id = None
            return self.state()

    def clear(self):
        with self.lock:
            self.transform = self.corners = self.selection = self.calibration_id = None
            self.surface_calibration_id = None
            self._reset_probe()
            return self.state()
