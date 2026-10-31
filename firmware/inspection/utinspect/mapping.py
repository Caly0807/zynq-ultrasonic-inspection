import csv
import hashlib
import io
import json
import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .echo import EchoConfig, Waveform, analyze, finite_number, load_csv, write_lvgl_csv
from .records import atomic_json


MAX_POINTS = 2048
STATUS_PRIORITY = {"UNSCANNED": 0, "NO_INDICATION": 1, "INVALID": 2, "UNCALIBRATED": 3, "SUSPECT": 4}


def validate_submission_id(value):
    if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 96 or
                              any(not character.isascii() or not (character.isalnum() or character in "-_") for character in value)):
        raise ValueError("Submission ID must contain 1..96 ASCII letters, digits, hyphens or underscores")


@dataclass(frozen=True)
class MapSpec:
    length_mm: float = 80.0
    width_mm: float = 80.0
    thickness_mm: float = 30.0
    grid_mm: float = 5.0
    normal_incidence_confirmed: bool = False

    def __post_init__(self):
        for value in (self.length_mm, self.width_mm, self.thickness_mm, self.grid_mm):
            if not finite_number(value) or not 0 < value <= 10000:
                raise ValueError("Map dimensions and grid must be finite positive millimetres")
        if type(self.normal_incidence_confirmed) is not bool:
            raise ValueError("Normal-incidence confirmation must be boolean")
        if self.columns > 32 or self.rows > 32:
            raise ValueError("Map supports at most 32 by 32 cells; increase grid_mm")

    @property
    def columns(self):
        return math.ceil(self.length_mm / self.grid_mm)

    @property
    def rows(self):
        return math.ceil(self.width_mm / self.grid_mm)

    def cell(self, position_x, position_y):
        if not all(finite_number(value) for value in (position_x, position_y)):
            raise ValueError("Position needs two finite workpiece coordinates")
        if not 0 <= position_x <= self.length_mm or not 0 <= position_y <= self.width_mm:
            raise ValueError("Manual position is outside the workpiece")
        return min(self.columns - 1, int(position_x / self.grid_mm)), min(self.rows - 1, int(position_y / self.grid_mm))

    def center(self, column, row):
        return ((column * self.grid_mm + min((column + 1) * self.grid_mm, self.length_mm)) / 2,
                (row * self.grid_mm + min((row + 1) * self.grid_mm, self.width_mm)) / 2)


def normalized_status(diagnosis):
    status = diagnosis["status"].removeprefix("DEMO_")
    if status not in STATUS_PRIORITY or status == "UNSCANNED":
        raise ValueError("Unknown mapping diagnostic status")
    return status


def envelope(frame, bins=256):
    stride = max(1, math.ceil(len(frame.samples) / bins))
    return {"bins": [[min(frame.samples[start:start + stride]), max(frame.samples[start:start + stride])]
                     for start in range(0, len(frame.samples), stride)],
            "stride": stride, "sample_count": len(frame.samples), "trigger_index": frame.trigger_index,
            "sample_period_us": frame.sample_period_us, "representation": "MIN_MAX_ENVELOPE_NOT_RAW_OSCILLATION"}


