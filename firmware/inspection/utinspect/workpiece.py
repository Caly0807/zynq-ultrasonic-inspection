import math
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from .vision import atomic_json


@dataclass(frozen=True)
class WorkpieceSpec:
    length_mm: float = 80.0
    width_mm: float = 80.0
    thickness_mm: float = 30.0
    dimensions_confirmed: bool = False

    def __post_init__(self):
        if not all(math.isfinite(value) and value > 0 for value in (self.length_mm, self.width_mm, self.thickness_mm)):
            raise ValueError("Workpiece dimensions must be positive and finite")
        if not isinstance(self.dimensions_confirmed, bool):
            raise ValueError("Dimension confirmation must be a boolean")


def order_corners(points):
    vertices = np.asarray(points, np.float32)
    if vertices.shape != (4, 2) or not np.isfinite(vertices).all():
        raise ValueError("Select four finite workpiece corners")
    hull = cv2.convexHull(vertices).reshape(-1, 2)
    if len(hull) != 4 or cv2.contourArea(hull) < 64:
        raise ValueError("The four corners must form a non-degenerate convex face")
    center = hull.mean(axis=0)
    angles = np.arctan2(hull[:, 1] - center[1], hull[:, 0] - center[0])
    hull = hull[np.argsort(angles)]
    start = int(np.argmin(hull[:, 0] + hull[:, 1]))
    return np.roll(hull, -start, axis=0)


class PlanarFace:
    def __init__(self, corners, image_shape, spec):
        self.corners = order_corners(corners)
        self.height, self.width = image_shape[:2]
        if np.any(self.corners < 0) or np.any(self.corners[:, 0] >= self.width) or np.any(self.corners[:, 1] >= self.height):
            raise ValueError("Workpiece corners must lie inside the image")
        self.spec = spec
        destination = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32)
        self.transform = cv2.getPerspectiveTransform(self.corners, destination)
        if not np.isfinite(self.transform).all() or abs(np.linalg.det(self.transform)) < 1e-12:
            raise ValueError("The selected workpiece plane is singular")

    def normalized(self, point):
        pixel = np.asarray(point, np.float32)
        if pixel.shape != (2,) or not np.isfinite(pixel).all():
            raise ValueError("Probe point must contain two finite image coordinates")
        if cv2.pointPolygonTest(self.corners, tuple(float(value) for value in pixel), False) < 0:
            return None
        projected = cv2.perspectiveTransform(pixel.reshape(1, 1, 2), self.transform)[0, 0]
        return tuple(float(np.clip(value, 0, 1)) for value in projected)

    def position_mm(self, point, contact_on_plane=False):
        normalized = self.normalized(point)
        if normalized is None or not contact_on_plane or not self.spec.dimensions_confirmed:
            return None
        return (normalized[0] * self.spec.length_mm, normalized[1] * self.spec.width_mm)

    def describe(self):
        return {"corners_px": self.corners.tolist(), "image_width": self.width, "image_height": self.height,
                "spec": asdict(self.spec), "coordinate_system": "IMAGE_TOP_LEFT_X_RIGHT_Y_DOWN_NOT_STAGE_COORDINATES",
                "accuracy": "UNCALIBRATED_PLANAR_ESTIMATE", "requires_fixed_camera_and_workpiece": True}


