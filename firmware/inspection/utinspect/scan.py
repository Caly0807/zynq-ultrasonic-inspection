import csv
import hashlib
import json
import math
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from pathlib import Path

from .echo import Waveform, analyze, write_lvgl_csv
from .records import Observation, atomic_json


TERMINAL_STATES = ("COMPLETE", "STOPPED", "FAULT")


class StopRequested(Exception):
    pass


class CooperativeStopEvent(threading.Event):
    def __init__(self, poll):
        super().__init__()
        self.poll = poll

    def requested(self):
        return super().is_set()

    def is_set(self):
        self.poll()
        return self.requested()

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self.is_set():
                return True
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return False
            super().wait(0.02 if remaining is None else min(0.02, remaining))


@dataclass(frozen=True)
class ScanPlan:
    start_mm: float = 0.0
    end_mm: float = 50.0
    step_mm: float = 5.0
    soft_min_mm: float = 0.0
    soft_max_mm: float = 100.0
    settle_s: float = 0.2
    world_origin_x_mm: float = 70.0
    world_y_mm: float = 0.0
    position_tolerance_mm: float = 3.0
    max_pose_age_s: float = 0.5

    def __post_init__(self):
        if not all(type(value) in (int, float) and math.isfinite(value) for value in asdict(self).values()):
            raise ValueError("Scan settings must be finite numbers, not booleans or strings")
        if not self.soft_min_mm <= self.start_mm <= self.end_mm <= self.soft_max_mm:
            raise ValueError("Scan outside soft travel limits")
        if self.step_mm <= 0 or self.settle_s < 0 or self.max_pose_age_s <= 0 or self.position_tolerance_mm <= 0:
            raise ValueError("Invalid step, delay or position tolerance")
        if not all(math.isfinite(self.world_origin_x_mm + position) for position in (self.start_mm, self.end_mm)):
            raise ValueError("World coordinate range overflows")
        if self.point_count > 10000:
            raise ValueError("Scan would exceed 10000 points")

    @property
    def point_count(self):
        span = Decimal(str(self.end_mm)) - Decimal(str(self.start_mm))
        intervals = (span / Decimal(str(self.step_mm))).to_integral_value(rounding="ROUND_CEILING")
        return int(intervals) + 1

    def preview(self):
        positions = list(self.positions())
        return {"schema": 1, "plan": asdict(self), "point_count": len(positions), "positions_mm": positions,
                "world_x_range_mm": [self.world_origin_x_mm + positions[0], self.world_origin_x_mm + positions[-1]],
                "last_step_mm": positions[-1] - positions[-2] if len(positions) > 1 else 0.0,
                "settling_lower_bound_s": self.settle_s * len(positions),
                "lvgl_review_capacity": 256, "lvgl_review_compatible": len(positions) <= 256,
                "hardware_motion_driver": False, "transmit_enabled_by_this_software": False,
                "notes": ["Preview only; no movement, capture or transmission.",
                          "Settling time excludes movement, manual confirmation, positioning and acquisition.",
                          "World coordinates and soft limits are settings, not verified physical travel."]}

    def positions(self):
        current = Decimal(str(self.start_mm))
        ending, step = Decimal(str(self.end_mm)), Decimal(str(self.step_mm))
        while current < ending:
            yield float(current)
            current += step
        yield float(ending)


class SimulatedStage:
    simulated = True

    def move_to(self, position_mm):
        self.position_mm = position_mm

    def stop(self):
        pass


class ManualStage:
    simulated = False

    def move_to(self, position_mm):
        reply = input(f"Move probe manually to stage X={position_mm:g} mm; ENTER confirms; q stops: ")
        if reply.strip().lower() == "q":
            raise StopRequested()

    def stop(self):
        pass


class PoseFile:
    def __init__(self, path, timeout_s=2.0):
        self.path, self.timeout_s = Path(path), timeout_s

    def __call__(self, position_mm, settled_ns, stop_event):
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            if stop_event.is_set():
                raise StopRequested()
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                observation = Observation(**payload)
                if observation.timestamp_ns >= settled_ns:
                    return observation
            except (OSError, ValueError, TypeError):
                pass
            stop_event.wait(0.02)
        raise TimeoutError("No fresh pose after settling; run the vision process on this same host")


