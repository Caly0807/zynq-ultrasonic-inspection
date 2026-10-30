import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .records import Observation, atomic_json


@dataclass(frozen=True)
class Intrinsics:
    matrix: np.ndarray
    distortion: np.ndarray
    width: int
    height: int

    def __post_init__(self):
        if self.matrix.shape != (3, 3) or not np.isfinite(self.matrix).all():
            raise ValueError("Camera matrix must be finite and 3x3")
        if self.matrix[0, 0] <= 0 or self.matrix[1, 1] <= 0:
            raise ValueError("Camera focal lengths must be positive")
        if self.distortion.size not in (4, 5, 8, 12, 14) or not np.isfinite(self.distortion).all():
            raise ValueError("Invalid distortion coefficients")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Invalid calibration image size")

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(np.array(data["camera_matrix"], dtype=float), np.array(data["distortion"], dtype=float), int(data["width"]), int(data["height"]))


class Tracker:
    def __init__(self, intrinsics=None, reference_id=0, probe_id=1, reference_size_mm=30.0,
                 probe_size_mm=30.0, tip_offset_mm=None, max_error_px=1.5, contact_tolerance_mm=3.0):
        if not 0 <= reference_id < 50 or not 0 <= probe_id < 50 or reference_id == probe_id:
            raise ValueError("Use distinct reference/probe IDs from DICT_4X4_50")
        if not all(math.isfinite(value) and value > 0 for value in (reference_size_mm, probe_size_mm, max_error_px, contact_tolerance_mm)):
            raise ValueError("Marker sizes and tolerances must be positive")
        if tip_offset_mm is not None and (len(tip_offset_mm) != 3 or not all(math.isfinite(value) for value in tip_offset_mm)):
            raise ValueError("tip_offset_mm requires three finite physical offsets")
        self.intrinsics = intrinsics
        self.reference_id, self.probe_id = reference_id, probe_id
        self.reference_size_mm, self.probe_size_mm = reference_size_mm, probe_size_mm
        self.tip_offset_mm = tip_offset_mm
        self.max_error_px, self.contact_tolerance_mm = max_error_px, contact_tolerance_mm
        self.dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        parameters = cv2.aruco.DetectorParameters()
        parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        self.detector = cv2.aruco.ArucoDetector(self.dictionary, parameters)

    @staticmethod
    def object_points(size_mm):
        half_size = size_mm / 2.0
        return np.array([[-half_size, half_size, 0], [half_size, half_size, 0], [half_size, -half_size, 0], [-half_size, -half_size, 0]], dtype=np.float64)

    def _pose(self, corners, size_mm):
        points = self.object_points(size_mm)
        candidates = []
        for method in (cv2.SOLVEPNP_IPPE_SQUARE, cv2.SOLVEPNP_ITERATIVE):
            try:
                solved, rotations, translations, *_ = cv2.solvePnPGeneric(
                    points, corners.reshape(4, 2), self.intrinsics.matrix,
                    self.intrinsics.distortion, flags=method)
            except cv2.error:
                continue
            if not solved:
                continue
            for rotation, translation in zip(rotations, translations):
                if not np.isfinite(rotation).all() or not np.isfinite(translation).all() or translation[2, 0] <= 0:
                    continue
                rotation_matrix, _ = cv2.Rodrigues(rotation)
                if np.dot(rotation_matrix[:, 2], translation[:, 0]) >= 0:
                    continue
                if np.any((points @ rotation_matrix.T + translation[:, 0])[:, 2] <= 0):
                    continue
                projected, _ = cv2.projectPoints(points, rotation, translation, self.intrinsics.matrix, self.intrinsics.distortion)
                error = float(np.sqrt(np.mean(np.sum((projected.reshape(4, 2) - corners.reshape(4, 2)) ** 2, axis=1))))
                transform = np.eye(4)
                transform[:3, :3], transform[:3, 3] = rotation_matrix, translation[:, 0]
                candidates.append((error, transform))
        if not candidates:
            raise ValueError("pose_estimation_failed")
        return min(candidates, key=lambda candidate: candidate[0])

    def locate(self, image, source="LIVE", timestamp_ns=None):
        if source not in ("LIVE", "RECORDED", "DEMO"):
            raise ValueError("Invalid image provenance")
        timestamp_ns = time.monotonic_ns() if timestamp_ns is None else timestamp_ns
        corners, identifiers, _ = self.detector.detectMarkers(image)
        marker_ids = tuple(int(value) for value in identifiers.flatten()) if identifiers is not None else ()
        annotated = image.copy()
        if marker_ids:
            cv2.aruco.drawDetectedMarkers(annotated, corners, identifiers)

        def invalid(reason, error=None):
            return Observation(False, reason, None, timestamp_ns, source, marker_ids, error), annotated

        if len(set(marker_ids)) != len(marker_ids):
            return invalid("duplicate_marker_id")
        if self.reference_id not in marker_ids or self.probe_id not in marker_ids:
            return invalid("reference_or_probe_marker_missing")
        if self.intrinsics is None:
            return invalid("camera_intrinsics_required")
        if image.shape[:2] != (self.intrinsics.height, self.intrinsics.width):
            return invalid("image_size_differs_from_calibration")
        if self.tip_offset_mm is None:
            return invalid("physical_marker_to_tip_offset_required")
        try:
            reference_error, camera_from_world = self._pose(corners[marker_ids.index(self.reference_id)], self.reference_size_mm)
            probe_error, camera_from_probe = self._pose(corners[marker_ids.index(self.probe_id)], self.probe_size_mm)
        except (ValueError, cv2.error):
            return invalid("pose_estimation_failed")
        error = max(reference_error, probe_error)
        if error > self.max_error_px:
            return invalid("reprojection_error_too_large", error)
        tip_in_probe = np.array([*self.tip_offset_mm, 1.0])
        tip_in_world = np.linalg.inv(camera_from_world) @ camera_from_probe @ tip_in_probe
        position = tuple(float(value) for value in tip_in_world[:3])
        if abs(position[2]) > self.contact_tolerance_mm:
            return invalid("tip_not_on_calibrated_workpiece_plane", error)
        observation = Observation(True, "ok", position, timestamp_ns, source, marker_ids, error)
        cv2.putText(annotated, f"TIP {position[0]:.1f}, {position[1]:.1f}, {position[2]:.1f} mm [{source}]",
                    (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (30, 170, 30), 2)
        return observation, annotated


def calibrate_images(paths, columns, rows, square_mm, output):
    if columns < 3 or rows < 3 or not math.isfinite(square_mm) or square_mm <= 0:
        raise ValueError("Invalid checkerboard dimensions")
    object_grid = np.zeros((columns * rows, 3), np.float32)
    object_grid[:, :2] = np.mgrid[0:columns, 0:rows].T.reshape(-1, 2) * square_mm
    object_points, image_points, accepted = [], [], []
    image_size = None
    for path in paths:
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        size = (image.shape[1], image.shape[0])
        if image_size is not None and size != image_size:
            raise ValueError("Calibration images must have the same size")
        found, corners = cv2.findChessboardCornersSB(image, (columns, rows))
        if found:
            image_size = size
            object_points.append(object_grid.copy())
            image_points.append(corners)
            accepted.append(str(path))
    if len(accepted) < 8:
        raise ValueError("Need at least 8 detected checkerboard views at varied angles/positions")
    rms, matrix, distortion, _, _ = cv2.calibrateCamera(object_points, image_points, image_size, None, None)
    if not math.isfinite(rms) or rms > 1.5:
        raise ValueError(f"Calibration rejected: RMS={rms:.3f}px; target <=1.5px")
    result = {"camera_matrix": matrix.tolist(), "distortion": distortion.tolist(), "width": image_size[0],
              "height": image_size[1], "rms_px": rms, "square_mm": square_mm, "accepted_images": accepted}
    atomic_json(output, result)
    return result


def generate_targets(directory, marker_mm=30.0, square_mm=20.0):
    if min(marker_mm, square_mm) <= 0 or not all(math.isfinite(value) for value in (marker_mm, square_mm)):
        raise ValueError("Print dimensions must be positive")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for marker_id, name in ((0, "reference"), (1, "probe")):
        modules = cv2.aruco.generateImageMarker(dictionary, marker_id, 6)
        margin, pitch = 5.0, marker_mm / 6
        extent = marker_mm + margin * 2
        rectangles = [f'<rect x="{margin + column * pitch:g}" y="{margin + row * pitch:g}" width="{pitch:g}" height="{pitch:g}"/>'
                      for row in range(6) for column in range(6) if modules[row, column] == 0]
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{extent:g}mm" height="{extent:g}mm" viewBox="0 0 {extent:g} {extent:g}"><rect width="100%" height="100%" fill="white"/><g fill="black">' + "".join(rectangles) + '</g></svg>'
        (directory / f"{name}_{marker_id}.svg").write_text(svg, encoding="utf-8")
    rectangles = [f'<rect x="{column * square_mm:g}" y="{row * square_mm:g}" width="{square_mm:g}" height="{square_mm:g}"/>'
                  for row in range(7) for column in range(10) if (row + column) % 2 == 0]
    width, height = 10 * square_mm, 7 * square_mm
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:g}mm" height="{height:g}mm" viewBox="0 0 {width:g} {height:g}"><rect width="100%" height="100%" fill="white"/><g fill="black">' + "".join(rectangles) + '</g></svg>'
    (directory / "checkerboard_9x6.svg").write_text(svg, encoding="utf-8")


def synthetic_scene(position_mm):
    intrinsics = Intrinsics(np.array([[900., 0., 480.], [0., 900., 360.], [0., 0., 1.]]), np.zeros(5), 960, 720)
    render_scale = 4
    image = np.full((720 * render_scale, 960 * render_scale, 3), 255, np.uint8)
    rotation = np.array([math.pi, 0., 0.])
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for marker_id, center in ((0, (0., 0., 0.)), (1, (70. + position_mm, 0., 35.))):
        world_points = Tracker.object_points(30.) + np.array(center)
        destination, _ = cv2.projectPoints(world_points, rotation, np.array([-60., 0., 500.]), intrinsics.matrix, intrinsics.distortion)
        marker = cv2.aruco.generateImageMarker(dictionary, marker_id, 240)
        marker = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
        source = np.array([[0, 0], [239, 0], [239, 239], [0, 239]], np.float32)
        warp = cv2.getPerspectiveTransform(source, destination.reshape(4, 2).astype(np.float32) * render_scale)
        projected = cv2.warpPerspective(marker, warp, (960 * render_scale, 720 * render_scale), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))
        image = np.minimum(image, projected)
    return cv2.resize(image, (960, 720), interpolation=cv2.INTER_AREA), intrinsics
