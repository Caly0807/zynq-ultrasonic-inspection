import argparse
import hashlib
import json
import os
import struct
import sys
from pathlib import Path


STATUS = struct.Struct("<8I")
HEADER = struct.Struct("<4sIIIIIQ")
STATUS_IOCTL = 0x80205510
FRAME_BYTES = HEADER.size + 4096


def decode_status(data):
    if len(data) != STATUS.size:
        raise ValueError("Truncated Linux board status")
    magic, version, width, height, stride, pixel_format, flags, sequence = STATUS.unpack(data)
    if (magic, version, width, height, stride, pixel_format) != (0x314C5455, 1, 800, 480, 2400, 1):
        raise ValueError("Unreviewed Linux board ABI or display geometry")
    return {"schema": "ut-linux-transport/1", "capture_status": flags, "sequence": sequence,
            "display_size": [width, height], "pixel_format": "GRB888", "live_capture_authorized": False,
            "transmit_enabled": bool(flags & 0xC0), "motion_enabled": False,
            "board_boot_verified_by_this_status": False}


def decode_test_frame(data):
    if len(data) != FRAME_BYTES:
        raise ValueError("Truncated or oversized DMA test frame")
    magic, version, sequence, flags, period_ns, sample_count, monotonic_ns = HEADER.unpack_from(data)
    if magic != b"UTC1" or version != 1 or period_ns != 40 or sample_count != 4096 or monotonic_ns == 0:
        raise ValueError("Unexpected DMA test frame ABI or sample timing")
    if flags & 0xD9 or flags & 0x104 != 0x104:
        raise ValueError("DMA self-test is busy, rejected, unsafe, or not identified as TEST data")
    samples = data[HEADER.size:]
    if samples != bytes(range(256)) * 16:
        raise ValueError("DMA self-test payload does not match the FPGA ramp")
    return {"schema": "ut-dma-selftest/1", "source": "FPGA_DMA_TEST_NOT_ULTRASOUND", "sequence": sequence,
            "sample_count": sample_count, "sample_period_ns": period_ns, "monotonic_ns": monotonic_ns,
            "sample_sha256": hashlib.sha256(samples).hexdigest(), "measurement_authorized": False,
            "transmit_enabled": False, "motion_enabled": False}


def inspect_transport(device=Path("/dev/ut_board"), selftest=False):
    if sys.platform != "linux":
        raise ValueError("The board transport is Linux only; Windows serial devices are never opened")
    import fcntl
    descriptor = os.open(device, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        status = bytearray(STATUS.size)
        fcntl.ioctl(descriptor, STATUS_IOCTL, status, True)
        result = decode_status(status)
        if result["transmit_enabled"]:
            raise ValueError("Unexpected active transmit state; stop and inspect hardware")
        if selftest:
            result["selftest"] = decode_test_frame(os.read(descriptor, FRAME_BYTES))
        return result
    finally:
        os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read Linux board status or explicitly run DMA TEST; never ultrasound/LIVE")
    parser.add_argument("--device", type=Path, default=Path("/dev/ut_board"))
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--hv-off-confirmed", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.selftest and not arguments.hv_off_confirmed:
        raise ValueError("Confirm the current HV-off condition before an explicit DMA self-test")
    print(json.dumps(inspect_transport(arguments.device, arguments.selftest), indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
