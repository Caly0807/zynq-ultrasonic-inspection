import json
import threading
import time
import uuid
import webbrowser
from collections import OrderedDict
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import cv2
import numpy as np

from .camera import Camera
from .workpiece import PlanarFace, ProbePoint, VisualSettings, WorkpieceSpec, annotate, find_probe, find_workpieces, save_result, sharpness, top_view
from .workpiece_config import read_profile, write_profile


ROOT = Path(__file__).resolve().parents[1]


class WorkpieceSession:
    def __init__(self, image, output, spec, camera=None, visual_settings=None, profile_path=None):
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.camera = camera
        self.image_source = "LIVE_CAMERA" if camera else "RECORDED"
        self.output = Path(output).resolve()
        self.spec = spec
        self.settings = visual_settings or VisualSettings()
        self.profile_path = Path(profile_path).resolve() if profile_path else None
        self.saved_face_reference = None
        self.frozen_snapshot = None
        self.face = self.manual_sample = None
        self.contact_on_plane = False
        self.geometry_id = uuid.uuid4().hex
        self.session_id = uuid.uuid4().hex
        self.cache = OrderedDict()
        self.history, self.marks = [], []
        self.event_ids = set()
        self.acquisition_ids = set()
        self.error = ""
        self.sequence = 0
        self.thread = None
        self._update(image, camera.timestamp_ns if camera else time.monotonic_ns())

    def _update(self, image, timestamp_ns):
        with self.lock:
            if self.face and image.shape[:2] != (self.face.height, self.face.width):
                self._reset_geometry()
            self.sequence += 1
            candidates = find_workpieces(image, self.settings.focus_min)
            probe, reason, probe_candidates = find_probe(image, self.face, self.settings, self.contact_on_plane) if self.face else (None, "select_workpiece_face", [])
            self.latest = {"image": image, "timestamp_ns": timestamp_ns, "frame_id": f"{self.session_id}:{self.sequence}",
                           "candidates": candidates, "focus": sharpness(image), "face": self.face, "probe": probe,
                           "probe_reason": reason, "probe_candidates": probe_candidates, "geometry_id": self.geometry_id}
            self.cache[self.latest["frame_id"]] = self.latest
            while len(self.cache) > 30:
                self.cache.popitem(last=False)

    def start(self):
        if self.camera:
            self.thread = threading.Thread(target=self._capture, daemon=True)
            self.thread.start()

    def _capture(self):
        try:
            while not self.stopped.is_set():
                _, image = self.camera.read()
                self._update(image, self.camera.timestamp_ns)
        except (OSError, ValueError, cv2.error) as error:
            with self.lock:
                self.error = str(error)

    def close(self):
        self.stopped.set()
        if self.camera:
            self.camera.release()
        if self.thread:
            self.thread.join(timeout=2)

    def _reset_geometry(self):
        self.face = self.manual_sample = None
        self.frozen_snapshot = None
        self.contact_on_plane = False
        self.marks = []
        self.geometry_id = uuid.uuid4().hex

    def _snapshot(self, frame_id=None, fresh=False):
        snapshot = self.cache.get(frame_id) if frame_id else self.latest
        if snapshot is None:
            raise ValueError("Camera frame is no longer cached; click on the current image again")
        if snapshot["geometry_id"] != self.geometry_id:
            raise ValueError("Workpiece geometry changed; obtain a new camera frame")
        age_ns = time.monotonic_ns() - snapshot["timestamp_ns"]
        if fresh and (self.error or not 0 <= age_ns <= 2_000_000_000):
            raise ValueError("Camera frame is stale or camera is offline")
        return snapshot

    def _display_snapshot(self, frame_id=None, fresh=False):
        if self.frozen_snapshot and frame_id in (None, self.frozen_snapshot["frame_id"]):
            if self.frozen_snapshot["geometry_id"] != self.geometry_id:
                raise ValueError("Workpiece geometry changed; freeze a new frame")
            return self.frozen_snapshot
        return self._snapshot(frame_id, fresh)

    def _manual_valid(self):
        if self.manual_sample is None or self.manual_sample["geometry_id"] != self.geometry_id:
            return False
        if self.frozen_snapshot and self.manual_sample["frame_id"] == self.frozen_snapshot["frame_id"]:
            return True
        return 0 <= time.monotonic_ns() - self.manual_sample["timestamp_ns"] <= 2_000_000_000

    def _camera_identity(self):
        return getattr(self.camera, "backend", "LIVE_CAMERA") if self.camera else "RECORDED"

    def _reference_compatible(self):
        reference = self.saved_face_reference
        return bool(reference and reference["camera_identity"] == self._camera_identity() and
                    reference["image_size"] == [self.latest["image"].shape[1], self.latest["image"].shape[0]])

    def state(self):
        with self.lock:
            snapshot = self._display_snapshot()
            probe = snapshot["probe"]
            display_probe = self.manual_sample["probe"] if self._manual_valid() else probe
            return {"visual_api_version": 2, "source": self.image_source, "frame_id": snapshot["frame_id"], "frame_count": self.sequence,
                    "camera_error": self.error, "camera_age_s": (time.monotonic_ns() - self.latest["timestamp_ns"]) / 1e9,
                    "frozen": self.frozen_snapshot is not None,
                    "display_age_s": (time.monotonic_ns() - snapshot["timestamp_ns"]) / 1e9,
                    "sharpness": snapshot["focus"], "focus_warning": snapshot["focus"] < self.settings.focus_min,
                    "image_width": snapshot["image"].shape[1], "image_height": snapshot["image"].shape[0],
                    "spec": asdict(self.spec), "face": self.face.describe() if self.face else None,
                    "candidates": snapshot["candidates"], "probe": asdict(probe) if probe else None,
                    "probe_reason": snapshot["probe_reason"], "probe_candidates": snapshot["probe_candidates"],
                    "manual_point_available": self._manual_valid(),
                    "display_probe": asdict(display_probe) if display_probe else None,
                    "display_plane_position_mm": self.face.position_mm(display_probe.pixel, display_probe.contact_on_plane) if self.face and display_probe else None,
                    "visual_settings": asdict(self.settings),
                    "profile": {"path": str(self.profile_path) if self.profile_path else None,
                                "exists": bool(self.profile_path and self.profile_path.is_file()),
                                "has_face_reference": self.saved_face_reference is not None,
                                "face_reference_compatible": self._reference_compatible()},
                    "contact_on_plane_confirmed": self.contact_on_plane,
                    "plane_position_mm": self.face.position_mm(probe.pixel, probe.contact_on_plane) if self.face and probe else None,
                    "accuracy": "UNCALIBRATED_PLANAR_ESTIMATE", "hardware_control": False,
                    "live_suspect_count": sum(item["kind"] == "LIVE_SUSPECT" for item in self.history),
                    "history": self.history[-12:], "output_directory": str(self.output)}

    def picture(self, rectified=False, raw=False):
        with self.lock:
            snapshot = self._display_snapshot()
            probe = snapshot["probe"]
            if self._manual_valid():
                probe = self.manual_sample["probe"]
            if raw:
                image, extension = snapshot["image"], ".jpg"
            elif rectified:
                image = top_view(snapshot["image"], self.face, probe, self.marks)
                extension = ".png"
            else:
                label = self.image_source + " / " + ("FROZEN TEST FRAME" if self.frozen_snapshot else "CAMERA OFFLINE" if self.error else "NO ULTRASOUND CONNECTED")
                image = annotate(snapshot["image"], self.face, probe, label, snapshot["candidates"] if self.face is None else ())
                extension = ".jpg"
            success, encoded = cv2.imencode(extension, image)
            if not success:
                raise OSError("Image encoding failed")
            return encoded.tobytes(), snapshot["frame_id"]

    def action(self, payload):
        with self.lock:
            action = payload.get("action")
            displayed = self._display_snapshot()
            was_frozen = self.frozen_snapshot is not None
            if action == "reset":
                self._reset_geometry()
            elif action == "freeze":
                if self.frozen_snapshot is None:
                    self.frozen_snapshot = self._snapshot(payload.get("frame_id"), fresh=self.camera is not None)
                    self.frozen_snapshot["frozen_for_test"] = True
                self.manual_sample = None
            elif action == "resume":
                self.frozen_snapshot = self.manual_sample = None
            elif action == "spec":
                self.spec = WorkpieceSpec(float(payload["length_mm"]), float(payload["width_mm"]),
                                          float(payload["thickness_mm"]), payload.get("dimensions_confirmed") is True)
                self._reset_geometry()
            elif action in ("lock_auto", "corners"):
                snapshot = self._display_snapshot(payload.get("frame_id"), fresh=self.camera is not None)
                if action == "lock_auto":
                    if not snapshot["candidates"]:
                        raise ValueError("No clear square candidate; focus the camera or select four corners")
                    corners = snapshot["candidates"][0]["corners_px"]
                else:
                    corners = payload["corners"]
                face = PlanarFace(corners, snapshot["image"].shape, self.spec)
                self._reset_geometry()
                self.face = face
            elif action == "contact":
                if self.settings.probe_mode == "round" and payload.get("confirmed") is True:
                    raise ValueError("Circular body centers are above the surface; contact confirmation is unavailable")
                self.contact_on_plane = payload.get("confirmed") is True
                self.geometry_id = uuid.uuid4().hex
                self.manual_sample = None
            elif action == "probe":
                snapshot = self._display_snapshot(payload.get("frame_id"), fresh=self.camera is not None)
                if self.face is None:
                    raise ValueError("Confirm the workpiece face before selecting the probe contact point")
                probe = ProbePoint(tuple(float(value) for value in payload["pixel"]), "MANUAL_CONTACT", True)
                if self.face.normalized(probe.pixel) is None:
                    raise ValueError("Select a probe contact point inside the workpiece face")
                self.manual_sample = {**snapshot, "face": self.face, "probe": probe, "selected_ns": time.monotonic_ns()}
            elif action == "settings":
                values = payload.get("settings")
                if not isinstance(values, dict):
                    raise ValueError("A visual settings object is required")
                try:
                    settings = VisualSettings(**{**asdict(self.settings), **values})
                except TypeError as error:
                    raise ValueError("Unknown visual setting") from error
                self.settings = settings
                self.contact_on_plane = False
                self.manual_sample = None
                self.geometry_id = uuid.uuid4().hex
            elif action in ("profile_save", "profile_load"):
                if self.profile_path is None:
                    raise ValueError("No profile path was configured at startup")
                if action == "profile_save":
                    self.saved_face_reference = write_profile(self.profile_path, self.spec, self.settings, self.face, self._camera_identity())
                else:
                    spec, settings, reference = read_profile(self.profile_path)
                    self.spec, self.settings, self.saved_face_reference = spec, settings, reference
                    self._reset_geometry()
            elif action == "restore_face":
                if payload.get("confirmed") is not True:
                    raise ValueError("Explicitly confirm the camera and workpiece have not moved")
                if not self._reference_compatible():
                    raise ValueError("Saved corners require the same camera and resolution; select four new corners")
                corners = np.asarray(self.saved_face_reference["normalized_corners"], np.float32)
                corners *= np.array([self.latest["image"].shape[1] - 1, self.latest["image"].shape[0] - 1])
                face = PlanarFace(corners, self.latest["image"].shape, self.spec)
                self._reset_geometry()
                self.face = face
            elif action in ("capture", "manual_mark"):
                if action == "manual_mark" and self.manual_sample:
                    if not self._manual_valid():
                        raise ValueError("Manual selection expired; freeze the image before selecting a test point")
                    if not self.frozen_snapshot:
                        self._snapshot(self.manual_sample["frame_id"], fresh=self.camera is not None)
                    snapshot = self.manual_sample
                else:
                    snapshot = self._display_snapshot(payload.get("frame_id"), fresh=self.camera is not None)
                if self.error and not self.frozen_snapshot:
                    raise ValueError("Camera is offline; an old frame will not be saved as a new snapshot")
                record = save_result(self.output, snapshot["image"], snapshot["face"], snapshot["probe"],
                                     self.image_source, manual_mark=action == "manual_mark", frame_id=snapshot["frame_id"],
                                     frame_timestamp_ns=snapshot["timestamp_ns"], marks=self.marks,
                                     frozen=bool(self.frozen_snapshot and self.frozen_snapshot["frame_id"] == snapshot["frame_id"]),
                                     visual_settings=self.settings)
                self.history.append(record)
                if action == "manual_mark":
                    self.marks.append(record)
                return record
            else:
                raise ValueError("Unknown visual inspection action")
            if action in ("reset", "spec", "lock_auto", "corners", "contact", "settings", "profile_load", "restore_face"):
                keep_frozen = was_frozen and action in ("lock_auto", "corners", "contact", "settings")
                snapshot = displayed if keep_frozen else self.latest
                self._update(snapshot["image"], snapshot["timestamp_ns"])
                if keep_frozen:
                    self.frozen_snapshot = self.latest
                    self.frozen_snapshot["frozen_for_test"] = True
            return {"ok": True}

    def diagnosis(self, payload):
        with self.lock:
            event_id = payload.get("event_id")
            frame_id = payload.get("camera_frame_id")
            if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128 or event_id in self.event_ids:
                raise ValueError("A new bounded event_id is required")
            if not isinstance(frame_id, str) or not frame_id:
                raise ValueError("An exact camera_frame_id is required; no latest-frame fallback")
            diagnosis = payload.get("diagnosis")
            if not isinstance(diagnosis, dict):
                raise ValueError("A diagnosis object is required")
            acquisition_id = diagnosis.get("frame_id")
            if diagnosis.get("source") == "LIVE" and isinstance(acquisition_id, str) and acquisition_id in self.acquisition_ids:
                raise ValueError("This LIVE acquisition frame has already been marked")
            snapshot = self._snapshot(frame_id, fresh=diagnosis.get("source") == "LIVE")
            if diagnosis.get("source") == "LIVE" and snapshot.get("frozen_for_test"):
                raise ValueError("Frozen display frames are for visual tests, not LIVE defect binding")
            if snapshot["focus"] < 12:
                raise ValueError("Camera image is too blurred for defect binding")
            record = save_result(self.output, snapshot["image"], snapshot["face"], snapshot["probe"],
                                 self.image_source, diagnosis=diagnosis, frame_id=frame_id,
                                 frame_timestamp_ns=snapshot["timestamp_ns"], marks=self.marks, event_id=event_id,
                                 visual_settings=self.settings)
            self.event_ids.add(event_id)
            if diagnosis.get("source") == "LIVE":
                self.acquisition_ids.add(acquisition_id)
            self.history.append(record)
            self.marks.append(record)
            return record


