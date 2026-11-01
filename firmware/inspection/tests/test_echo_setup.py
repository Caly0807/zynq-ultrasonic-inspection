import struct
import tempfile
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from utinspect.echo import EchoConfig, Waveform, analyze
from utinspect.echo_setup import EchoSetup, STATUS
from utinspect.board_health import echo_setup_diagnostics
from utinspect.live_service import LiveAcquisition
from utinspect.mapping import MapSession


class EchoSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = 10_000_000_000
        self.active = True
        self.in_flight = False
        self.owner = "lcd-" + "1" * 32
        self.session = MapSession(self.root / "session", mode="LIVE")
        self.session.bind_local_display(self.root / "lcd")
        self.live = mock.Mock()
        self.live.state.side_effect = lambda: {"active": self.active, "capture_in_flight": self.in_flight}
        self.live.mapping_frame.side_effect = lambda: (self.owner, self.frame) if self.active else (None, None)
        self.live.stop.side_effect = lambda: setattr(self, "active", False)
        self.setup = EchoSetup(self.root / "lcd", self.session, self.live, clock=lambda: self.now)
        self.index = 0
        self.next_frame()

    def next_frame(self, *, flaw=False, quiet_noise=False, gain=128, wall=True):
        self.now += 200_000_000
        self.index += 1
        samples = [128] * 4096
        if wall:
            samples[275] = 195
        if flaw:
            samples[120] = 200
        if quiet_noise:
            for index in range(2500, 2751):
                samples[index] = 140 if index % 2 else 116
        self.frame = Waveform(tuple(samples), 0, .04, "LIVE", gain,
                              frame_id=f"TEST_FIXTURE_NOT_BOARD_{self.index}", acquired_monotonic_ns=self.now)

    def collect(self, **kwargs):
        self.setup.command("echo-reference", {"reference_confirmed": True, "normal_incidence_confirmed": True})
        for index in range(8):
            self.next_frame(**kwargs)
            self.setup.tick()

    def test_boot_and_opening_setup_do_not_change_configuration_or_start_capture(self):
        state = self.setup.tick()
        self.assertEqual(state["phase"], "IDLE")
        self.assertFalse(self.session.echo.calibrated)
        self.assertIsNone(self.session.echo.baseline_gate_us)
        self.live.start.assert_not_called()
        self.live.stop.assert_not_called()

    def test_preview_configuration_stops_then_applies_without_approval_or_restart(self):
        self.setup.command("echo-preview", {})
        self.assertFalse(self.active)
        self.in_flight = True
        self.assertEqual(self.setup.tick()["phase"], "APPLYING")
        self.in_flight = False
        self.assertEqual(self.setup.tick()["reason"], "PREVIEW_APPLIED")
        self.assertEqual(self.session.echo.baseline_gate_us, (100., 110.))
        self.assertFalse(self.session.echo.calibrated)
        self.live.start.assert_not_called()

    def test_eight_fresh_frames_require_separate_explicit_approval(self):
        self.collect()
        self.assertEqual(self.setup.phase, "READY")
        self.assertFalse(self.session.echo.calibrated)
        self.assertAlmostEqual(self.setup.zero, 11 - 60000 / 5900)
        self.setup.command("echo-approve", {"review_confirmed": True})
        self.assertFalse(self.active)
        self.setup.tick()
        self.assertTrue(self.session.echo.calibrated)
        self.assertEqual(self.session.echo.reference_gain_code, 128)
        self.assertTrue(self.session.echo.calibration_id.startswith("LCD-REF-"))
        self.live.start.assert_not_called()

    def test_reference_transient_in_flaw_gate_is_rejected_not_masked(self):
        self.collect(flaw=True)
        self.assertEqual(self.setup.reason, "REFERENCE_FLAW_GATE_CROSSED")
        with self.assertRaises(ValueError):
            self.setup.command("echo-approve", {"review_confirmed": True})
        self.assertFalse(self.session.echo.calibrated)
        self.live.configure_echo.assert_not_called()

    def test_missing_backwall_cannot_calibrate(self):
        self.collect(wall=False)
        self.assertEqual(self.setup.reason, "BACKWALL_MISSING")
        self.assertIsNone(self.setup.proposal)

    def test_noisy_quiet_gate_cannot_be_approved(self):
        self.collect(quiet_noise=True)
        self.assertIn(self.setup.reason, ("QUIET_GATE_NOISY", "BACKWALL_MISSING"))
        self.assertIsNone(self.setup.proposal)

    def test_pc_owned_reference_and_truthy_confirmations_are_rejected(self):
        self.owner = "pc-" + "1" * 32
        with self.assertRaises(ValueError):
            self.setup.command("echo-reference", {"reference_confirmed": True, "normal_incidence_confirmed": True})
        for value in (1, "true", [True]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.setup.command("echo-reference", {"reference_confirmed": value, "normal_incidence_confirmed": True})

    def test_stale_or_duplicate_frames_cannot_fill_the_reference(self):
        self.setup.command("echo-reference", {"reference_confirmed": True, "normal_incidence_confirmed": True})
        self.next_frame()
        for index in range(8):
            self.setup.tick()
        self.assertEqual(len(self.setup.frames), 1)
        self.now += 600_000_000
        self.setup.tick()
        self.assertEqual(len(self.setup.frames), 1)

    def test_gain_change_cancels_reference(self):
        self.setup.command("echo-reference", {"reference_confirmed": True, "normal_incidence_confirmed": True})
        self.next_frame(gain=160)
        self.setup.tick()
        self.assertEqual(self.setup.reason, "GAIN_CHANGED")

    def test_lease_loss_cancels_collection_without_restart(self):
        self.setup.command("echo-reference", {"reference_confirmed": True, "normal_incidence_confirmed": True})
        self.active = False
        self.setup.tick()
        self.assertEqual(self.setup.reason, "NEED_LOCAL_LIVE")
        self.live.start.assert_not_called()

    def test_expired_reference_cannot_be_approved(self):
        self.collect()
        self.now += 60_000_000_001
        with self.assertRaises(ValueError):
            self.setup.command("echo-approve", {"review_confirmed": True})
        self.assertFalse(self.session.echo.calibrated)

    def test_editing_the_draft_invalidates_its_previous_reference(self):
        self.collect()
        self.setup.command("echo-set", {"parameter": 6, "value": 40})
        self.assertIsNone(self.setup.proposal)
        self.assertEqual(self.setup.phase, "IDLE")
        with self.assertRaises(ValueError):
            self.setup.command("echo-approve", {"review_confirmed": True})

    def test_invalid_draft_or_quiet_window_is_not_applied(self):
        self.setup.command("echo-set", {"parameter": 1, "value": 10})
        with self.assertRaises(ValueError):
            self.setup.command("echo-preview", {})
        self.assertEqual((self.setup.phase, self.setup.reason), ("REJECTED", "INVALID_CONFIGURATION"))
        self.live.stop.assert_not_called()
        for value in (float("nan"), float("inf"), True, 10000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.setup.command("echo-set", {"parameter": 0, "value": value})

    def test_nonempty_map_cannot_mix_calibrations(self):
        with mock.patch.object(self.session, "snapshot", return_value={"point_count": 1}), self.assertRaises(ValueError):
            self.setup.command("echo-preview", {})
        self.live.configure_echo.assert_not_called()

    def test_status_layout_is_bounded_and_current(self):
        self.setup.tick()
        data = (self.root / "lcd/echo-status.bin").read_bytes()
        self.assertEqual(len(data), 100)
        values = STATUS.unpack(data)
        self.assertEqual(values[:3], (b"UTE1", 1, self.now))
        self.assertEqual(struct.unpack_from("<f", data, 36 + 4 * 4)[0], 100.)

    def test_approved_reference_gain_change_invalidates_diagnosis(self):
        config = EchoConfig(calibrated=True, calibration_id="TEST_ONLY", baseline_gate_us=(100., 110.), reference_gain_code=128)
        result = analyze(replace(self.frame, gain=160), config)
        self.assertEqual((result["status"], result["reason"]), ("INVALID", "calibration_gain_mismatch"))
        self.assertIsNone(result["depth_mm"])

    def test_diagnostics_are_fresh_whitelisted_and_not_physical_acceptance(self):
        self.setup.tick()
        path = self.root / "lcd/echo-status.json"
        with mock.patch("utinspect.board_health.time.monotonic_ns", return_value=self.now):
            status = echo_setup_diagnostics(path)
            self.assertTrue(status["available"])
            self.assertFalse(status["active_calibrated"])
            self.assertFalse(status["real_flaw_detection_verified"])
            self.assertNotIn("draft_values", status)
            original = json.loads(path.read_text())
            for key, value in (("updated_monotonic_ns", self.now + 1), ("updated_monotonic_ns", self.now - 3_000_000_001),
                               ("collected", True), ("gain_code", 225), ("reference_noise_codes", float("nan")),
                               ("real_flaw_detection_verified", True), ("phase", "PASS")):
                with self.subTest(key=key, value=value):
                    path.write_text(json.dumps({**original, key: value}))
                    self.assertFalse(echo_setup_diagnostics(path)["available"])

    def test_live_reconfiguration_requires_settled_stopped_state(self):
        live = LiveAcquisition(self.root / "transport", clock=lambda: self.now)
        live.available, live.owner, live.lease_deadline = True, self.owner, self.now + 1_000_000_000
        with self.assertRaises(ValueError):
            live.configure_echo(EchoConfig())
        live.owner, live.in_flight = None, True
        with self.assertRaises(ValueError):
            live.configure_echo(EchoConfig())
        live.in_flight = False
        config = EchoConfig(baseline_gate_us=(100., 110.))
        live.configure_echo(config)
        self.assertIs(live.echo, config)
        self.assertFalse(live.state()["active"])


if __name__ == "__main__":
    unittest.main()