class ReplayCapture:
    def __init__(self, frames):
        self.frames = iter(frames)

    def capture(self, request_id, stop_event):
        frame = next(self.frames)
        return replace(frame, source="DEMO" if frame.source == "DEMO" else "RECORDED")


class ExchangeCapture:
    def __init__(self, directory, timeout_s=5.0):
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("Invalid capture timeout")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.seen_frame_ids = set()

    def capture(self, request_id, stop_event):
        request_path = self.directory / f"request_{request_id}.json"
        response_path = self.directory / f"response_{request_id}.json"
        if request_path.exists() or response_path.exists():
            raise ValueError("Refusing pre-existing acquisition request/response")
        request_ns = time.monotonic_ns()
        atomic_json(request_path, {"schema": 1, "request_id": request_id, "samples": 4096, "request_monotonic_ns": request_ns,
                                   "require_new_capture": True, "enable_transmit": False})
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            if stop_event.is_set():
                raise StopRequested()
            if response_path.exists():
                payload = json.loads(response_path.read_text(encoding="utf-8"))
                if payload.get("schema") != 1 or payload.get("request_id") != request_id:
                    raise ValueError("Acquisition response does not match request")
                if payload.get("capture_complete") is not True or payload.get("dma_complete") is not True:
                    raise ValueError("Capture AND DMA completion are required")
                data = payload["frame"]
                if data.get("source") != "LIVE" or not data.get("frame_id") or data["frame_id"] in self.seen_frame_ids:
                    raise ValueError("Acquisition requires a unique, explicitly LIVE frame")
                frame = Waveform(samples=tuple(data["samples"]), trigger_index=data["trigger_index"],
                                 sample_period_us=data["sample_period_us"], source=data["source"],
                                 gain=data.get("gain", 128), overrange=data.get("overrange", False), frame_id=data["frame_id"],
                                 acquired_monotonic_ns=data.get("acquired_monotonic_ns"))
                if len(frame.samples) != 4096:
                    raise ValueError("Expected a complete 4096-sample acquisition")
                if frame.acquired_monotonic_ns is None or not request_ns <= frame.acquired_monotonic_ns <= time.monotonic_ns():
                    raise ValueError("Frame timestamp must follow this request and use the SAME HOST monotonic clock")
                self.seen_frame_ids.add(frame.frame_id)
                return frame
            stop_event.wait(0.02)
        raise TimeoutError("Acquisition adapter did not answer; no hardware capture driver is bundled yet")