def make_handler(session, stop_callback, *, access_policy=None, device_status=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format_string, *arguments):
            return

        def _send(self, data, content_type="application/json; charset=utf-8", status=200, frame_id=None, authenticate=False):
            self.send_response(status)
            if authenticate:
                self.send_header("WWW-Authenticate", 'Basic realm="UT Studio device", charset="UTF-8"')
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if frame_id:
                self.send_header("X-Camera-Frame-Id", frame_id)
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, payload, status=200):
            self._send(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"), status=status)

        def _valid_host(self):
            if access_policy is not None:
                return access_policy.allowed(self)
            port = self.server.server_port
            origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
            return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"} and self.headers.get("Origin", f"http://127.0.0.1:{port}") in origins

        def _authorize(self):
            if not self._valid_host():
                self._json({"error": "Peer, Host or same-origin policy rejected the request"}, 403)
                return False
            if access_policy is not None and not access_policy.authenticated(self):
                self._send(b'{"error":"Device login required"}', status=401, authenticate=True)
                return False
            return True

        def do_GET(self):
            if not self._authorize():
                return
            route = urlsplit(self.path).path
            try:
                if route == "/":
                    self._send((ROOT / "web/workpiece.html").read_bytes(), "text/html; charset=utf-8")
                elif route == "/api/state":
                    state = session.state()
                    if device_status is not None:
                        state["device"] = device_status
                    self._json(state)
                elif route == "/api/device" and device_status is not None:
                    self._json(device_status)
                elif route in ("/api/frame.jpg", "/api/top.png", "/api/raw.jpg"):
                    data, frame_id = session.picture(route.endswith("top.png"), raw=route.endswith("raw.jpg"))
                    self._send(data, "image/png" if route.endswith(".png") else "image/jpeg", frame_id=frame_id)
                elif route.startswith("/captures/"):
                    filename = unquote(route.removeprefix("/captures/"))
                    target = (session.output / filename).resolve()
                    if target.parent != session.output or not target.is_file() or target.suffix not in (".png", ".json"):
                        self._json({"error": "Unknown capture"}, 404)
                    else:
                        self._send(target.read_bytes(), "image/png" if target.suffix == ".png" else "application/json; charset=utf-8")
                else:
                    self._json({"error": "Unknown route"}, 404)
            except (ValueError, OSError) as error:
                self._json({"error": str(error)}, 400)

        def do_POST(self):
            if not self._authorize():
                return
            try:
                if access_policy is not None and (len(self.headers.get_all("Content-Length", [])) != 1 or self.headers.get("Transfer-Encoding")):
                    raise ValueError("Use one Content-Length without transfer encoding")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65536:
                    raise ValueError("Invalid JSON request size")
                self.connection.settimeout(5)
                body = self.rfile.read(size)
                if len(body) != size:
                    raise ValueError("Incomplete JSON request body")
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    self._json({"error": "Same-origin JSON requests required"}, 403)
                    return
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("JSON object required")
                route = urlsplit(self.path).path
                if route == "/api/action":
                    self._json(session.action(payload))
                elif route == "/api/diagnosis":
                    self._json(session.diagnosis(payload))
                elif route == "/api/stop":
                    self._json({"stopped": True})
                    threading.Thread(target=stop_callback, daemon=True).start()
                else:
                    self._json({"error": "Unknown route"}, 404)
            except (ValueError, TypeError, KeyError, OSError, cv2.error) as error:
                self._json({"error": str(error)}, 400)

    return Handler


def run_workpiece(args):
    if not 1024 <= args.port <= 65535:
        raise ValueError("Choose a local port in 1024..65535")
    spec = WorkpieceSpec(args.length_mm, args.width_mm, args.thickness_mm, args.dimensions_confirmed)
    visual_settings = VisualSettings(probe_mode=args.probe_mode)
    profile_path = Path(args.profile).resolve()
    reference = None
    if profile_path.is_file():
        spec, visual_settings, reference = read_profile(profile_path)
    camera = None
    session = None
    server = None
    try:
        if args.image:
            image = cv2.imdecode(np.fromfile(args.image, np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError("Could not decode the recorded input image")
        else:
            camera = Camera(args.camera, args.width, args.height, args.camera_name, args.ffmpeg)
            _, image = camera.read()
        output = Path(args.out) if args.out else ROOT / "runs" / f"workpiece_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        session = WorkpieceSession(image, output, spec, camera, visual_settings, profile_path)
        session.saved_face_reference = reference
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(session, lambda: server.shutdown()))
        session.start()
        address = f"http://127.0.0.1:{args.port}"
        print("VISUAL_DEBUG_URL=" + address, flush=True)
        print("OUTPUT_DIRECTORY=" + str(output.resolve()), flush=True)
        print("No motor, HV, FPGA or Flash operations. Images are saved only on request or a validated diagnosis.", flush=True)
        if args.preview:
            webbrowser.open(address)
        server.serve_forever(poll_interval=0.2)
    finally:
        if server:
            server.server_close()
        if session:
            session.close()
        elif camera:
            camera.release()
