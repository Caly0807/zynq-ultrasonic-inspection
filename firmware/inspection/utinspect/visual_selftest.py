import json
from pathlib import Path

import cv2
import numpy as np

from .vision import atomic_json
from .workpiece import VisualSettings, WorkpieceSpec
from .workpiece_app import WorkpieceSession


def demo_workpiece_image(centers=((320, 240),)):
    image = np.full((480, 640, 3), 28, np.uint8)
    cv2.rectangle(image, (160, 80), (480, 400), (170, 175, 180), -1)
    cv2.rectangle(image, (160, 80), (480, 400), (230, 235, 240), 3)
    for center in centers:
        cv2.circle(image, center, 38, (110, 135, 160), -1, cv2.LINE_AA)
        cv2.circle(image, center, 38, (245, 245, 245), 4, cv2.LINE_AA)
        cv2.circle(image, center, 33, (35, 40, 45), 2, cv2.LINE_AA)
    cv2.putText(image, "DEMO TEST IMAGE / NOT CAMERA", (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (20, 180, 240), 1)
    return image


def run_visual_selftest(output):
    directory = Path(output).resolve()
    session = WorkpieceSession(demo_workpiece_image(), directory, WorkpieceSpec(dimensions_confirmed=True),
                               visual_settings=VisualSettings(probe_mode="round"),
                               profile_path=directory / "selftest_profile.json")
    session.image_source = "DEMO"
    steps = []
    try:
        session.action({"action": "lock_auto"})
        state = session.state()
        if state["probe"] is None or state["probe"]["method"] != "ROUND_BODY" or state["plane_position_mm"] is not None:
            raise ValueError("DEMO round-body detection or contact-coordinate guard failed")
        steps.extend(["workpiece_candidate_and_explicit_lock", "round_body_without_false_contact_coordinates"])
        body_record = session.action({"action": "manual_mark"})
        session.action({"action": "freeze"})
        session.action({"action": "probe", "pixel": [320, 240]})
        manual_record = session.action({"action": "manual_mark"})
        if not manual_record["frozen_frame"] or manual_record["kind"] != "MANUAL_TEST_NOT_ULTRASOUND":
            raise ValueError("DEMO frozen manual mark lost its provenance")
        steps.append("frozen_manual_point_and_test_export")
        for record in (body_record, manual_record):
            if record["image_source"] != "DEMO":
                raise ValueError("Synthetic images cannot become live camera evidence")
            for name in ("raw", "annotated", "top"):
                image = cv2.imdecode(np.fromfile(directory / record["files"][name], np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    raise ValueError("DEMO export could not be decoded")
            saved = json.loads((directory / record["files"]["json"]).read_text(encoding="utf-8"))
            if saved["camera_frame_id"] != record["camera_frame_id"]:
                raise ValueError("DEMO export frame association was lost")
        steps.append("raw_annotated_top_json_roundtrip")
        session.action({"action": "profile_save"})
        session.action({"action": "profile_load"})
        if session.state()["face"] is not None:
            raise ValueError("Loading a profile must not silently restore old geometry")
        session.action({"action": "restore_face", "confirmed": True})
        if session.state()["contact_on_plane_confirmed"]:
            raise ValueError("Restoring corners must not restore contact confirmation")
        steps.append("profile_reload_requires_explicit_geometry_confirmation")
        report = {"schema": 1, "result": "PASS", "source": "DEMO", "real_camera_accessed": False,
                  "hardware_control": False, "steps": steps, "records": [body_record, manual_record]}
        atomic_json(directory / "visual_selftest.json", report)
        return report
    finally:
        session.close()
