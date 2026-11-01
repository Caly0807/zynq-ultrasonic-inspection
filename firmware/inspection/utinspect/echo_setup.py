import hashlib
import json
import statistics
import struct
import time
from dataclasses import asdict, replace
from pathlib import Path

from .echo import EchoConfig, analyze, finite_number, write_lvgl_csv
from .records import atomic_json


STATUS = struct.Struct("<4sIQ5I16f")
PHASES = {"IDLE": 0, "COLLECTING": 1, "READY": 2, "REJECTED": 3, "APPLYING": 4, "APPLIED": 5}
REASONS = {"WAITING": 0, "NEED_LOCAL_LIVE": 1, "INVALID_CONFIGURATION": 2, "BACKWALL_MISSING": 3,
           "REFERENCE_FLAW_GATE_CROSSED": 4, "QUIET_GATE_NOISY": 5, "UNSTABLE_REFERENCE": 6,
           "REFERENCE_EXPIRED": 7, "NEW_SESSION_REQUIRED": 8, "PREVIEW_APPLIED": 9,
           "GAIN_CHANGED": 10, "REVIEW_REQUIRED": 11, "CALIBRATION_APPLIED": 12, "CLIPPING": 13}
FIELDS = ("flaw_start", "flaw_end", "wall_start", "wall_end", "quiet_start", "quiet_end",
          "flaw_threshold", "wall_threshold", "sound_speed", "time_zero", "thickness")
BOUNDS = ((0, 80), (.04, 100), (.04, 140), (.08, 160), (0, 155), (.04, 163),
          (1, 120), (1, 120), (1000, 10000), (0, 80), (1, 100))


