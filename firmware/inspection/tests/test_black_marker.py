import tempfile
import unittest
from pathlib import Path

from utinspect.basic_vision import BasicVision, parse_frame, red_marker
from test_basic_vision import NOW, frame_bytes


def dot_frame(*, dots=((82, 56, 86, 60),), color=(180, 45, 5)):
    payload = bytearray(frame_bytes(boxes=[(60, 40, 100, 80)]).replace(bytes((240, 32, 16)), bytes(color)))
    offset = len(payload) - 160 * 120 * 3
    for left, top, right, bottom in dots:
        for row in range(top, bottom):
            for column in range(left, right):
                start = offset + (row * 160 + column) * 3
                payload[start:start + 3] = bytes((20, 20, 20))
    return parse_frame(bytes(payload), NOW, True)


class BlackMarkerTests(unittest.TestCase):
    def test_dot_center_is_not_the_red_region_centroid(self):
        marker = red_marker(dot_frame())
        self.assertEqual(marker['status'], 'FOUND')
        self.assertEqual(marker['point_kind'], 'CENTER_BLACK_DOT')
        self.assertEqual(marker['pixel'], [83.5, 57.5])
        self.assertEqual(marker['area_pixels'], 16)

    def test_red_or_orange_tape_can_surround_the_dot(self):
        for color in ((240, 32, 16), (180, 45, 5), (200, 60, 20)):
            with self.subTest(color=color):
                self.assertEqual(red_marker(dot_frame(color=color))['point_kind'], 'CENTER_BLACK_DOT')

    def test_multiple_dots_are_not_silently_reduced_to_one(self):
        marker = red_marker(dot_frame(dots=((82, 56, 86, 60), (70, 62, 74, 66))))
        self.assertEqual(marker['status'], 'AMBIGUOUS')
        self.assertEqual(marker['candidate_count'], 2)
        self.assertIsNone(marker['pixel'])

    def test_black_spot_outside_tape_is_not_the_probe_center(self):
        marker = red_marker(dot_frame(dots=((20, 20, 24, 24),)))
        self.assertNotEqual(marker.get('point_kind'), 'CENTER_BLACK_DOT')

    def test_native_v3_dot_metadata_has_an_explicit_point_kind(self):
        payload = frame_bytes(magic='UTCAM3').replace(b'\n160 120\n', b' 5 1 83500 57500 16\n160 120\n', 1)
        marker = red_marker(parse_frame(payload, NOW))
        self.assertEqual(marker['point_kind'], 'CENTER_BLACK_DOT')
        self.assertEqual(marker['pixel'], [83.5, 57.5])

    def test_v2_cannot_silently_claim_v3_dot_semantics(self):
        payload = frame_bytes(magic='UTCAM2').replace(b'\n160 120\n', b' 5 1 83500 57500 16\n160 120\n', 1)
        with self.assertRaises(ValueError):
            parse_frame(payload, NOW)

    def test_bad_native_dot_area_or_count_is_rejected(self):
        for fields in (b'5 1 83500 57500 1', b'5 2 83500 57500 16', b'5 1 83500 57500 1000'):
            payload = frame_bytes(magic='UTCAM3').replace(b'\n160 120\n', b' ' + fields + b'\n160 120\n', 1)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                parse_frame(payload, NOW)

    def test_board_can_require_a_dot_instead_of_a_plain_red_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'frame.ppm'
            path.write_bytes(frame_bytes(magic='UTCAM2').replace(b'\n160 120\n', b' 1 1 79500 59500 400\n160 120\n', 1))
            state = BasicVision(path, clock=lambda: NOW, require_center_dot=True).state()
            self.assertEqual(state['marker']['status'], 'CENTER_DOT_MISSING')
            self.assertIsNone(state['position_mm'])


if __name__ == '__main__':
    unittest.main()