class Scanner:
    def __init__(self, plan, echo_config, stage, locate, capture, output):
        self.plan, self.echo_config = plan, echo_config
        self.stage, self.locate, self.capture = stage, locate, capture
        self.output = Path(output)
        self.stop_event = CooperativeStopEvent(self._poll_control)
        self.pause_event = threading.Event()
        self.state = "IDLE"
        self.run_id = uuid.uuid4().hex
        self.manifest = None
        self._processed_controls = set()

    def stop(self):
        self.stop_event.set()

    def pause(self):
        self.pause_event.set()

    def resume(self):
        self.pause_event.clear()

    def _poll_control(self):
        if self.manifest is None:
            return
        for path in sorted((self.output / "control").glob("*.json")):
            if path.name in self._processed_controls:
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(self.output.resolve()) or path.stat().st_size > 4096:
                raise ValueError("Invalid scan control file")
            command = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(command, dict) or type(command.get("schema")) is not int or command.get("schema") != 1 or command.get("run_id") != self.run_id:
                raise ValueError("Scan control belongs to another run")
            action = command.get("action")
            if action not in ("pause", "resume", "stop"):
                raise ValueError("Unknown scan control action")
            self._processed_controls.add(path.name)
            {"pause": self.pause, "resume": self.resume, "stop": self.stop}[action]()
            self.manifest["last_control"] = {"file": path.name, "action": action, "accepted_time_unix_ns": time.time_ns()}
            self._event("CONTROL_ACCEPTED", action=action, command_file=path.name)
            self._save_manifest()

    def _check(self, allow_pause=False):
        if self.stop_event.is_set():
            raise StopRequested()
        if allow_pause and self.pause_event.is_set():
            previous = self.state
            self._state("PAUSED")
            while self.pause_event.is_set():
                if self.stop_event.wait(0.02):
                    raise StopRequested()
            self._state(previous)

    def _event(self, state, **details):
        with (self.output / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"state": state, "monotonic_ns": time.monotonic_ns(),
                                     "time_unix_ns": time.time_ns(), **details}, allow_nan=False) + "\n")

    def _save_manifest(self):
        self.manifest.update(phase=self.state, updated_time_unix_ns=time.time_ns(),
                             pause_requested=self.pause_event.is_set(), stop_requested=self.stop_event.requested())
        for attempt in range(5):
            try:
                atomic_json(self.output / "manifest.json", self.manifest)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.01)

    def _state(self, state, **details):
        self.state = state
        self.manifest["status"] = state if state in (*TERMINAL_STATES, "PAUSED") else "RUNNING"
        self.manifest.update(details)
        self._event(state, **details)
        self._save_manifest()

    def _validate_pose(self, observation, position_mm, settled_ns):
        if not observation.valid or observation.position_mm is None:
            raise ValueError(f"Vision rejected: {observation.reason}")
        now_ns = time.monotonic_ns()
        if not settled_ns <= observation.timestamp_ns <= now_ns or now_ns - observation.timestamp_ns > self.plan.max_pose_age_s * 1e9:
            raise ValueError("Vision pose is stale, from the future or from a different host clock")
        if len(observation.position_mm) != 3 or not all(math.isfinite(value) for value in observation.position_mm):
            raise ValueError("Invalid vision coordinates")
        expected = self.plan.world_origin_x_mm + position_mm
        if abs(observation.position_mm[0] - expected) > self.plan.position_tolerance_mm or abs(observation.position_mm[1] - self.plan.world_y_mm) > self.plan.position_tolerance_mm:
            raise ValueError("Probe position differs from the commanded scan line")
        if observation.source not in ("DEMO", "RECORDED", "LIVE"):
            raise ValueError("Unknown vision provenance")

    def run(self):
        if self.manifest is not None:
            raise ValueError("A Scanner instance can only run once")
        self.output.mkdir(parents=True, exist_ok=False)
        (self.output / "waveforms").mkdir()
        manifest = {"schema": 1, "run_id": self.run_id, "plan": asdict(self.plan), "echo": asdict(self.echo_config),
                    "stage": "SIMULATED" if self.stage.simulated else "MANUAL", "hardware_motion_driver": False,
                    "transmit_enabled_by_this_software": False, "status": "RUNNING", "point_count": 0,
                    "planned_point_count": self.plan.point_count, "started_time_unix_ns": time.time_ns(),
                    "cleanup_complete": False, "pause_policy": "BETWEEN_POINTS"}
        self.manifest = manifest
        fields = ["point_id", "stage_x_mm", "world_x_mm", "world_y_mm", "world_z_mm", "source", "status", "depth_mm", "waveform"]
        outcome = "COMPLETE"
        try:
            self._state("READY")
            with (self.output / "summary.csv").open("w", encoding="utf-8-sig", newline="") as summary:
                writer = csv.DictWriter(summary, fieldnames=fields)
                writer.writeheader()
                summary.flush()
                for point_index, position_mm in enumerate(self.plan.positions()):
                    self._check(allow_pause=True)
                    self._state("MOVING", point_index=point_index, target_mm=position_mm)
                    self.stage.move_to(position_mm)
                    self._check()
                    self._state("SETTLING")
                    if self.stop_event.wait(self.plan.settle_s):
                        raise StopRequested()
                    settled_ns = time.monotonic_ns()
                    self._state("LOCATING")
                    observation = self.locate(position_mm, settled_ns, self.stop_event)
                    self._validate_pose(observation, position_mm, settled_ns)
                    self._check()
                    point_id = f"{self.run_id}_{point_index:05d}"
                    self._state("ACQUIRING", request_id=point_id)
                    frame = self.capture.capture(point_id, self.stop_event)
                    self._check()
                    self._state("VERIFYING_POSITION")
                    after_capture_ns = time.monotonic_ns()
                    final_observation = self.locate(position_mm, after_capture_ns, self.stop_event)
                    self._validate_pose(final_observation, position_mm, after_capture_ns)
                    if math.dist(observation.position_mm, final_observation.position_mm) > self.plan.position_tolerance_mm:
                        raise ValueError("Probe moved during acquisition")
                    if frame.source == "LIVE" and observation.source != "LIVE":
                        raise ValueError("LIVE capture requires a LIVE pose")
                    if frame.source == "LIVE" and final_observation.source != "LIVE":
                        raise ValueError("LIVE capture requires a LIVE final pose")
                    if frame.source == "DEMO" or observation.source == "DEMO" or final_observation.source == "DEMO" or self.stage.simulated:
                        frame = replace(frame, source="DEMO")
                    diagnosis = analyze(frame, self.echo_config)
                    waveform_name = f"waveforms/{point_index:05d}.csv"
                    self._state("COMMITTING")
                    write_lvgl_csv(self.output / waveform_name, frame)
                    write_lvgl_csv(self.output / "latest.csv", frame)
                    self._check()
                    record = {"point_id": point_id, "point_index": point_index, "stage_x_mm": position_mm,
                              "vision": asdict(observation), "vision_after_capture": asdict(final_observation), "diagnosis": diagnosis, "waveform": waveform_name,
                              "source": frame.source, "frame_id": frame.frame_id, "time_unix_ns": time.time_ns(),
                              "waveform_sha256": hashlib.sha256((self.output / waveform_name).read_bytes()).hexdigest()}
                    with (self.output / "points.jsonl").open("a", encoding="utf-8") as journal:
                        journal.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                        journal.flush()
                        os.fsync(journal.fileno())
                    writer.writerow(dict(zip(fields, [point_id, position_mm, *observation.position_mm, frame.source,
                                                       diagnosis["status"], diagnosis["depth_mm"], waveform_name])))
                    summary.flush()
                    os.fsync(summary.fileno())
                    manifest["point_count"] += 1
                    self._state("POINT_COMMITTED", last_point_id=point_id)
                    print(f"POINT {point_index:03d} X={position_mm:g} {frame.source} {diagnosis['status']}", flush=True)
                self._check()
                self._state("FINISHING")
        except (StopRequested, KeyboardInterrupt):
            outcome = "STOPPED"
        except Exception as error:
            manifest["error"] = str(error)
            outcome = "FAULT"
            raise
        finally:
            try:
                self.stage.stop()
                manifest["cleanup_complete"] = True
            except Exception as error:
                outcome = "FAULT"
                manifest["stop_error"] = str(error)
                raise
            finally:
                manifest["finished_time_unix_ns"] = time.time_ns()
                self._state(outcome)
        return manifest