class MapSession:
    def __init__(self, directory, spec=None, echo=None, mode="RECORDED"):
        if mode not in ("DEMO", "RECORDED", "LIVE"):
            raise ValueError("Unknown map acquisition mode")
        self.root = Path(directory).resolve()
        self.spec = spec or MapSpec()
        self.echo = echo or EchoConfig()
        self.mode = mode
        if mode != "DEMO" and self.echo.calibration_id.startswith("DEMO"):
            raise ValueError("DEMO calibration cannot authorize real measurements")
        self.session_id = uuid.uuid4().hex
        self.created_unix_ns = time.time_ns()
        self.points = []
        self.frame_ids = set()
        self.submission_ids = set()
        self.bridge_error = ""
        self.display_directory = None
        self.lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=False)
        (self.root / "waveforms").mkdir()
        self._commit()

    def add(self, frame, position_x, position_y, position_confirmed=False, *, submission_id=None, visual_evidence=None):
        with self.lock:
            validate_submission_id(submission_id)
            if submission_id is not None and submission_id in self.submission_ids:
                raise ValueError("The same submission cannot create another map point")
            self.spec.cell(position_x, position_y)
            if position_confirmed is not True:
                raise ValueError("Explicitly confirm the probe is at the entered position")
            if frame.source != self.mode:
                raise ValueError("Acquisition provenance cannot be mixed or promoted within a map session")
            if len(frame.samples) != 4096:
                raise ValueError("Map points require a complete 4096-sample waveform")
            if not isinstance(frame.frame_id, str) or not 1 <= len(frame.frame_id) <= 160:
                raise ValueError("A bounded acquisition frame ID is required")
            if frame.frame_id in self.frame_ids:
                raise ValueError("The same acquisition frame cannot be painted at multiple positions")
            if len(self.points) >= MAX_POINTS:
                raise ValueError("Map point capacity reached; start another session")
            if frame.source == "LIVE":
                age = time.monotonic_ns() - (frame.acquired_monotonic_ns or 0)
                if frame.acquired_monotonic_ns is None or not 0 <= age <= 2_000_000_000:
                    raise ValueError("LIVE mapping requires a fresh same-host acquisition timestamp")
            if visual_evidence is not None:
                self._validate_visual_evidence(frame, visual_evidence)
            diagnosis = analyze(frame, self.echo)
            status = normalized_status(diagnosis)
            depth = diagnosis["depth_mm"]
            depth_reason = "NO_CALIBRATED_SUSPECT"
            if status == "SUSPECT" and depth is not None:
                if not self.spec.normal_incidence_confirmed:
                    depth, depth_reason = None, "NORMAL_INCIDENCE_NOT_CONFIRMED"
                elif not 0 < depth < self.spec.thickness_mm:
                    depth, depth_reason = None, "OUTSIDE_WORKPIECE_THICKNESS"
                else:
                    depth_reason = "CALIBRATED_NORMAL_INCIDENCE_REFLECTOR_DEPTH"
            else:
                depth = None
            point_id = uuid.uuid4().hex
            relative = "waveforms/" + point_id + ".csv"
            waveform_path = self.root / relative
            write_lvgl_csv(waveform_path, frame)
            record = {"point_id": point_id, "index": len(self.points), "frame_id": frame.frame_id, "submission_id": submission_id,
                      "source": frame.source, "position_mm": [float(position_x), float(position_y)],
                      "position_basis": "CAMERA_MARKER_ESTIMATE_UNVERIFIED" if visual_evidence else "OPERATOR_ENTERED_WORKPIECE_MM_UNVERIFIED",
                      "position_confirmed": True, "position_accuracy_mm": None,
                      "created_unix_ns": time.time_ns(), "diagnosis": diagnosis, "status": status,
                      "reflector_depth_mm": depth,
                      "distance_from_bottom_mm": self.spec.thickness_mm - depth if depth is not None else None,
                      "depth_reason": depth_reason, "hole_height_mm": None, "hole_diameter_mm": None,
                      "waveform": relative, "waveform_sha256": hashlib.sha256(waveform_path.read_bytes()).hexdigest(),
                      "preview": envelope(frame)}
            if visual_evidence is not None:
                record["visual_evidence"] = dict(visual_evidence)
                record["position_confirmed"] = False
                record["continuous_tracking_operator_confirmed"] = True
            self.points.append(record)
            self.frame_ids.add(frame.frame_id)
            if submission_id is not None:
                self.submission_ids.add(submission_id)
            try:
                self._commit()
            except Exception:
                self.points.pop()
                self.frame_ids.remove(frame.frame_id)
                if submission_id is not None:
                    self.submission_ids.remove(submission_id)
                raise
            return record

    @staticmethod
    def _validate_visual_evidence(frame, evidence):
        if (frame.source != "LIVE" or not isinstance(evidence, dict) or
                set(evidence) != {"camera_frame_id", "camera_monotonic_ns", "adc_monotonic_ns", "skew_ms", "stable_ms", "timing_basis"}):
            raise ValueError("Visual mapping needs bounded real-camera timing evidence")
        camera_id = evidence["camera_frame_id"]
        camera_time, adc_time = evidence["camera_monotonic_ns"], evidence["adc_monotonic_ns"]
        if (not isinstance(camera_id, str) or not camera_id.startswith("USB_CAMERA-") or len(camera_id) > 160 or
                type(camera_time) is not int or type(adc_time) is not int or camera_time <= 0 or
                adc_time != frame.acquired_monotonic_ns or abs(camera_time - adc_time) > 200_000_000 or
                not all(finite_number(evidence[name]) for name in ("skew_ms", "stable_ms")) or
                abs(evidence["skew_ms"] - abs(camera_time - adc_time) / 1_000_000) > .001 or
                not 350 <= evidence["stable_ms"] <= 60_000 or
                evidence["timing_basis"] != "DEQUEUE_TIMESTAMPS_NOT_SENSOR_SYNCHRONIZED"):
            raise ValueError("Camera and ADC evidence is stale, moving or not time-associated")

    def snapshot(self):
        with self.lock:
            cells = [{"column": column, "row": row, "center_mm": self.spec.center(column, row),
                      "status": "UNSCANNED", "count": 0, "point_ids": [], "depths_mm": [], "selected_point_id": None}
                     for row in range(self.spec.rows) for column in range(self.spec.columns)]
            for point in self.points:
                column, row = self.spec.cell(*point["position_mm"])
                cell = cells[row * self.spec.columns + column]
                if STATUS_PRIORITY[point["status"]] >= STATUS_PRIORITY[cell["status"]]:
                    cell["status"] = point["status"]
                    cell["selected_point_id"] = point["point_id"]
                cell["count"] += 1
                cell["point_ids"].append(point["point_id"])
                if point["reflector_depth_mm"] is not None:
                    cell["depths_mm"].append(point["reflector_depth_mm"])
            visited = sum(cell["count"] > 0 for cell in cells)
            return {"schema": "ut-manual-map/1", "session_id": self.session_id,
                    "created_unix_ns": self.created_unix_ns, "source": self.mode, "spec": asdict(self.spec),
                    "echo": asdict(self.echo), "columns": self.spec.columns, "rows": self.spec.rows,
                    "position_basis": "CAMERA_MARKER_ESTIMATE_UNVERIFIED" if any("visual_evidence" in point for point in self.points) else "OPERATOR_ENTERED_WORKPIECE_MM_UNVERIFIED",
                    "coverage_basis": "SAMPLED_GRID_CELLS_NOT_PHYSICAL_BEAM_COVERAGE",
                    "point_count": len(self.points), "sampled_cells": visited, "total_cells": len(cells),
                    "grid_coverage_percent": visited * 100.0 / len(cells),
                    "valid_cells": sum(cell["status"] in ("NO_INDICATION", "SUSPECT") for cell in cells),
                    "suspect_cells": sum(cell["status"] == "SUSPECT" for cell in cells),
                    "invalid_cells": sum(cell["status"] == "INVALID" for cell in cells),
                    "depth_point_count": sum(point["reflector_depth_mm"] is not None for point in self.points),
                    "hardware_control": False, "transmit_enabled_by_this_software": False, "bridge_error": self.bridge_error,
                    "reconstruction": "INDICATION_POINTS_ONLY_NOT_HOLE_SHAPE_OR_CT",
                    "cells": cells, "points": list(self.points)}

    def _commit(self):
        snapshot = self.snapshot()
        temporary = self.root / "map.csv.tmp"
        temporary.write_text(embedded_csv(snapshot), encoding="ascii")
        atomic_json(self.root / "session.json", snapshot)
        try:
            temporary.replace(self.root / "map.csv")
            self.bridge_error = ""
        except OSError:
            self.bridge_error = "Display snapshot update failed; session.json remains authoritative"
        if self.display_directory is not None:
            try:
                self._publish_display(snapshot)
            except OSError:
                self.bridge_error = "Local LCD publication failed; session.json remains authoritative"

    def bind_local_display(self, directory):
        with self.lock:
            self.display_directory = Path(directory).resolve()
            self.display_directory.mkdir(parents=True, exist_ok=True)
            self._publish_display(self.snapshot())

    def begin_new(self):
        with self.lock:
            incoming = MapSession(self.root.parent / ("session_" + uuid.uuid4().hex), self.spec, self.echo, self.mode)
            if self.display_directory is not None:
                incoming.bind_local_display(self.display_directory)
            for name in ("root", "session_id", "created_unix_ns", "points", "frame_ids", "submission_ids", "bridge_error"):
                setattr(self, name, getattr(incoming, name))
            return self.snapshot()

    def _publish_display(self, snapshot):
        destination = self.display_directory
        if snapshot["points"]:
            source = self.root / snapshot["points"][-1]["waveform"]
            temporary = destination / "waveform.csv.tmp"
            temporary.write_bytes(source.read_bytes())
            temporary.replace(destination / "waveform.csv")
        else:
            (destination / "waveform.csv").unlink(missing_ok=True)
        temporary = destination / "map.csv.tmp"
        temporary.write_text(embedded_csv(snapshot), encoding="ascii")
        temporary.replace(destination / "map.csv")

    def waveform_csv(self, point_id):
        with self.lock:
            point = next((item for item in self.points if item["point_id"] == point_id), None)
            if point is None:
                raise ValueError("Unknown point ID")
            data = (self.root / point["waveform"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != point["waveform_sha256"]:
                raise ValueError("Original waveform has changed; refusing an unverified replacement")
            return data

    def waveform(self, point_id):
        with self.lock:
            point = next((item for item in self.points if item["point_id"] == point_id), None)
            if point is None:
                raise ValueError("Unknown point ID")
            path = self.root / point["waveform"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != point["waveform_sha256"]:
                raise ValueError("Original waveform has changed; refusing to display an unverified replacement")
            frame = load_csv(path)
            return {"source": point["source"], "frame_id": point["frame_id"], "samples": frame.samples,
                    "trigger_index": frame.trigger_index, "sample_period_us": frame.sample_period_us,
                    "diagnosis": point["diagnosis"]}


def embedded_csv(snapshot):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    spec = snapshot["spec"]
    writer.writerow(["UTMAP", 1, snapshot["columns"], snapshot["rows"], spec["length_mm"], spec["width_mm"],
                     spec["thickness_mm"], spec["grid_mm"], snapshot["source"], "MANUAL_UNVERIFIED"])
    for cell in snapshot["cells"]:
        nearest = min(cell["depths_mm"]) if cell["depths_mm"] else None
        writer.writerow(["CELL", cell["column"], cell["row"], cell["status"], cell["count"],
                         f"{nearest:.6f}" if nearest is not None else ""])
    return stream.getvalue()


def synthetic_map_waveform(position_x, position_y, spec):
    first = math.hypot(position_x - spec.length_mm * 0.35, position_y - spec.width_mm * 0.38) < 11
    second = math.hypot(position_x - spec.length_mm * 0.72, position_y - spec.width_mm * 0.68) < 8
    bad_coupling = position_x < spec.length_mm * 0.15 and position_y > spec.width_mm * 0.85
    depth = 12 + 0.08 * (position_x - 28) if first else 20.0
    samples = []
    for index in range(4096):
        sample_time = (index - 256) * 0.04
        value = 128 + math.sin(index * 1.71)
        for echo_time, amplitude in ((spec.thickness_mm / 2.95, 2 if bad_coupling else 65),
                                     (depth / 2.95, 52 if first or second else 2)):
            distance = sample_time - echo_time
            value += amplitude * math.exp(-distance * distance / 0.04) * math.cos(distance * 24)
        samples.append(round(max(0, min(255, value))))
    return Waveform(tuple(samples), 256, 0.04, "DEMO", frame_id="demo-map-" + uuid.uuid4().hex)


def demo_session(directory, complete=True):
    spec = MapSpec(normal_incidence_confirmed=True)
    echo = replace(EchoConfig(), calibrated=True, calibration_id="DEMO_ONLY_NOT_FOR_MEASUREMENT")
    session = MapSession(directory, spec, echo, "DEMO")
    for row in range(spec.rows):
        columns = range(spec.columns) if row % 2 == 0 else reversed(range(spec.columns))
        for column in columns:
            if not complete and row >= 11 and column >= 8:
                continue
            position_x, position_y = spec.center(column, row)
            session.add(synthetic_map_waveform(position_x, position_y, spec), position_x, position_y, True)
    return session


def render_dashboard(snapshot):
    template = (Path(__file__).resolve().parents[1] / "web/inspection.html").read_text(encoding="utf-8")
    payload = json.dumps(snapshot, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return template.replace('<script id="boot" type="application/json">null</script>',
                            '<script id="boot" type="application/json">' + payload + '</script>')
