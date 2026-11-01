import json
import re
import threading
import time
from pathlib import Path

from .echo import EchoConfig, analyze
from .live_capture import LinuxCapture
from .live_result import encode_result
from .mapping import envelope


class LiveAcquisition:
    def __init__(self, directory, echo=None, *, factory=LinuxCapture, clock=time.monotonic_ns):
        self.directory = Path(directory)
        self.echo = echo or EchoConfig()
        self.factory, self.clock = factory, clock
        self.condition = threading.Condition(threading.RLock())
        self.stop_event = threading.Event()
        self.worker = None
        self.available = False
        self.owner = None
        self.lease_deadline = 0
        self.gain = 128
        self.latest = None
        self.latest_waveform = None
        self.latest_diagnosis = None
        self.fault = ""
        self.boot_id = None
        self.in_flight = False

    def _active(self):
        return self.available and not self.fault and self.owner is not None and self.clock() < self.lease_deadline

    def state(self):
        with self.condition:
            active = self._active()
            fresh = active and self.latest is not None and 0 <= self.clock() - self.latest.monotonic_ns <= 1_000_000_000
            result = {"schema": "ut-realtime/1", "available": self.available, "active": bool(active),
                      "state": "FAULT" if self.fault else "RUNNING" if active else "STOPPED",
                      "error": self.fault, "gain_code": self.gain, "fresh": bool(fresh),
                      "operator_keepalive_required": True, "keepalive_timeout_ms": 1000,
                      "transmit_policy": "ONE_EXPLICIT_GUARDED_PULSE_PER_FRAME_THEN_OFF",
                      "calibrated": self.echo.calibrated, "motion_enabled": False}
            result["capture_in_flight"] = self.in_flight
            result["stop_settled"] = not active and not self.in_flight
            if fresh:
                result.update(record=self.latest.record(), preview=envelope(self.latest_waveform),
                              diagnosis=dict(self.latest_diagnosis))
            return result

    def start(self, payload):
        if set(payload) != {"owner", "gain", "probe_coupled", "safe_to_pulse"} or payload.get("probe_coupled") is not True or payload.get("safe_to_pulse") is not True:
            raise ValueError("Explicit current coupled-probe and safe-to-pulse confirmations are required")
        owner, gain = payload["owner"], payload["gain"]
        if not isinstance(owner, str) or re.fullmatch(r"(?:pc|lcd)-[a-f0-9]{32}", owner) is None:
            raise ValueError("Choose a unique PC or local-screen operator session")
        if type(gain) is not int or not 0 <= gain <= 224:
            raise ValueError("Gain must be an integer in 0..0xE0")
        with self.condition:
            if not self.available or self.fault:
                raise ValueError("Guarded real ADC transport is unavailable: " + self.fault)
            if self._active() and self.owner != owner:
                raise ValueError("Another operator owns live capture; stop it before taking control")
            self.owner, self.gain = owner, gain
            self.latest = self.latest_waveform = None
            self.latest_diagnosis = None
            self.lease_deadline = self.clock() + 1_000_000_000
            self.condition.notify_all()
        return self.state()

    def keepalive(self, payload):
        if set(payload) != {"owner"}:
            raise ValueError("A keepalive must contain only the current operator session")
        with self.condition:
            if not self._active() or payload["owner"] != self.owner:
                raise ValueError("Operator lease expired or belongs to another screen; a heartbeat cannot restart capture")
            self.lease_deadline = self.clock() + 1_000_000_000
            self.condition.notify_all()
        return self.state()

    def stop(self):
        with self.condition:
            self.owner = None
            self.lease_deadline = 0
            (self.directory / "live.utc2").unlink(missing_ok=True)
            (self.directory / "live-result.bin").unlink(missing_ok=True)
            self.condition.notify_all()
        return self.state()

    def capture(self, request_id, stop_event):
        requested = self.clock()
        deadline = time.monotonic() + 2
        with self.condition:
            while True:
                if stop_event.is_set():
                    raise ValueError("Map acquisition stopped; no point was committed")
                if not self._active():
                    raise ValueError("Start and monitor real pulsed acquisition before recording a map point")
                if self.latest_waveform is not None and self.latest_waveform.acquired_monotonic_ns >= requested:
                    return self.latest_waveform
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("No new ADC frame followed this map request; no stale point was committed")
                self.condition.wait(min(remaining, 0.05))

    def mapping_frame(self):
        with self.condition:
            waveform = self.latest_waveform
            if (not self._active() or waveform is None or
                    not 0 <= self.clock() - waveform.acquired_monotonic_ns <= 300_000_000):
                return None, None
            return self.owner, waveform

    def configure_echo(self, config):
        if not isinstance(config, EchoConfig):
            raise ValueError("Expected validated echo configuration")
        with self.condition:
            if self._active() or self.in_flight:
                raise ValueError("Stop and settle acquisition before changing echo configuration")
            self.echo = config
            self.latest = self.latest_waveform = self.latest_diagnosis = None
            for name in ("live-result.bin", "live.utc2"):
                (self.directory / name).unlink(missing_ok=True)
            self.condition.notify_all()

    def start_worker(self):
        if self.worker is not None:
            raise ValueError("The acquisition worker can only be started once")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.worker = threading.Thread(target=self._run, name="ut-real-adc", daemon=True)
        self.worker.start()

    def close(self):
        self.stop()
        self.stop_event.set()
        with self.condition:
            self.condition.notify_all()
        if self.worker is not None:
            self.worker.join(timeout=2)
            if self.worker.is_alive():
                raise RuntimeError("Capture worker did not exit; hardware TX lease must time out before further board operations")

    def _publish(self, frame, diagnosis):
        result = self.directory / "live-result.bin.tmp"
        result.write_bytes(encode_result(frame, diagnosis, self.echo))
        result.replace(self.directory / "live-result.bin")
        temporary = self.directory / "live.utc2.tmp"
        temporary.write_bytes(frame.raw)
        temporary.replace(self.directory / "live.utc2")

    def _mailbox(self):
        path = self.directory / "live-command.json"
        try:
            command = path.read_bytes()
        except FileNotFoundError:
            return
        if len(command) > 1024:
            raise ValueError("Oversized local-screen live command")
        path.unlink()
        payload = json.loads(command)
        if not isinstance(payload, dict) or set(payload) != {"action", "payload", "monotonic_ns"}:
            raise ValueError("Invalid local-screen live command")
        timestamp = payload["monotonic_ns"]
        if type(timestamp) is not int or not 0 <= self.clock() - timestamp <= 500_000_000:
            raise ValueError("Stale local-screen command cannot arm or refresh transmission")
        action = payload["action"]
        if action == "stop":
            self.stop()
        elif action == "start":
            self.start(payload["payload"])
        elif action == "keepalive":
            self.keepalive(payload["payload"])
        else:
            raise ValueError("Unknown local-screen live action")

    def _run(self):
        try:
            with self.factory() as capture:
                with self.condition:
                    self.boot_id = capture.boot_id
                    self.available = True
                    self.condition.notify_all()
                while not self.stop_event.is_set():
                    try:
                        self._mailbox()
                    except (ValueError, KeyError, TypeError, UnicodeError):
                        self.stop()
                    with self.condition:
                        if not self._active():
                            if self.owner is not None:
                                self.stop()
                            self.condition.wait(0.05)
                            continue
                        owner, gain = self.owner, self.gain
                        self.in_flight = True
                    started = time.monotonic()
                    frame = capture.capture_once("PULSED", gain, operator_ready=True)
                    waveform = frame.waveform(self.boot_id)
                    diagnosis = analyze(waveform, self.echo)
                    with self.condition:
                        self.in_flight = False
                        if self._active() and owner == self.owner and gain == self.gain:
                            self._publish(frame, diagnosis)
                            self.latest, self.latest_waveform = frame, waveform
                            self.latest_diagnosis = diagnosis
                            self.condition.notify_all()
                    self.stop_event.wait(max(0, 0.1 - (time.monotonic() - started)))
        except (OSError, ValueError, RuntimeError) as error:
            with self.condition:
                self.fault = str(error)
        finally:
            with self.condition:
                self.available = False
                self.owner = None
                self.in_flight = False
                self.condition.notify_all()
