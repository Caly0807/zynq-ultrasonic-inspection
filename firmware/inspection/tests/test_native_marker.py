import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utinspect.basic_vision import BasicVision, MAX_AGE_NS, parse_frame, red_marker
from test_basic_vision import NOW, frame_bytes


def native_frame(fields="1 1 79500 59500 400"):
    return frame_bytes(magic="UTCAM2", boxes=[(70, 50, 90, 70)]).replace(b"\n160 120\n", (" " + fields + "\n160 120\n").encode(), 1)


class NativeMarkerTests(unittest.TestCase):
    def test_native_metadata_avoids_python_pixel_sweep(self):
        frame = parse_frame(native_frame(), NOW)
        with mock.patch("utinspect.basic_vision.deque", side_effect=AssertionError("Python flood fill must not run")):
            result = red_marker(frame)
        self.assertEqual(result, {"status": "FOUND", "candidate_count": 1, "pixel": [79.5, 59.5], "area_pixels": 400})

    def test_native_and_legacy_results_match_for_known_centroid(self):
        native = parse_frame(native_frame(), NOW)
        legacy = parse_frame(frame_bytes(magic="UTCAM1", boxes=[(70, 50, 90, 70)]), NOW)
        self.assertEqual(red_marker(native), red_marker(legacy))
        self.assertEqual(native.frame_id, legacy.frame_id)

    def test_no_marker_ambiguity_and_oversize_keep_position_unknown(self):
        for fields, expected in (("2 0 0 0 0", "NOT_FOUND"), ("3 2 79500 59500 400", "AMBIGUOUS"),
                                 ("4 1 79500 59500 4000", "TOO_LARGE")):
            with self.subTest(fields=fields):
                result = red_marker(parse_frame(native_frame(fields), NOW))
                self.assertEqual(result["status"], expected)
                self.assertIsNone(result["pixel"])

    def test_invalid_native_fields_do_not_become_positions(self):
        for fields in ("1 0 79500 59500 400", "1 1 160000 59500 400", "1 1 79500 120000 400",
                       "1 1 79500 59500 19000", "2 1 0 0 0", "3 1 0 0 10", "4 1 79500 59500 1",
                       "9 1 79500 59500 400", "1 1 nan 59500 400"):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                parse_frame(native_frame(fields), NOW)

    def test_metadata_version_and_arity_cannot_disagree(self):
        for data in (frame_bytes(magic="UTCAM2"), native_frame().replace(b"UTCAM2", b"UTCAM1", 1)):
            with self.assertRaises(ValueError):
                parse_frame(data, NOW)

    def test_processing_delay_is_included_in_reported_frame_freshness(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "frame.ppm"
            path.write_bytes(native_frame())
            clock = iter((NOW, NOW, NOW + MAX_AGE_NS + 1))
            vision = BasicVision(path, clock=lambda: next(clock))
            result = vision.state()
            self.assertFalse(result["preview_available"])
            self.assertIn("processing", result["error"])
