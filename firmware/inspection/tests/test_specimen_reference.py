import tempfile
import unittest
from pathlib import Path

from utinspect.basic_vision import BasicVision
from utinspect.echo import EchoConfig, Waveform
from utinspect.mapping import MapSession
from utinspect.specimen_reference import hole_reference
from test_basic_vision import CORNERS, NOW, frame_bytes


class SpecimenReferenceTests(unittest.TestCase):
    def test_six_holes_belong_to_two_distinct_three_hole_blocks(self):
        depth = hole_reference("UT45-02-R3", "X20")
        diameter = hole_reference("UT45-03-R3", "X20")
        self.assertEqual([hole["nominal_depth_from_A_mm"] for hole in depth["holes"]], [8, 15, 22])
        self.assertEqual([hole["nominal_depth_from_A_mm"] for hole in diameter["holes"]], [15, 15, 13])
        self.assertEqual([hole["nominal_diameter_mm"] for hole in diameter["holes"]], [3, 4, 5])
        for reference in (depth, diameter):
            self.assertTrue(reference["reference_only"])
            self.assertFalse(reference["feeds_diagnosis"])
            self.assertEqual(len(reference["holes"]), 3)

    def test_face_a_h1_direction_is_explicit_and_mirrors_positions_not_depths(self):
        forward, reverse = [hole_reference("UT45-02-R3", end)["holes"] for end in ("X20", "X60")]
        self.assertEqual([hole["position_mm"] for hole in forward], [[20, 40], [40, 40], [60, 40]])
        self.assertEqual([hole["position_mm"] for hole in reverse], [[60, 40], [40, 40], [20, 40]])
        self.assertEqual([hole["nominal_depth_from_A_mm"] for hole in forward],
                         [hole["nominal_depth_from_A_mm"] for hole in reverse])

    def test_missing_identity_direction_and_wrong_geometry_are_rejected(self):
        for arguments in (("six-holes", "X20"), ("UT45-02-R3", ""), ("UT45-02-R3", "back"),
                          ("UT45-02-R3", "X20", 100, 80), ("UT45-02-R3", "X20", float("nan"), 80)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                hole_reference(*arguments)

    def test_reference_catalog_cannot_be_modified_by_a_caller(self):
        first = hole_reference("UT45-02-R3", "X20")
        first["holes"][0]["position_mm"][0] = 99
        self.assertEqual(hole_reference("UT45-02-R3", "X20")["holes"][0]["position_mm"], [20, 40])

    def test_surface_overlay_requires_fresh_calibrated_image_and_keeps_reference_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "frame.ppm"
            path.write_bytes(frame_bytes())
            vision = BasicVision(path, allow_host_fixture=True, clock=lambda: NOW)
            self.assertFalse(vision.reference_overlay("UT45-02-R3", "X20")["projected"])
            frozen = vision.freeze()["selection"]
            vision.calibrate({"calibration_id": frozen["calibration_id"], "corners": CORNERS, "fixed_confirmed": True})
            reference = vision.reference_overlay("UT45-02-R3", "X20")
            self.assertTrue(reference["projected"])
            self.assertEqual(reference["surface_calibration_id"], vision.surface_calibration_id)
            for hole, expected in zip(reference["holes"], ([50, 60], [80, 60], [110, 60])):
                for actual, target in zip(hole["surface_pixel"], expected):
                    self.assertAlmostEqual(actual, target)
            path.unlink()
            lost = vision.reference_overlay("UT45-02-R3", "X20")
            self.assertFalse(lost["projected"])
            self.assertNotIn("surface_pixel", lost["holes"][0])

    def test_identical_waveforms_still_report_suspects_outside_all_known_holes(self):
        with tempfile.TemporaryDirectory() as temporary:
            echo = EchoConfig(calibrated=True, calibration_id="OFFLINE_REFERENCE_ISOLATION", baseline_gate_us=(20, 22))
            session = MapSession(Path(temporary) / "map", echo=echo, mode="RECORDED")
            samples = [128] * 4096
            samples[110], samples[254] = 210, 190
            for index, position in enumerate(((20, 40), (5, 5), (75, 75), (40, 40), (60, 40))):
                hole_reference("UT45-02-R3" if index % 2 else "UT45-03-R3", "X20" if index % 2 else "X60")
                frame = Waveform(tuple(samples), 0, .04, "RECORDED", frame_id=f"REFERENCE_ISOLATION_{index}")
                point = session.add(frame, *position, position_confirmed=True)
                self.assertEqual(point["status"], "SUSPECT")
            self.assertEqual(len(session.points), 5)
            diagnoses = [{key: value for key, value in point["diagnosis"].items() if key != "frame_id"} for point in session.points]
            self.assertTrue(all(diagnosis == diagnoses[0] for diagnosis in diagnoses))