def sharpness(image):
    return float(cv2.Laplacian(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def find_workpieces(image, focus_min=12.0):
    if sharpness(image) < focus_min:
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    image_area = image.shape[0] * image.shape[1]
    candidates = []
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, perimeter * 0.025, True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            continue
        area = float(cv2.contourArea(polygon))
        if not 0.02 * image_area <= area <= 0.85 * image_area:
            continue
        corners = order_corners(polygon.reshape(4, 2))
        sides = np.linalg.norm(corners - np.roll(corners, -1, axis=0), axis=1)
        aspect = float((sides[0] + sides[2]) / (sides[1] + sides[3]))
        if not 0.45 <= aspect <= 2.2:
            continue
        rectangle = cv2.minAreaRect(corners)
        rectangle_area = rectangle[1][0] * rectangle[1][1]
        fill = area / max(rectangle_area, 1)
        if fill < 0.65:
            continue
        score = math.sqrt(area / image_area) * fill * math.exp(-abs(math.log(aspect)))
        candidates.append({"corners_px": corners.tolist(), "score": score, "area_px": area})
    distinct = []
    for candidate in sorted(candidates, key=lambda item: item["score"], reverse=True):
        corners = np.array(candidate["corners_px"], np.float32)
        if any(float(np.mean(np.linalg.norm(corners - np.array(other["corners_px"]), axis=1))) < 12 for other in distinct):
            continue
        distinct.append(candidate)
    return distinct[:5]


@dataclass(frozen=True)
class VisualSettings:
    probe_mode: str = "red"
    focus_min: float = 12.0
    red_saturation_min: int = 130
    red_value_min: int = 65
    red_min_area_px: int = 6
    round_min_radius_fraction: float = 0.06
    round_max_radius_fraction: float = 0.22
    round_vote_threshold: float = 32.0

    def __post_init__(self):
        if self.probe_mode not in ("red", "round", "manual"):
            raise ValueError("Choose red, round or manual probe localization")
        for name in ("red_saturation_min", "red_value_min", "red_min_area_px"):
            value = getattr(self, name)
            maximum = 10000 if name == "red_min_area_px" else 255
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"Invalid {name}")
        for name in ("focus_min", "round_min_radius_fraction", "round_max_radius_fraction", "round_vote_threshold"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError(f"Invalid {name}")
        if not 0 <= self.focus_min <= 10000 or not 8 <= self.round_vote_threshold <= 100:
            raise ValueError("Invalid focus or circle threshold")
        if not 0.01 <= self.round_min_radius_fraction < self.round_max_radius_fraction <= 0.4:
            raise ValueError("Circle radius fractions must satisfy 0.01 <= min < max <= 0.4")


@dataclass(frozen=True)
class ProbePoint:
    pixel: tuple[float, float]
    method: str
    contact_on_plane: bool = False

    def __post_init__(self):
        if len(self.pixel) != 2 or not all(math.isfinite(value) for value in self.pixel):
            raise ValueError("Probe pixel must contain two finite coordinates")
        if self.method not in ("RED_STICKER", "MANUAL_CONTACT", "ROUND_BODY"):
            raise ValueError("Unknown probe localization method")
        if not isinstance(self.contact_on_plane, bool):
            raise ValueError("Probe contact confirmation must be a boolean")
        if self.method == "ROUND_BODY" and self.contact_on_plane:
            raise ValueError("A raised circular probe body is not a contact-plane marker")


def find_red_probe(image, face, contact_on_plane=False, settings=None):
    settings = settings or VisualSettings()
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lower = (settings.red_saturation_min, settings.red_value_min)
    red = cv2.inRange(hsv, (0, *lower), (10, 255, 255)) | cv2.inRange(hsv, (170, *lower), (179, 255, 255))
    region = np.zeros(image.shape[:2], np.uint8)
    cv2.fillConvexPoly(region, face.corners.astype(np.int32), 255)
    mask = cv2.morphologyEx(red & region, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask)
    maximum_area = max(30, cv2.contourArea(face.corners) * 0.02)
    points = [tuple(float(value) for value in centroids[index]) for index in range(1, count)
              if settings.red_min_area_px <= stats[index, cv2.CC_STAT_AREA] <= maximum_area]
    if len(points) != 1:
        return None, "red_sticker_missing" if not points else "multiple_red_stickers_ambiguous"
    return ProbePoint(points[0], "RED_STICKER", contact_on_plane), "ok"


def find_probe(image, face, settings=None, contact_on_plane=False):
    settings = settings or VisualSettings()
    if settings.probe_mode == "manual":
        return None, "manual_mode", []
    if sharpness(image) < settings.focus_min:
        return None, "frame_blurred", []
    if settings.probe_mode == "red":
        probe, reason = find_red_probe(image, face, contact_on_plane, settings)
        return probe, reason, [{"pixel": list(probe.pixel), "method": probe.method}] if probe else []
    left, top, width, height = cv2.boundingRect(face.corners)
    gray = cv2.cvtColor(image[top:top + height, left:left + width], cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (7, 7), 1.5)
    scale = math.sqrt(cv2.contourArea(face.corners))
    minimum = max(4, int(scale * settings.round_min_radius_fraction))
    maximum = max(minimum + 1, int(scale * settings.round_max_radius_fraction))
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, 1.2, max(10, scale * 0.16),
                               param1=100, param2=settings.round_vote_threshold,
                               minRadius=minimum, maxRadius=maximum)
    candidates = []
    if circles is not None:
        for center_x, center_y, radius in circles[0]:
            pixel = (float(center_x + left), float(center_y + top))
            if cv2.pointPolygonTest(face.corners, pixel, True) < float(radius) * 0.65:
                continue
            candidates.append({"pixel": list(pixel), "radius_px": float(radius), "method": "ROUND_BODY"})
    if len(candidates) != 1:
        return None, "round_probe_missing" if not candidates else "multiple_round_probes_ambiguous", candidates
    return ProbePoint(tuple(candidates[0]["pixel"]), "ROUND_BODY", False), "body_center_not_contact", candidates


def draw_point(image, pixel, label, color):
    center = tuple(int(round(value)) for value in pixel)
    cv2.circle(image, center, 12, color, 2, cv2.LINE_AA)
    cv2.drawMarker(image, center, color, cv2.MARKER_CROSS, 30, 2)
    text_width = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 2)[0][0]
    origin = (max(4, min(center[0] + 17, image.shape[1] - text_width - 4)), max(22, center[1] - 18))
    cv2.putText(image, label, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 2, cv2.LINE_AA)


