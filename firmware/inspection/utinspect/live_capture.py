import argparse
import hashlib
import json
import os
import stat
import struct
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .echo import Waveform


CAPS = struct.Struct("<8I")
HEADER = struct.Struct("<4s7IQ")
FRAME_BYTES = HEADER.size + 4096
CAPS_IOCTL = 0x80205520
CAPTURE_IOCTL = 0xC0005521 | (FRAME_BYTES << 16)
MODES = {"TEST": 1, "PASSIVE": 2, "PULSED": 3}


def decode_caps(data):
    if len(data) != CAPS.size:
        raise ValueError("Truncated live-capture capability record")
    if CAPS.unpack(data) != (0x32564C55, 2, 0xE, 4096, 40, 0xE0, 250000, 0):
        raise ValueError("Guarded real-capture hardware/driver pair is unavailable; no trigger was requested")
    return {"schema": "ut-live-caps/2", "modes": list(MODES), "samples": 4096,
            "sample_period_ns": 40, "max_gain": 224, "hardware_tx_lease_us": 250000}


@dataclass(frozen=True)
class RawCapture:
    raw: bytes
    sequence: int
    status: int
    gain: int
    mode: str
    monotonic_ns: int

    @property
    def samples(self):
        return self.raw[HEADER.size:]

    def record(self):
        return {"schema": "ut-raw-capture/2", "source": "FPGA_DMA_TEST_NOT_ULTRASOUND" if self.mode == "TEST"
                else "FPGA_ADC_" + self.mode, "sequence": self.sequence, "mode": self.mode,
                "sample_count": 4096, "sample_period_ns": 40, "gain_code": self.gain,
                "overrange": bool(self.status & 0x10), "monotonic_ns": self.monotonic_ns,
                "minimum": min(self.samples), "maximum": max(self.samples),
                "sample_sha256": hashlib.sha256(self.samples).hexdigest(),
                "raw_sha256": hashlib.sha256(self.raw).hexdigest(),
                "transmit_enabled_after_capture": False, "motion_enabled": False,
                "calibrated": False, "thickness_or_defect_verdict_authorized": False,
                "timing_note": "25 MSPS ADC samples; pulsed frame starts after fire acknowledgement; time zero needs calibration"}

    def waveform(self, boot_id):
        if self.mode != "PULSED":
            raise ValueError("Only a newly requested pulsed ADC frame can enter the LIVE ultrasonic map")
        return Waveform(tuple(self.samples), 0, 0.04, "LIVE", self.gain, bool(self.status & 0x10),
                        "adc:" + str(uuid.UUID(boot_id)) + ":" + str(self.sequence), self.monotonic_ns)


def decode_frame(data, *, mode, gain, requested_ns, now_ns, previous_sequence=None):
    if len(data) != FRAME_BYTES or mode not in MODES:
        raise ValueError("Truncated, oversized or unknown real-capture frame")
    magic, version, sequence, flags, period, count, actual_gain, actual_mode, timestamp = HEADER.unpack_from(data)
    if (magic, version, period, count, actual_gain, actual_mode) != (b"UTC2", 2, 40, 4096, gain, MODES[mode]):
        raise ValueError("Real-capture ABI, gain or requested mode does not match")
    if flags & ~0x3FFF or flags & 0x30C9 or not flags & 4:
        raise ValueError("Capture is incomplete, rejected, still transmitting or has an expired safety lease")
    if bool(flags & 0x100) != (mode == "TEST") or bool(flags & 0x800) != (mode == "PASSIVE"):
        raise ValueError("TEST/PASSIVE/PULSED source flags do not match the request")
    if type(requested_ns) is not int or type(now_ns) is not int or not 0 < requested_ns <= timestamp <= now_ns:
        raise ValueError("Capture timestamp must follow this request on the same Linux monotonic clock")
    if now_ns - timestamp > 2_000_000_000:
        raise ValueError("Capture is stale; it cannot be presented as LIVE")
    if previous_sequence is not None and sequence != (previous_sequence + 1) & 0xFFFFFFFF:
        raise ValueError("Capture sequence was repeated or changed outside this producer")
    samples = data[HEADER.size:]
    if mode == "TEST" and samples != bytes(range(256)) * 16:
        raise ValueError("DMA TEST payload does not match the FPGA ramp")
    return RawCapture(bytes(data), sequence, flags, gain, mode, timestamp)


