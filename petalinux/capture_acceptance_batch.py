import argparse
import csv
import hashlib
import io
import json
import os
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from board_acceptance import BoardClient, validate_token
from monitor_camera_readonly import direct_link_guard


ROLES = ("UNDRILLED_REFERENCE", "HOLE_TARGET", "NORMAL_CONTROL")


class ReadOnlyBoardClient(BoardClient):
    def request(self, route, payload=None):
        if payload is not None or route not in ("/api/device", "/api/live/waveform"):
            raise PermissionError("Acceptance recording permits only device and waveform GET requests")
        direct_link_guard()
        return super().request(route)


def verified_frame(payload):
    if not isinstance(payload, dict) or payload.get("schema") != "ut-live-waveform/1":
        raise ValueError("Expected the live waveform API schema")
    record, samples = payload.get("record", {}), payload.get("samples")
    if (not isinstance(record, dict) or record.get("schema") != "ut-raw-capture/2" or record.get("source") != "FPGA_ADC_PULSED"
            or record.get("mode") != "PULSED" or record.get("sample_count") != 4096
            or record.get("sample_period_ns") != 40 or payload.get("sample_period_us") != .04
            or type(payload.get("trigger_index")) is not int or payload["trigger_index"] != 0):
        raise ValueError("Only complete 25MSPS pulsed ADC frames may enter this acceptance batch")
    if not isinstance(samples, list) or len(samples) != 4096 or any(type(value) is not int or not 0 <= value <= 255 for value in samples):
        raise ValueError("ADC samples must contain exactly 4096 unsigned eight-bit integers")
    if (type(record.get("gain_code")) is not int or not 0 <= record["gain_code"] <= 224
            or type(record.get("overrange")) is not bool or type(record.get("sequence")) is not int
            or not 0 <= record["sequence"] <= 0xffffffff
            or type(record.get("monotonic_ns")) is not int or record["monotonic_ns"] <= 0
            or record.get("transmit_enabled_after_capture") is not False or record.get("motion_enabled") is not False):
        raise ValueError("Invalid ADC frame metadata")
    if record.get("minimum") != min(samples) or record.get("maximum") != max(samples):
        raise ValueError("ADC range metadata does not match sample codes")
    if hashlib.sha256(bytes(samples)).hexdigest() != record.get("sample_sha256"):
        raise ValueError("ADC sample hash mismatch")
    if not isinstance(payload.get("frame_id"), str):
        raise ValueError("Expected a string ADC frame identity")
    identity = payload["frame_id"].split(":")
    if len(identity) != 3 or identity[0] != "adc":
        raise ValueError("Expected the original ADC frame identity")
    try:
        boot_id = str(uuid.UUID(identity[1]))
        sequence = int(identity[2])
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Invalid ADC frame identity") from error
    if sequence != record["sequence"] or identity[1] != boot_id:
        raise ValueError("Frame identity does not match ADC metadata")
    if not isinstance(payload.get("diagnosis"), dict):
        raise ValueError("The original board diagnosis is missing")
    return boot_id, record


def waveform_csv(payload):
    verified_frame(payload)
    record = payload["record"]
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("Sample in Buffer", "TRIGGER", "ad_data[7:0]", "ad_otr_r", "gain_val[7:0]", "Sample Period ns", "Source Kind"))
    writer.writerow(("UNSIGNED", "UNSIGNED", "UNSIGNED", "HEX", "HEX", "UNSIGNED", "ASCII"))
    for index, value in enumerate(payload["samples"]):
        writer.writerow((index, int(index == 0), value, int(record["overrange"]), format(record["gain_code"], "02x"), 40, "LIVE"))
    return output.getvalue().encode("utf-8")