def annotate(image, face=None, probe=None, label="LIVE CAMERA / NO ULTRASOUND CONNECTED", candidates=()):
    output = image.copy()
    for candidate in candidates[:1]:
        cv2.polylines(output, [np.array(candidate["corners_px"], np.int32)], True, (0, 190, 230), 2, cv2.LINE_AA)
    if face is not None:
        cv2.polylines(output, [face.corners.astype(np.int32)], True, (220, 210, 30), 2, cv2.LINE_AA)
        for index, corner in enumerate(face.corners):
            cv2.putText(output, str(index + 1), tuple(corner.astype(int)), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (220, 210, 30), 2)
    if probe is not None:
        draw_point(output, probe.pixel, probe.method, (40, 175, 255))
    cv2.rectangle(output, (0, 0), (output.shape[1], 33), (24, 24, 24), -1)
    cv2.putText(output, label, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (230, 235, 240), 1, cv2.LINE_AA)
    return output


def top_view(image, face=None, probe=None, marks=(), size=480):
    if face is None:
        output = np.full((size, size, 3), (28, 22, 17), np.uint8)
        cv2.putText(output, "SELECT WORKPIECE FACE", (55, size // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (170, 180, 190), 1)
        return output
    destination = np.array([[0, 0], [size - 1, 0], [size - 1, size - 1], [0, size - 1]], np.float32)
    output = cv2.warpPerspective(image, cv2.getPerspectiveTransform(face.corners, destination), (size, size))
    for fraction in (0.25, 0.5, 0.75):
        offset = int(fraction * (size - 1))
        cv2.line(output, (offset, 0), (offset, size - 1), (140, 160, 170), 1)
        cv2.line(output, (0, offset), (size - 1, offset), (140, 160, 170), 1)
    for mark in marks:
        normalized = mark.get("apparent_face_uv")
        if normalized is not None:
            pixel = tuple(value * (size - 1) for value in normalized)
            draw_point(output, pixel, mark["kind"], (45, 60, 240) if mark["kind"] == "LIVE_SUSPECT" else (40, 165, 255))
    if probe is not None:
        normalized = face.normalized(probe.pixel)
        already_marked = normalized is not None and any(
            mark.get("apparent_face_uv") is not None and np.allclose(mark["apparent_face_uv"], normalized, atol=1 / size)
            for mark in marks)
        if normalized is not None and not already_marked:
            draw_point(output, tuple(value * (size - 1) for value in normalized), "PROBE", (230, 220, 30))
    cv2.rectangle(output, (0, size - 28), (size, size), (24, 24, 24), -1)
    cv2.putText(output, "PLANAR ESTIMATE / NOT METROLOGY", (10, size - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.47, (230, 235, 240), 1)
    return output


def save_result(directory, image, face=None, probe=None, image_source="RECORDED", diagnosis=None,
                manual_mark=False, frame_id="", frame_timestamp_ns=0, marks=(), event_id=None,
                frozen=False, visual_settings=None):
    if image_source not in ("LIVE_CAMERA", "RECORDED", "DEMO"):
        raise ValueError("Invalid image provenance")
    if diagnosis is not None and manual_mark:
        raise ValueError("A manual mark cannot masquerade as a diagnosis")
    kind = "MANUAL_TEST_NOT_ULTRASOUND" if manual_mark else "CAMERA_SNAPSHOT"
    if diagnosis is not None:
        if not isinstance(diagnosis, dict):
            raise ValueError("A diagnosis object is required")
        source, status = diagnosis.get("source"), diagnosis.get("status")
        if source not in ("LIVE", "RECORDED", "DEMO") or status not in ("SUSPECT", "DEMO_SUSPECT"):
            raise ValueError("Only a provenance-labelled suspect diagnosis can produce a defect marker")
        if (source == "DEMO") != status.startswith("DEMO_"):
            raise ValueError("DEMO diagnosis must keep its DEMO status")
        identifiers = (diagnosis.get("calibration_id"), diagnosis.get("frame_id"))
        if diagnosis.get("calibrated") is not True or any(not isinstance(value, str) or not value.strip() for value in identifiers):
            raise ValueError("Ultrasonic diagnosis requires calibration and an acquisition frame ID")
        if event_id is not None and (not isinstance(event_id, str) or not 1 <= len(event_id) <= 128):
            raise ValueError("A bounded diagnosis event_id is required")
        if source == "LIVE":
            age = time.monotonic_ns() - frame_timestamp_ns
            if frozen or image_source != "LIVE_CAMERA" or age < 0 or age > 2_000_000_000 or not frame_id:
                raise ValueError("LIVE diagnosis requires its exact fresh live camera frame")
            if face is None or probe is None or probe.method != "RED_STICKER" or not probe.contact_on_plane or not face.spec.dimensions_confirmed:
                raise ValueError("LIVE defect binding needs a confirmed workpiece and contact-plane probe marker")
        kind = source + "_SUSPECT"
    if manual_mark or diagnosis is not None:
        if face is None or probe is None or face.normalized(probe.pixel) is None:
            raise ValueError("A defect/test marker requires a probe point inside the selected face")
    record = {"schema": 1, "kind": kind, "image_source": image_source, "camera_frame_id": frame_id,
              "event_id": event_id,
              "camera_received_monotonic_ns": frame_timestamp_ns, "saved_unix_ns": time.time_ns(),
              "face": face.describe() if face else None, "probe": asdict(probe) if probe else None,
              "apparent_face_uv": face.normalized(probe.pixel) if face and probe else None,
              "plane_position_mm": face.position_mm(probe.pixel, probe.contact_on_plane) if face and probe else None,
              "accuracy": "UNCALIBRATED_PLANAR_ESTIMATE", "diagnosis": diagnosis,
              "frozen_frame": bool(frozen), "visual_settings": asdict(visual_settings) if visual_settings else None,
              "transmit_enabled_by_this_software": False, "motor_outputs_implemented": False}
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    identifier = f"capture_{time.time_ns()}_{uuid.uuid4().hex[:6]}"
    record["files"] = {"raw": identifier + "_raw.png", "annotated": identifier + "_annotated.png",
                       "top": identifier + "_top.png", "json": identifier + ".json"}
    provenance = image_source + " / " + kind
    annotated = annotate(image, face, probe if kind == "CAMERA_SNAPSHOT" else None, provenance)
    if probe is not None and kind != "CAMERA_SNAPSHOT":
        draw_point(annotated, probe.pixel, kind, (45, 60, 240) if kind == "LIVE_SUSPECT" else (40, 165, 255))
    projected = top_view(image, face, probe, [*marks, record] if kind != "CAMERA_SNAPSHOT" else marks)
    cv2.rectangle(projected, (0, 0), (projected.shape[1], 26), (24, 24, 24), -1)
    cv2.putText(projected, provenance, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (230, 235, 240), 1, cv2.LINE_AA)
    rendered = {"raw": image, "annotated": annotated, "top": projected}
    for name, picture in rendered.items():
        success, encoded = cv2.imencode(".png", picture)
        if not success:
            raise OSError("Failed to encode inspection image")
        encoded.tofile(str(output / record["files"][name]))
    atomic_json(output / record["files"]["json"], record)
    return record