class LinuxCapture:
    def __init__(self, device=Path("/dev/ut_board")):
        self.device = Path(device)
        self.descriptor = None
        self.previous_sequence = None
        self.boot_id = None

    def __enter__(self):
        if sys.platform != "linux":
            raise ValueError("Real ADC capture runs on ARM Linux, never a Windows serial device")
        import fcntl
        self.ioctl = fcntl.ioctl
        self.boot_id = str(uuid.UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()))
        self.descriptor = os.open(self.device, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            if not stat.S_ISCHR(os.fstat(self.descriptor).st_mode):
                raise ValueError("The capture transport must be the reviewed character device")
            payload = bytearray(CAPS.size)
            self.ioctl(self.descriptor, CAPS_IOCTL, payload, True)
            self.capabilities = decode_caps(payload)
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None

    def __exit__(self, *arguments):
        self.close()

    def capture_once(self, mode="PASSIVE", gain=128, *, operator_ready=False):
        if self.descriptor is None:
            raise ValueError("Capture transport is not open")
        if mode not in MODES or type(gain) is not int or not 0 <= gain <= 224:
            raise ValueError("Choose TEST/PASSIVE/PULSED and an integer gain in 0..0xE0")
        if mode == "PULSED" and operator_ready is not True:
            raise ValueError("An explicit current safe-to-pulse operator confirmation is required")
        payload = bytearray(FRAME_BYTES)
        HEADER.pack_into(payload, 0, b"UTC2", 2, 0, 0, 40, 4096, gain, MODES[mode], 0)
        requested_ns = time.monotonic_ns()
        self.ioctl(self.descriptor, CAPTURE_IOCTL, payload, True)
        result = decode_frame(payload, mode=mode, gain=gain, requested_ns=requested_ns,
                              now_ns=time.monotonic_ns(), previous_sequence=self.previous_sequence)
        self.previous_sequence = result.sequence
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Guarded ARM Linux ADC capture; defaults to one passive frame, never automatic transmission")
    parser.add_argument("--device", type=Path, default=Path("/dev/ut_board"))
    parser.add_argument("--mode", choices=MODES, default="PASSIVE")
    parser.add_argument("--gain", type=lambda value: int(value, 0), default=128)
    parser.add_argument("--frames", type=int, default=1)
    parser.add_argument("--rate", type=float, default=10)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--hv-off-confirmed", action="store_true")
    parser.add_argument("--probe-coupled-and-safe-to-pulse", action="store_true")
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.frames <= 1000 or not 0 < arguments.rate <= 20 or not 0 <= arguments.gain <= 224:
        raise ValueError("Use 1..1000 frames, 0..0xE0 gain and a finite rate up to 20 Hz")
    if arguments.mode == "TEST" and not arguments.hv_off_confirmed:
        raise ValueError("DMA TEST requires current HV-OFF confirmation")
    if arguments.mode == "PULSED" and not arguments.probe_coupled_and_safe_to_pulse:
        raise ValueError("PULSED capture requires explicit current probe-coupled/safe-to-pulse confirmation")
    arguments.out.mkdir(parents=True, exist_ok=False)
    with LinuxCapture(arguments.device) as capture:
        for index in range(arguments.frames):
            started = time.monotonic()
            frame = capture.capture_once(arguments.mode, arguments.gain,
                                         operator_ready=arguments.probe_coupled_and_safe_to_pulse)
            path = arguments.out / ("frame_%04d.utc2" % index)
            with path.open("xb") as output:
                output.write(frame.raw)
            record = {**frame.record(), "boot_id": capture.boot_id, "raw_file": path.name}
            path.with_suffix(".json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(record), flush=True)
            if index + 1 < arguments.frames:
                time.sleep(max(0, 1 / arguments.rate - (time.monotonic() - started)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        raise SystemExit(2)
