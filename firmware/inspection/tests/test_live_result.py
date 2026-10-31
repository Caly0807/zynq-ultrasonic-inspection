import struct
import unittest

from utinspect.echo import EchoConfig
from utinspect.live_capture import HEADER, RawCapture
from utinspect.live_result import RESULT, encode_result


class LiveResultTests(unittest.TestCase):
    def frame(self, mode="PULSED"):
        data = HEADER.pack(b"UTC2", 2, 17, 0x204, 40, 4096, 128, 3, 1000000000) + bytes([128]) * 4096
        return RawCapture(data, 17, 0x204, 128, mode, 1000000000)

    def diagnosis(self, **changes):
        return {"source": "LIVE", "status": "INVALID", "calibrated": False, "depth_mm": None,
                "reason": "insufficient_baseline_select_a_verified_quiet_gate", **changes}

    def test_exact_wire_layout_and_unverified_baseline_remain_invalid(self):
        result = encode_result(self.frame(), self.diagnosis(), EchoConfig())
        self.assertEqual(len(result), 72)
        fields = RESULT.unpack(result)
        self.assertEqual(fields[:7], (b"UTD1", 1, 17, 0, 0, 1, 1000000000))
        self.assertEqual(fields[7], 128)
        self.assertEqual(fields[-1], 5900)

    def test_calibrated_depth_and_backwall_use_the_board_analysis_result(self):
        diagnosis = self.diagnosis(status="SUSPECT", calibrated=True, depth_mm=12,
                                  baseline=127.5, noise_codes=1.5, flaw_gate_crossed=True,
                                  backwall_visible=True, flaw={"amplitude_codes": 35, "time_us": 5},
                                  backwall={"amplitude_codes": 60, "time_us": 10.2})
        fields = RESULT.unpack(encode_result(self.frame(), diagnosis, EchoConfig()))
        self.assertEqual(fields[3:5], (2, 31))
        self.assertAlmostEqual(fields[9], 12)
        self.assertAlmostEqual(fields[14], 30.09, places=4)

    def test_no_test_passive_demo_or_uncalibrated_final_results(self):
        for mode in ("TEST", "PASSIVE"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                encode_result(self.frame(mode), self.diagnosis(), EchoConfig())
        for changes in ({"source": "DEMO"}, {"status": "NO_INDICATION"},
                        {"depth_mm": 10}, {"baseline": float("nan")}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                encode_result(self.frame(), self.diagnosis(**changes), EchoConfig())