class EchoSetup:
    def __init__(self, directory, session, live, *, clock=time.monotonic_ns):
        self.directory, self.session, self.live, self.clock = Path(directory), session, live, clock
        config = session.echo
        self.values = [*config.flaw_gate_us, *config.backwall_gate_us, *(config.baseline_gate_us or (100., 110.)),
                       config.flaw_threshold_codes, config.backwall_threshold_codes, config.sound_speed_mps,
                       config.time_zero_us, session.spec.thickness_mm]
        self.phase, self.reason = "IDLE", "WAITING"
        self.frames, self.seen = [], set()
        self.started, self.finished = 0, 0
        self.owner, self.gain, self.proposal = None, None, None
        self.peak_flaw, self.peak_wall, self.noise, self.zero = 0., 0., 0., 0.
        self.pending = None
        self.reference_id = None

    def _config(self):
        try:
            return EchoConfig(flaw_gate_us=tuple(self.values[:2]), backwall_gate_us=tuple(self.values[2:4]),
                              baseline_gate_us=tuple(self.values[4:6]), flaw_threshold_codes=self.values[6],
                              backwall_threshold_codes=self.values[7], sound_speed_mps=self.values[8],
                              time_zero_us=self.values[9])
        except ValueError:
            self._reject("INVALID_CONFIGURATION")
            raise

    def _reject(self, reason):
        self.phase, self.reason, self.pending = "REJECTED", reason, None

    def _empty_session(self):
        if self.session.snapshot()["point_count"]:
            self._reject("NEW_SESSION_REQUIRED")
            raise ValueError("Create a new empty scan before changing acoustic calibration")

    def command(self, action, payload):
        if not isinstance(payload, dict):
            raise ValueError("Expected an acoustic setup object")
        if self.phase == "APPLYING":
            raise ValueError("Wait until stopped acquisition settles and configuration is applied")
        if action == "echo-set" and set(payload) == {"parameter", "value"}:
            parameter, value = payload["parameter"], payload["value"]
            if type(parameter) is not int or not 0 <= parameter < len(FIELDS) or not finite_number(value):
                raise ValueError("Expected one finite acoustic draft field")
            if not BOUNDS[parameter][0] <= value <= BOUNDS[parameter][1]:
                raise ValueError("Acoustic draft value is outside supported bounds")
            self.values[parameter] = float(value)
            self.phase, self.reason, self.proposal = "IDLE", "WAITING", None
            self.frames, self.seen = [], set()
        elif action == "echo-reference" and set(payload) == {"reference_confirmed", "normal_incidence_confirmed"}:
            if payload["reference_confirmed"] is not True or payload["normal_incidence_confirmed"] is not True:
                raise ValueError("Explicit known complete-block and normal-incidence confirmations are required")
            self._empty_session()
            self._config()
            owner, frame = self.live.mapping_frame()
            if owner is None or not owner.startswith("lcd-") or frame is None or frame.source != "LIVE":
                self._reject("NEED_LOCAL_LIVE")
                raise ValueError("Explicitly start real acquisition on the local screen before collecting a reference")
            self.owner, self.gain, self.started, self.finished = owner, frame.gain, self.clock(), 0
            self.phase, self.reason, self.proposal = "COLLECTING", "WAITING", None
            self.frames, self.seen = [], set()
        elif action == "echo-preview" and not payload:
            self._empty_session()
            self.pending = self._config()
            self.phase, self.reason = "APPLYING", "WAITING"
            self.started = self.clock()
            self.live.stop()
        elif action == "echo-approve" and set(payload) == {"review_confirmed"} and payload["review_confirmed"] is True:
            self._empty_session()
            owner, frame = self.live.mapping_frame()
            if (self.phase != "READY" or self.proposal is None or not 0 <= self.clock() - self.finished <= 60_000_000_000 or
                    owner != self.owner or frame is None or frame.gain != self.gain):
                self._reject("REFERENCE_EXPIRED")
                raise ValueError("Collect and review a fresh current-setup reference before approval")
            self.pending = self.proposal
            self.phase, self.reason = "APPLYING", "WAITING"
            self.started = self.clock()
            self.live.stop()
        else:
            raise ValueError("Unknown acoustic setup action or confirmation")

    def _review(self):
        config = self._config()
        results = [analyze(frame, config) for frame in self.frames]
        self.peak_flaw = max(result.get("flaw", {}).get("amplitude_codes", 0) for result in results)
        self.peak_wall = min(result.get("backwall", {}).get("amplitude_codes", 0) for result in results)
        self.noise = max(result.get("noise_codes", 0) for result in results)
        if any(frame.overrange or any(value in (0, 255) for value in frame.samples) for frame in self.frames):
            self._reject("CLIPPING")
        elif any(result.get("reason") in ("baseline_gate_outside_acquisition_window", "gate_outside_acquisition_window",
                                           "insufficient_baseline_select_a_verified_quiet_gate") for result in results):
            self._reject("INVALID_CONFIGURATION")
        elif any(result.get("flaw_gate_crossed") for result in results):
            self._reject("REFERENCE_FLAW_GATE_CROSSED")
        elif any(not result.get("backwall_visible") for result in results):
            self._reject("BACKWALL_MISSING")
        else:
            quiet = [[value for index, value in enumerate(frame.samples)
                      if config.baseline_gate_us[0] <= (index - frame.trigger_index) * frame.sample_period_us <= config.baseline_gate_us[1]]
                     for frame in self.frames]
            quiet_peak = max(abs(value - result["baseline"]) for samples, result in zip(quiet, results) for value in samples)
            times = [result["backwall"]["time_us"] for result in results]
            amplitudes = [result["backwall"]["amplitude_codes"] for result in results]
            self.zero = statistics.median(times) - 2000 * self.values[10] / config.sound_speed_mps
            if self.noise > config.backwall_threshold_codes / config.minimum_snr or quiet_peak > max(4., self.noise * 6):
                self._reject("QUIET_GATE_NOISY")
            elif max(times) - min(times) > .2 or max(amplitudes) > min(amplitudes) * 2:
                self._reject("UNSTABLE_REFERENCE")
            elif not 0 <= self.zero < config.flaw_gate_us[0]:
                self._reject("INVALID_CONFIGURATION")
            else:
                self.reference_id = hashlib.sha256(json.dumps({"values": self.values, "frames": [frame.frame_id for frame in self.frames]}, sort_keys=True).encode()).hexdigest()[:24]
                self.proposal = replace(config, time_zero_us=self.zero, calibrated=True,
                                        calibration_id="LCD-REF-" + self.reference_id, reference_gain_code=self.gain)
                self.phase, self.reason, self.finished = "READY", "REVIEW_REQUIRED", self.clock()
        report = {"schema": "ut-acoustic-reference/1", "source": "LIVE_LOCAL_OPERATOR_REFERENCE",
                  "phase": self.phase, "reason": self.reason, "draft": asdict(config), "entered_thickness_mm": self.values[10],
                  "thickness_and_sound_speed_basis": "OPERATOR_ENTERED_NOT_INDEPENDENT_METROLOGY",
                  "proposed_time_zero_us": self.zero, "gain_code": self.gain, "results": results,
                  "calibration_approved": False, "real_flaw_detection_verified": False}
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / ("echo-reference-" + str(self.started))
        destination.mkdir(exist_ok=False)
        for index, frame in enumerate(self.frames, 1):
            write_lvgl_csv(destination / f"frame_{index:02d}.csv", frame)
        atomic_json(destination / "review.json", report)

    def tick(self):
        now = self.clock()
        if self.phase == "COLLECTING":
            owner, frame = self.live.mapping_frame()
            if owner != self.owner or frame is None or frame.source != "LIVE":
                self._reject("NEED_LOCAL_LIVE")
            elif frame.gain != self.gain:
                self._reject("GAIN_CHANGED")
            elif now - self.started > 5_000_000_000:
                self._reject("UNSTABLE_REFERENCE")
            elif (frame.frame_id not in self.seen and frame.acquired_monotonic_ns is not None and
                  self.started <= frame.acquired_monotonic_ns <= now and now - frame.acquired_monotonic_ns <= 300_000_000):
                self.frames.append(frame)
                self.seen.add(frame.frame_id)
                if len(self.frames) == 8:
                    self._review()
        elif self.phase == "APPLYING":
            state = self.live.state()
            if now - self.started > 2_000_000_000:
                self._reject("UNSTABLE_REFERENCE")
            elif not state.get("active") and not state.get("capture_in_flight"):
                with self.session.lock:
                    self._empty_session()
                    self.live.configure_echo(self.pending)
                    self.session.echo = self.pending
                    self.phase = "APPLIED"
                    self.reason = "CALIBRATION_APPLIED" if self.pending.calibrated else "PREVIEW_APPLIED"
                    atomic_json(self.directory / "echo-active.json", {"schema": "ut-local-echo-config/1", "echo": asdict(self.pending),
                                "updated_monotonic_ns": now, "persistent": False, "real_flaw_detection_verified": False})
                    self.values[9] = self.pending.time_zero_us
                    self.pending = None
        state = self.live.state()
        flags = int(bool(state.get("active"))) | int(self.session.echo.calibrated) << 1 | int(self.session.echo.baseline_gate_us is not None) << 2
        age = max(0., (now - self.finished) / 1_000_000) if self.finished else -1.
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / "echo-status.bin.tmp"
        temporary.write_bytes(STATUS.pack(b"UTE1", flags, now, PHASES[self.phase], REASONS[self.reason], len(self.frames), 8,
                                          self.gain or 0, *self.values, self.peak_flaw, self.peak_wall, self.noise, self.zero, min(age, 1_000_000.)))
        temporary.replace(self.directory / "echo-status.bin")
        diagnostics = {"schema": "ut-echo-setup/1", "phase": self.phase, "reason": self.reason, "collected": len(self.frames),
                       "target": 8, "updated_monotonic_ns": now, "active_calibrated": self.session.echo.calibrated,
                       "active_baseline_configured": self.session.echo.baseline_gate_us is not None,
                       "gain_code": self.gain, "draft_values": dict(zip(FIELDS, self.values)),
                       "reference_peak_flaw_codes": self.peak_flaw, "reference_min_backwall_codes": self.peak_wall,
                       "reference_noise_codes": self.noise, "proposed_time_zero_us": self.zero,
                       "results_volatile": True, "real_flaw_detection_verified": False}
        atomic_json(self.directory / "echo-status.json", diagnostics)
        return diagnostics
