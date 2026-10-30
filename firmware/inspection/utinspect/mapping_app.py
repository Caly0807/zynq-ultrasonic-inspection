import hashlib
import json
import tempfile
import threading
import uuid
import webbrowser
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .echo import EchoConfig, load_csv
from .mapping import MAX_POINTS, MapSession, MapSpec, demo_session, embedded_csv, render_dashboard, synthetic_map_waveform, validate_submission_id
from .scan import ExchangeCapture, StopRequested


ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 1024 * 1024
MAX_SUBMISSIONS = MAX_POINTS * 2


class SubmissionConflict(ValueError):
    def __init__(self, message, state="CONFLICT"):
        super().__init__(message)
        self.state = state


class SubmissionUnconfirmed(ValueError):
    pass


def reject_constant(value):
    raise ValueError("Non-finite JSON values are not accepted")


def make_map_handler(session, capture=None, stop_event=None, *, access_policy=None, device_status=None, vision=None, transport_test=None):
    stopped = stop_event or threading.Event()
    acquisition_lock = threading.Lock()
    submissions = {}

    def acquire_point(payload):
        if stopped.is_set():
            raise ValueError("Session has stopped")
        if payload.get("session_id", session.session_id) != session.session_id:
            raise ValueError("Session changed; reload the page and reconfirm the manual position")
        position = payload.get("position_mm")
        if not isinstance(position, list) or len(position) != 2:
            raise ValueError("Select a two-dimensional manual position")
        session.spec.cell(*position)
        if payload.get("position_confirmed") is not True:
            raise ValueError("Confirm the probe is at the selected manual position")
        submission_id = payload.get("submission_id")
        if "submission_id" in payload and submission_id is None:
            raise ValueError("An explicit submission ID cannot be null")
        validate_submission_id(submission_id)
        text = payload.get("csv")
        if session.mode == "DEMO" and "csv" in payload:
            raise ValueError("DEMO sessions do not accept recorded inputs")
        if session.mode == "RECORDED" and (not isinstance(text, str) or not text or "\x00" in text):
            raise ValueError("Select an original waveform CSV; direct diagnoses are not accepted")
        if session.mode == "LIVE" and (capture is None or "csv" in payload):
            raise ValueError("LIVE points require a configured fresh-capture exchange adapter, not uploaded files")
        fingerprint = hashlib.sha256(json.dumps({"position_mm": [float(value) for value in position], "csv": text,
                                                  "source": session.mode}, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()
        if not acquisition_lock.acquire(blocking=False):
            raise SubmissionConflict("Another acquisition is already pending", "BUSY")
        receipt = None
        try:
            if submission_id is not None:
                receipt = submissions.get(submission_id)
                if receipt is not None:
                    if receipt["fingerprint"] != fingerprint:
                        raise SubmissionConflict("Submission ID belongs to different coordinates or input; no new acquisition was requested")
                    if receipt["state"] != "COMMITTED":
                        raise SubmissionConflict("Previous submission is unconfirmed; inspect the records and adapter before starting a new session. No retry was triggered", "UNCONFIRMED")
                    return receipt["point"], True
                if len(submissions) >= MAX_SUBMISSIONS:
                    raise ValueError("Submission ledger is full; start another session before further acquisition")
            with session.lock:
                if len(session.points) >= MAX_POINTS:
                    raise ValueError("Map point capacity reached; no acquisition was requested")
            if submission_id is not None:
                receipt = {"fingerprint": fingerprint, "state": "PENDING"}
                submissions[submission_id] = receipt
            try:
                if session.mode == "DEMO":
                    frame = synthetic_map_waveform(*position, session.spec)
                elif session.mode == "RECORDED":
                    with tempfile.TemporaryDirectory(prefix="utmap_import_") as directory:
                        path = Path(directory) / "waveform.csv"
                        path.write_text(text, encoding="utf-8")
                        frame = load_csv(path)
                    frame = replace(frame, frame_id="recorded-" + hashlib.sha256(text.encode("utf-8")).hexdigest())
                else:
                    frame = capture.capture(session.session_id + "_" + uuid.uuid4().hex, stopped)
                if stopped.is_set():
                    raise ValueError("Session stopped; no point was committed")
                point = session.add(frame, *position, position_confirmed=True, submission_id=submission_id)
            except Exception as error:
                if receipt is not None:
                    receipt["state"] = "UNCONFIRMED"
                raise SubmissionUnconfirmed(str(error)) from error
            if receipt is not None:
                receipt.update(state="COMMITTED", point=point)
            return point, False
        finally:
            acquisition_lock.release()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format_string, *arguments):
            return

        def allowed(self):
            if access_policy is not None:
                return access_policy.allowed(self)
            port = self.server.server_address[1]
            authorities = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in authorities:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin in {"http://" + authority for authority in authorities}

        def authorize(self):
            if not self.allowed():
                self.send_json({"error": "Peer, Host or same-origin policy rejected the request"}, 403)
                return False
            if access_policy is not None and not access_policy.authenticated(self):
                self.send_data(b'{"error":"Device login required"}', "application/json; charset=utf-8", 401, authenticate=True)
                return False
            return True

        def send_data(self, data, content_type, status=200, filename=None, authenticate=False):
            if status >= 400 and self.command == "POST" and getattr(self, "request_body_pending", False):
                self.request_body_pending = False
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) == 1 and not self.headers.get("Transfer-Encoding"):
                    try:
                        length = int(lengths[0])
                        if 0 <= length <= MAX_BODY:
                            self.connection.settimeout(1)
                            self.rfile.read(length)
                    except (ValueError, OSError, TimeoutError):
                        pass
                self.close_connection = True
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                if authenticate:
                    self.send_header("WWW-Authenticate", 'Basic realm="UT Studio device", charset="UTF-8"')
                if filename:
                    self.send_header("Content-Disposition", 'attachment; filename="' + filename + '"')
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def send_json(self, payload, status=200):
            self.send_data(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8", status)

        def do_GET(self):
            if not self.authorize():
                return
            route = urlsplit(self.path)
            try:
                snapshot = session.snapshot()
                if route.path == "/":
                    self.send_data((ROOT / "web/inspection.html").read_bytes(), "text/html; charset=utf-8")
                elif route.path == "/vision" and vision is not None:
                    self.send_data((ROOT / "web/camera_basic.html").read_bytes(), "text/html; charset=utf-8")
                elif route.path == "/api/vision/state" and vision is not None:
                    self.send_json(vision.state())
                elif route.path == "/api/vision/frame.png" and vision is not None:
                    frame_id = parse_qs(route.query).get("frame_id", [None])[0]
                    self.send_data(vision.frame_png(frame_id), "image/png")
                elif route.path == "/api/state":
                    state = {**snapshot, "capture_adapter_configured": capture is not None, "stopped": stopped.is_set()}
                    if device_status is not None:
                        state["device"] = device_status() if callable(device_status) else device_status
                    self.send_json(state)
                elif route.path == "/api/device" and device_status is not None:
                    self.send_json(device_status() if callable(device_status) else device_status)
                elif route.path == "/api/waveform":
                    point_id = parse_qs(route.query).get("point_id", [""])[0]
                    self.send_json(session.waveform(point_id))
                elif route.path == "/export/session.json":
                    self.send_data(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"),
                                   "application/json; charset=utf-8", filename="inspection_session.json")
                elif route.path == "/export/map.csv":
                    self.send_data(embedded_csv(snapshot).encode("ascii"), "text/csv; charset=ascii", filename="map.csv")
                elif route.path == "/export/waveform.csv":
                    point_id = parse_qs(route.query).get("point_id", [""])[0]
                    self.send_data(session.waveform_csv(point_id), "text/csv; charset=ascii", filename="waveform.csv")
                elif route.path == "/export/report.html":
                    self.send_data(render_dashboard(snapshot).encode("utf-8"), "text/html; charset=utf-8", filename="inspection_report.html")
                else:
                    self.send_json({"error": "Unknown endpoint"}, 404)
            except (ValueError, OSError) as error:
                self.send_json({"error": str(error)}, 400)

        def do_POST(self):
            self.request_body_pending = True
            if not self.authorize():
                return
            try:
                if access_policy is not None:
                    if self.headers.get_content_type() != "application/json" or len(self.headers.get_all("Content-Length", [])) != 1 or self.headers.get("Transfer-Encoding"):
                        self.send_json({"error": "Use one Content-Length and application/json without transfer encoding"}, 400)
                        return
                length = int(self.headers.get("Content-Length", "-1"))
                if not 1 <= length <= MAX_BODY:
                    self.send_json({"error": "Request must contain 1..1048576 bytes"}, 413)
                    return
                self.request_body_pending = False
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError("Truncated JSON request body")
                payload = json.loads(body, parse_constant=reject_constant)
                if not isinstance(payload, dict):
                    raise ValueError("A JSON object is required")
                route = urlsplit(self.path).path
                if route == "/api/transport/selftest" and transport_test is not None:
                    self.send_json(transport_test(payload))
                    return
                if vision is not None and route in ("/api/vision/freeze", "/api/vision/calibrate", "/api/vision/clear"):
                    action = {"/api/vision/freeze": vision.freeze, "/api/vision/calibrate": lambda: vision.calibrate(payload),
                              "/api/vision/clear": vision.clear}[route]
                    self.send_json(action())
                    return
                if route == "/api/stop":
                    stopped.set()
                    self.send_json({"ok": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if route != "/api/point":
                    self.send_json({"error": "Unknown endpoint"}, 404)
                    return
                point, replayed = acquire_point(payload)
                self.send_json({"ok": True, "point": point, "submission_id": point["submission_id"], "replayed": replayed})
            except SubmissionConflict as error:
                self.send_json({"error": str(error), "submission_state": error.state}, 409)
            except SubmissionUnconfirmed as error:
                self.send_json({"error": str(error), "submission_state": "UNCONFIRMED"}, 400)
            except (ValueError, KeyError, TypeError, StopIteration, UnicodeError, OSError, TimeoutError, StopRequested) as error:
                self.send_json({"error": str(error), "submission_state": "REJECTED"}, 400)

    return Handler


def run_manual_map(args):
    if not 1024 <= args.port <= 65535:
        raise ValueError("Choose an unprivileged local port")
    if args.mode != "LIVE" and args.exchange:
        raise ValueError("Only a LIVE session can use a fresh-capture adapter")
    data = json.loads(Path(args.config).read_text(encoding="utf-8")) if args.config else {}
    if args.mode == "DEMO":
        session = demo_session(args.out, complete=False)
    else:
        session = MapSession(args.out, MapSpec(**data.get("mapping", {})), EchoConfig(**data.get("echo", {})), args.mode)
    capture = ExchangeCapture(args.exchange) if args.exchange else None
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_map_handler(session, capture))
    address = f"http://127.0.0.1:{args.port}"
    print(f"MANUAL_MAP {address} SOURCE={session.mode} OUTPUT={session.root}", flush=True)
    print("Manual coordinates are unverified. No camera, motor, JTAG or HV outputs. Ctrl+C stops this server.", flush=True)
    if args.preview:
        webbrowser.open(address)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        (session.root / "report.html").write_text(render_dashboard(session.snapshot()), encoding="utf-8")
