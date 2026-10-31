import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utinspect import live_capture


class LiveCaptureTests(unittest.TestCase):
    def frame(self, source_mode="PULSED", **changes):
        values = dict(magic=b"UTC2", version=2, sequence=7,
                      flags={"PULSED": 0x204, "PASSIVE": 0xA04, "TEST": 0x304}[source_mode],
                      period=40, count=4096, gain=128, mode=live_capture.MODES[source_mode], timestamp=200)
        values.update(changes)
        samples = bytes(range(256)) * 16 if source_mode == "TEST" else bytes([200]) * 4096
        return live_capture.HEADER.pack(*values.values()) + samples

    def decode(self, mode="PULSED", data=None, **changes):
        parameters = dict(mode=mode, gain=128, requested_ns=100, now_ns=300, previous_sequence=6)
        parameters.update(changes)
        return live_capture.decode_frame(self.frame(mode) if data is None else data, **parameters)

    def test_abi_matches_exact_c_layout_and_ioctl_numbers(self):
        self.assertEqual(live_capture.HEADER.size, 40)
        self.assertEqual(live_capture.FRAME_BYTES, 4136)
        self.assertEqual(live_capture.CAPS_IOCTL, 0x80205520)
        self.assertEqual(live_capture.CAPTURE_IOCTL, 0xD0285521)

    def test_caps_require_guarded_hardware_driver_pair(self):
        fixture = [0x32564C55, 2, 0xE, 4096, 40, 224, 250000, 0]
        self.assertEqual(live_capture.decode_caps(struct.pack("<8I", *fixture))["modes"], ["TEST", "PASSIVE", "PULSED"])
        for index in range(8):
            changed = fixture.copy()
            changed[index] ^= 1
            with self.subTest(index=index), self.assertRaises(ValueError):
                live_capture.decode_caps(struct.pack("<8I", *changed))

    def test_real_pulsed_record_preserves_raw_and_never_claims_calibration(self):
        frame = self.decode()
        self.assertEqual(frame.raw, self.frame())
        self.assertEqual(frame.samples, bytes([200]) * 4096)
        record = frame.record()
        self.assertEqual(record["source"], "FPGA_ADC_PULSED")
        self.assertFalse(record["calibrated"])
        self.assertFalse(record["thickness_or_defect_verdict_authorized"])
        self.assertFalse(record["transmit_enabled_after_capture"])
        waveform = frame.waveform("ac13211e-481c-4b1e-a7dc-0bb3612565ae")
        self.assertEqual(waveform.source, "LIVE")
        self.assertEqual(waveform.sample_period_us, 0.04)
        self.assertEqual(waveform.acquired_monotonic_ns, 200)

    def test_passive_and_test_never_enter_ultrasonic_map(self):
        for mode in ("PASSIVE", "TEST"):
            frame = self.decode(mode)
            self.assertIn(mode if mode == "PASSIVE" else "NOT_ULTRASOUND", frame.record()["source"])
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                frame.waveform("ac13211e-481c-4b1e-a7dc-0bb3612565ae")

    def test_all_partial_frames_and_source_mismatches_are_rejected(self):
        changes = ({"magic": b"UTC1"}, {"version": 1}, {"period": 20}, {"count": 4095},
                   {"gain": 129}, {"mode": 2}, {"flags": 0x304}, {"flags": 0xA04}, {"flags": 0x200})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.decode(data=self.frame(**change))
        for payload in (self.frame()[:-1], self.frame() + b"x"):
            with self.assertRaises(ValueError):
                self.decode(data=payload)

    def test_busy_rejection_active_output_and_expired_lease_fail_closed(self):
        for flag in (1, 8, 0x40, 0x80, 0x1000, 0x2000):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                self.decode(data=self.frame(flags=0x204 | flag))

    def test_overrange_is_preserved_instead_of_hiding_a_clipped_echo(self):
        frame = self.decode(data=self.frame(flags=0x214))
        self.assertTrue(frame.record()["overrange"])
        self.assertTrue(frame.waveform("ac13211e-481c-4b1e-a7dc-0bb3612565ae").overrange)

    def test_fresh_same_host_timestamp_and_next_sequence_are_mandatory(self):
        for change in ({"timestamp": 99}, {"timestamp": 301}, {"sequence": 6}, {"sequence": 8}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.decode(data=self.frame(**change))
        with self.assertRaises(ValueError):
            self.decode(now_ns=2_000_000_201)
        wrapped = self.decode(data=self.frame(sequence=0), previous_sequence=0xFFFFFFFF)
        self.assertEqual(wrapped.sequence, 0)

    def test_test_ramp_integrity_is_not_enforced_on_real_adc(self):
        self.decode()
        payload = bytearray(self.frame("TEST"))
        payload[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "FPGA ramp"):
            self.decode("TEST", payload)

    def test_windows_never_opens_a_serial_or_board_device(self):
        with mock.patch.object(live_capture.sys, "platform", "win32"), mock.patch.object(live_capture.os, "open") as opened:
            with self.assertRaises(ValueError):
                with live_capture.LinuxCapture():
                    pass
            opened.assert_not_called()

    def test_pulsed_capture_needs_confirmation_before_any_ioctl(self):
        capture = live_capture.LinuxCapture()
        capture.descriptor = 123
        capture.ioctl = mock.Mock()
        with self.assertRaisesRegex(ValueError, "operator confirmation"):
            capture.capture_once("PULSED")
        capture.ioctl.assert_not_called()

    def test_cli_does_not_open_device_or_create_output_before_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "must-not-exist"
            for mode in ("PULSED", "TEST"):
                with self.subTest(mode=mode), mock.patch.object(live_capture, "LinuxCapture") as opened:
                    with self.assertRaises(ValueError):
                        live_capture.main(["--mode", mode, "--out", str(path)])
                    opened.assert_not_called()
                    self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