def validate_labels(labels):
    if labels.get("record_role") not in ROLES:
        raise ValueError("Choose an explicit complete-block, hole-target or normal-control recording role")
    for name in ("specimen_id", "placement_id"):
        if not isinstance(labels.get(name), str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", labels[name]) is None:
            raise ValueError("Use a short ASCII specimen and placement identity")
    hole_id = labels.get("hole_id")
    if hole_id is not None and (not isinstance(hole_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", hole_id) is None):
        raise ValueError("Use a short ASCII hole identity")
    if labels["record_role"] == "HOLE_TARGET" and hole_id is None:
        raise ValueError("A hole-target recording requires a hole identity")
    depth = labels.get("nominal_reflector_depth_from_probe_face_mm")
    if depth is not None and (type(depth) not in (float, int) or not 0 < depth < 30):
        raise ValueError("Nominal reference depth must be inside the 30mm test block")
    if type(labels.get("independent_recoupling_confirmed")) is not bool:
        raise ValueError("Recoupling confirmation must be explicit")


def record_batch(client, output, labels, *, frame_count=5, duration=12., interval=.08, clock=time.monotonic, sleeper=time.sleep):
    validate_labels(labels)
    if type(frame_count) is not int or not 1 <= frame_count <= 20 or not 1 <= duration <= 30 or not .02 <= interval <= 1:
        raise ValueError("Use 1..20 frames, a 1..30 second deadline and a bounded polling interval")
    output = Path(output).absolute()
    if output.exists() or any(parent.is_symlink() for parent in output.parents):
        raise ValueError("Use a new non-symlink recording directory")
    device = client.read_json("/api/device")
    if (device.get("runtime") != "ARM_LINUX" or device.get("machine") != "armv7l"
            or device.get("real_capture", {}).get("active") is not True
            or device.get("real_capture", {}).get("state") != "RUNNING"):
        raise ValueError("Start real acquisition on the local screen first; this recorder never starts or renews acquisition")
    session = device.get("service_session")
    if not isinstance(session, str) or not session:
        raise ValueError("Missing board service identity")
    output.mkdir(parents=True)
    start = clock()
    metadata = {"schema": "ut-acceptance-batch/1", **labels, "started_utc": datetime.now(timezone.utc).isoformat(),
                "service_session": session, "repeat_protocol": "ONE_PLACEMENT_CONTINUOUS_FRAMES_NOT_INDEPENDENT",
                "raw_representation": "ADC_CODES_FROM_AUTHENTICATED_LIVE_WAVEFORM_API",
                "overrange_representation": "FRAME_AGGREGATE_REPEATED_IN_CSV_COLUMN",
                "known_labels_used_for_prediction": False, "acquisition_started_or_renewed_by_recorder": False,
                "raw_binary_header_hash_verified": False, "frames": [], "status": "RECORDING"}
    seen, boot_id, gain, last_timestamp = {}, None, None, 0
    failure = None
    try:
        while len(metadata["frames"]) < frame_count and clock() - start < duration:
            payload = client.read_json("/api/live/waveform")
            current_boot, record = verified_frame(payload)
            frame_id = payload["frame_id"]
            fingerprint = (record["sample_sha256"], record["monotonic_ns"], record["gain_code"], record["overrange"])
            if frame_id in seen:
                if seen[frame_id] != fingerprint:
                    raise ValueError("The same ADC frame identity was returned with different content")
                sleeper(interval)
                continue
            if boot_id is not None and (current_boot != boot_id or record["gain_code"] != gain or record["monotonic_ns"] <= last_timestamp):
                raise ValueError("Board boot, gain or ADC time changed during one placement")
            boot_id, gain, last_timestamp = current_boot, record["gain_code"], record["monotonic_ns"]
            index = len(metadata["frames"]) + 1
            name = f"frame_{index:03d}.csv"
            data = waveform_csv(payload)
            with (output / name).open("xb") as stream:
                stream.write(data)
            metadata["frames"].append({"index": index, "file": name, "frame_id": frame_id,
                "sha256": hashlib.sha256(data).hexdigest(), "sample_sha256": record["sample_sha256"],
                "record": record, "diagnosis": payload["diagnosis"],
                "received_utc": datetime.now(timezone.utc).isoformat()})
            seen[frame_id] = fingerprint
        final = client.read_json("/api/device")
        if final.get("service_session") != session:
            raise ValueError("Service restarted during this recording; retain files for review")
        metadata["status"] = "COMPLETE" if len(metadata["frames"]) == frame_count else "INCOMPLETE"
    except Exception as error:
        metadata["status"] = "ABORTED"
        failure = error
        metadata["failure_type"] = type(error).__name__
    finally:
        metadata["elapsed_seconds"] = round(clock() - start, 3)
        metadata["requested_frames"] = frame_count
        metadata["diagnosis_counts"] = dict(Counter(entry["diagnosis"].get("status", "UNKNOWN") for entry in metadata["frames"]))
        (output / "acquisition.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if failure is not None:
        raise failure
    return {"status": metadata["status"], "frames_saved": len(metadata["frames"]),
            "elapsed_seconds": metadata["elapsed_seconds"], "diagnoses": metadata["diagnosis_counts"],
            "directory": str(output), "can_release_probe": True}


def main():
    parser = argparse.ArgumentParser(description="Quick read-only recording of already-running LIVE ADC frames; no start, keepalive, gain, motion or Flash operations")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--specimen", required=True)
    parser.add_argument("--placement", required=True)
    parser.add_argument("--role", choices=ROLES, required=True)
    parser.add_argument("--hole")
    parser.add_argument("--nominal-depth", type=float)
    parser.add_argument("--recoupling-confirmed", action="store_true")
    parser.add_argument("--frames", type=int, default=5)
    parser.add_argument("--deadline", type=float, default=12.)
    arguments = parser.parse_args()
    labels = {"record_role": arguments.role, "specimen_id": arguments.specimen,
              "placement_id": arguments.placement, "hole_id": arguments.hole,
              "nominal_reflector_depth_from_probe_face_mm": arguments.nominal_depth,
              "independent_recoupling_confirmed": arguments.recoupling_confirmed}
    validate_labels(labels)
    direct_link_guard()
    client = ReadOnlyBoardClient("192.168.77.2", 8766, validate_token(os.environ.pop("UT_BOARD_OPERATOR_TOKEN", "")))
    result = record_batch(client, arguments.out, labels, frame_count=arguments.frames, duration=arguments.deadline)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
