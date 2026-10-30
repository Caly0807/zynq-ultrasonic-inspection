import json
import math
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from utinspect.echo import EchoConfig, load_csv
from utinspect.mapping import MapSession, MapSpec, embedded_csv, render_dashboard, synthetic_map_waveform


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.spec = MapSpec(normal_incidence_confirmed=True)
        self.echo = replace(EchoConfig(), calibrated=True, calibration_id="DEMO_ONLY_TEST")

    def tearDown(self):
        self.temporary.cleanup()

    def session(self, spec=None, echo=None, mode="DEMO"):
        return MapSession(self.root / ("session_" + str(len(list(self.root.iterdir())))), spec or self.spec, echo or self.echo, mode)

    def frame(self, position_x=27.5, position_y=32.5):
        return synthetic_map_waveform(position_x, position_y, self.spec)

    def test_spec_rejects_boolean_nonfinite_and_excessive_grid(self):
        for values in ({"length_mm": True}, {"grid_mm": float("nan")}, {"grid_mm": .01}, {"thickness_mm": -1}, {"normal_incidence_confirmed": 1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                MapSpec(**values)
        self.assertEqual(self.spec.cell(80, 80), (15, 15))
        for position in ((-1, 0), (0, 81), (math.inf, 0), (True, 0)):
            with self.assertRaises(ValueError):
                self.spec.cell(*position)

    def test_empty_map_is_unknown_not_clear(self):
        state = self.session().snapshot()
        self.assertEqual(state["sampled_cells"], 0)
        self.assertEqual(state["grid_coverage_percent"], 0)
        self.assertTrue(all(cell["status"] == "UNSCANNED" for cell in state["cells"]))

    def test_point_preserves_waveform_depth_and_unknown_hole_dimensions(self):
        session = self.session()
        frame = self.frame()
        point = session.add(frame, 27.5, 32.5, True)
        self.assertEqual(point["status"], "SUSPECT")
        self.assertAlmostEqual(point["reflector_depth_mm"], 12, delta=.3)
        self.assertAlmostEqual(point["distance_from_bottom_mm"] + point["reflector_depth_mm"], 30)
        self.assertIsNone(point["hole_height_mm"])
        self.assertIsNone(point["hole_diameter_mm"])
        self.assertEqual(load_csv(session.root / point["waveform"]).samples, frame.samples)
        self.assertEqual(json.loads((session.root / "session.json").read_text())["point_count"], 1)
        self.assertEqual(session.snapshot()["sampled_cells"], 1)

    def test_explicit_operator_confirmation_is_required(self):
        session = self.session()
        for confirmation in (False, 1, "yes"):
            with self.assertRaises(ValueError):
                session.add(self.frame(), 20, 20, confirmation)
        self.assertFalse(session.points)

    def test_duplicate_frame_cannot_fill_multiple_cells(self):
        session = self.session()
        frame = self.frame()
        session.add(frame, 20, 20, True)
        with self.assertRaises(ValueError):
            session.add(frame, 40, 40, True)

    def test_sources_are_never_mixed(self):
        session = self.session()
        for source in ("RECORDED", "LIVE"):
            with self.assertRaises(ValueError):
                session.add(replace(self.frame(), source=source), 20, 20, True)
        with self.assertRaises(ValueError):
            self.session(mode="RECORDED")

    def test_unconfirmed_normal_incidence_has_no_vertical_depth(self):
        point = self.session(spec=MapSpec()).add(self.frame(), 27.5, 32.5, True)
        self.assertEqual(point["status"], "SUSPECT")
        self.assertIsNone(point["reflector_depth_mm"])
        self.assertEqual(point["depth_reason"], "NORMAL_INCIDENCE_NOT_CONFIRMED")

    def test_uncalibrated_waveform_never_gets_depth(self):
        point = self.session(echo=EchoConfig()).add(self.frame(), 27.5, 32.5, True)
        self.assertEqual(point["status"], "UNCALIBRATED")
        self.assertIsNone(point["reflector_depth_mm"])

    def test_depth_outside_thickness_is_not_rendered_inside_block(self):
        point = self.session(spec=replace(self.spec, thickness_mm=5)).add(self.frame(), 27.5, 32.5, True)
        self.assertIsNone(point["reflector_depth_mm"])
        self.assertEqual(point["depth_reason"], "OUTSIDE_WORKPIECE_THICKNESS")

    def test_repeated_cell_does_not_erase_suspect_or_inflate_coverage(self):
        session = self.session()
        session.add(self.frame(), 27.5, 32.5, True)
        session.add(self.frame(2.5, 2.5), 27.5, 32.5, True)
        state = session.snapshot()
        self.assertEqual(state["sampled_cells"], 1)
        self.assertEqual(state["suspect_cells"], 1)
        self.assertEqual(state["cells"][6 * 16 + 5]["count"], 2)

    def test_missing_backwall_is_invalid_not_no_indication(self):
        point = self.session().add(self.frame(2.5, 77.5), 2.5, 77.5, True)
        self.assertEqual(point["status"], "INVALID")
        self.assertIsNone(point["reflector_depth_mm"])

    def test_tampered_waveform_is_rejected(self):
        session = self.session()
        point = session.add(self.frame(), 27.5, 32.5, True)
        (session.root / point["waveform"]).write_text("changed")
        for method in (session.waveform, session.waveform_csv):
            with self.assertRaises(ValueError):
                method(point["point_id"])

    def test_csv_download_preserves_gain_and_overrange(self):
        session = self.session()
        point = session.add(replace(self.frame(), gain=192, overrange=True), 20, 20, True)
        self.assertEqual(session.waveform_csv(point["point_id"]), (session.root / point["waveform"]).read_bytes())
        frame = load_csv(session.root / point["waveform"])
        self.assertEqual(frame.gain, 192)
        self.assertTrue(frame.overrange)

    def test_live_needs_fresh_timestamp(self):
        session = self.session(mode="LIVE", echo=EchoConfig())
        for timestamp in (None, time.monotonic_ns() - 3_000_000_000, time.monotonic_ns() + 10_000_000_000):
            with self.assertRaises(ValueError):
                session.add(replace(self.frame(), source="LIVE", acquired_monotonic_ns=timestamp), 20, 20, True)
        point = session.add(replace(self.frame(), source="LIVE", acquired_monotonic_ns=time.monotonic_ns()), 20, 20, True)
        self.assertEqual(point["source"], "LIVE")
        self.assertIsNone(point["reflector_depth_mm"])

    def test_small_screen_export_has_all_cells_and_provenance(self):
        session = self.session()
        session.add(self.frame(), 27.5, 32.5, True)
        text = embedded_csv(session.snapshot())
        self.assertTrue(text.startswith("UTMAP,1,16,16,"))
        self.assertIn(",DEMO,MANUAL_UNVERIFIED\n", text)
        self.assertEqual(len(text.splitlines()), 257)
        self.assertIn("CELL,5,6,SUSPECT,1,", text)

    def test_offline_report_is_self_contained_and_script_safe(self):
        session = self.session()
        state = session.snapshot()
        state["echo"]["calibration_id"] = "</script><script>alert(1)</script>"
        html = render_dashboard(state)
        self.assertNotIn(state["echo"]["calibration_id"], html)
        self.assertIn("\\u003c/script\\u003e", html)
        self.assertNotIn('<script src="http', html)
        self.assertIn("INDICATION_POINTS_ONLY_NOT_HOLE_SHAPE_OR_CT", html)

    def test_failed_canonical_commit_does_not_append_point(self):
        session = self.session()
        with patch("utinspect.mapping.atomic_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                session.add(self.frame(), 20, 20, True)
        self.assertFalse(session.points)
        self.assertEqual(json.loads((session.root / "session.json").read_text())["point_count"], 0)


    def test_submission_ids_are_persisted_and_cannot_create_a_second_point(self):
        session = self.session()
        point = session.add(self.frame(), 27.5, 32.5, True, submission_id="direct-add-once")
        self.assertEqual(point["submission_id"], "direct-add-once")
        with self.assertRaisesRegex(ValueError, "same submission"):
            session.add(self.frame(), 2.5, 2.5, True, submission_id="direct-add-once")
        self.assertEqual(len(session.points), 1)

    def test_invalid_submission_ids_do_not_write_a_point(self):
        session = self.session()
        for value in ("", "../point", "x" * 97, False, 7, "中文"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                session.add(self.frame(), 27.5, 32.5, True, submission_id=value)
        self.assertFalse(session.points)
        self.assertFalse(list((session.root / "waveforms").iterdir()))


if __name__ == "__main__":
    unittest.main()
