import argparse
import json
import platform
import signal
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

from .echo import EchoConfig
from .mapping import MapSession, MapSpec, render_dashboard
from .mapping_app import make_map_handler
from .network import NetworkAccess, load_token, private_address
from .board_health import service_health
from .basic_vision import BasicVision
from .board_capture import inspect_transport


def runtime_status():
    machine = platform.machine().lower()
    on_board = sys.platform == "linux" and machine in ("armv7l", "armv8l", "aarch64")
    clock_domain = "UNVERIFIED_LOCAL_CLOCK_ONLY"
    if sys.platform == "linux":
        try:
            clock_domain = "linux-boot:" + str(uuid.UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()))
        except (OSError, ValueError):
            pass
    return {
        "schema": "ut-device/1",
        "runtime": "ARM_LINUX" if on_board else "HOST_TEST_ONLY",
        "machine": machine,
        "operating_system": platform.system(),
        "service_session": uuid.uuid4().hex,
        "clock_domain": clock_domain,
        "started_unix_ns": time.time_ns(),
        "diagnosis_location": "SERVICE_PROCESS",
        "browser_role": "DISPLAY_AND_OPERATOR_INPUT_ONLY",
        "coordinate_source": "MANUAL_UNVERIFIED",
        "live_capture_ready": False,
        "vision_active": False,
        "local_lcd_ready": False,
        "storage_persistence_verified": False,
        "transmit_enabled": False,
        "motion_enabled": False,
        "timing_contract": "Monotonic timestamps are local to this service; never subtract a PC clock",
        "limitations": ["Linux DMA transport is TEST only; no real ultrasound producer is installed",
                        "Basic vision requires a separate UVC helper, four-corner calibration and one red marker",
                        "Linux LCD and I2C touch still need board validation", "Reflector indications are not hole-shape reconstruction"],
    }


class DeviceHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 8

    def __init__(self, address, handler):
        self.connection_slots = threading.BoundedSemaphore(8)
        super().__init__(address, handler)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(5)
        return request, address

    def process_request(self, request, client_address):
        if not self.connection_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.connection_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connection_slots.release()


def create_server(address, port, peer, token, session, runtime_directory=None, vision=None, *, live=None, local_display_independent=False):
    access = NetworkAccess(private_address(address), private_address(peer), token)
    status = runtime_status()
    transport_lock = threading.Lock()
    def transport_test(payload):
        if (runtime_directory is None or status["runtime"] != "ARM_LINUX" or session.mode != "DEMO" or
                set(payload) != {"hv_off_confirmed", "test"} or payload.get("hv_off_confirmed") is not True or
                payload.get("test") != "FPGA_RAMP_4096"):
            raise ValueError("Explicit current HV-OFF confirmation and ARM DEMO runtime are required for FPGA ramp TEST")
        if not transport_lock.acquire(blocking=False):
            raise ValueError("A transport self-test is already pending; no retry was triggered")
        try:
            return inspect_transport(selftest=True)
        finally:
            transport_lock.release()
    if vision is None and runtime_directory is not None:
        vision = BasicVision(Path(runtime_directory) / "vision/frame.ppm", session.spec.length_mm, session.spec.width_mm)
    def current_status():
        result = dict(status)
        if live is not None:
            result["real_capture"] = live.state()
            result["live_capture_ready"] = result["real_capture"]["available"]
            result["live_session_active"] = result["real_capture"]["active"]
            result["transmit_enabled"] = result["real_capture"]["active"] or result["real_capture"]["capture_in_flight"]
            result["realtime_endpoint"] = "/live"
            result["limitations"] = ["Real ADC transport requires current operator arming and keepalive; reboot never arms TX",
                                     "Depth and defect verdicts require approved time-zero, gates and material calibration",
                                     "Motor motion is not enabled; positions remain manually confirmed",
                                     "Reflector indications are not hole-shape reconstruction"]
        if runtime_directory is not None:
            result["services"] = service_health(runtime_directory)
        if vision is not None:
            result["vision_endpoint"] = "/vision"
            result["vision"] = vision.state()
            result["vision_active"] = result["vision"]["preview_available"]
        return result
    provider = status if runtime_directory is None and vision is None and live is None else current_status
    listen_address = "0.0.0.0" if local_display_independent else address
    server = DeviceHTTPServer((listen_address, port), make_map_handler(session, capture=live, access_policy=access, device_status=provider,
                                                               vision=vision, transport_test=transport_test, live=live))
    server.device_status = status
    server.basic_vision = vision
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="Board-side diagnosis and mapping; LIVE requires guarded hardware and explicit operator arming")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--peer", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--mode", choices=("DEMO", "RECORDED", "LIVE"), default="RECORDED")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--local-display-independent", action="store_true")
    arguments = parser.parse_args(argv)
    private_address(arguments.bind)
    private_address(arguments.peer)
    if not 1024 <= arguments.port <= 65535:
        raise ValueError("Choose an unprivileged TCP port")
    if arguments.bind == arguments.peer and arguments.bind != "127.0.0.1":
        raise ValueError("The device and PC must have different IPv4 addresses")
    configuration = json.loads(arguments.config.read_text(encoding="utf-8")) if arguments.config else {}
    arguments.data.mkdir(parents=True, exist_ok=True)
    token_path = arguments.token_file or arguments.data / "operator.token"
    token = load_token(token_path)
    run_name = datetime.now(timezone.utc).strftime("session_%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:12]
    session = MapSession(arguments.data / run_name, MapSpec(**configuration.get("mapping", {})),
                         EchoConfig(**configuration.get("echo", {})), arguments.mode)
    session.bind_local_display(arguments.data / "lcd")
    live = None
    if arguments.mode == "LIVE":
        if runtime_status()["runtime"] != "ARM_LINUX":
            raise ValueError("LIVE service must run on ARM Linux; no host-side simulated producer is accepted")
        from .live_service import LiveAcquisition
        live = LiveAcquisition(arguments.data / "lcd", session.echo)
        live.start_worker()
    server = create_server(arguments.bind, arguments.port, arguments.peer, token, session, arguments.data, live=live,
                           local_display_independent=arguments.local_display_independent)

    def stop(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    print(f"UT_DEVICE http://{arguments.bind}:{server.server_address[1]} SOURCE={session.mode}", flush=True)
    print(f"LOGIN_USER=operator TOKEN_FILE={token_path.resolve()}", flush=True)
    print(json.dumps(server.device_status, ensure_ascii=False), flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        if live is not None:
            live.close()
        server.server_close()
        (session.root / "report.html").write_text(render_dashboard(session.snapshot()), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