def scan_status(directory):
    for attempt in range(5):
        try:
            manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.01)
    if not isinstance(manifest, dict) or manifest.get("schema") != 1 or not manifest.get("run_id"):
        raise ValueError("Invalid scan manifest")
    status = manifest.get("status")
    if status not in (*TERMINAL_STATES, "RUNNING", "PAUSED"):
        raise ValueError("Unknown scan status")
    result = dict(manifest)
    result["activity_confirmed"] = False
    result["activity_note"] = "File snapshot only; RUNNING is not proof that a process or hardware is alive."
    return result


def request_control(directory, action):
    if action not in ("pause", "resume", "stop"):
        raise ValueError("Unknown scan control action")
    output = Path(directory)
    manifest = scan_status(output)
    if manifest["status"] in TERMINAL_STATES:
        raise ValueError("Cannot control a finished scan")
    command = {"schema": 1, "run_id": manifest["run_id"], "action": action, "requested_time_unix_ns": time.time_ns()}
    destination = output / "control" / f"{command['requested_time_unix_ns']:020d}_{uuid.uuid4().hex}.json"
    if not destination.resolve().is_relative_to(output.resolve()):
        raise ValueError("Unsafe scan control directory")
    atomic_json(destination, command)
    return {**command, "command_file": str(destination.resolve()), "state": "REQUESTED_NOT_CONFIRMED",
            "note": "Cooperative software control, NOT an emergency stop. Pause occurs between points; manual input must return first."}
