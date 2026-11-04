import math


SPECIMENS = {
    "UT45-02-R3": ((3, 8), (3, 15), (3, 22)),
    "UT45-03-R3": ((3, 15), (4, 15), (5, 13)),
}


def hole_reference(specimen, h1_end, length_mm=80, width_mm=80):
    if not isinstance(specimen, str) or specimen not in SPECIMENS:
        raise ValueError("Select UT45-02-R3 or UT45-03-R3; each specimen contains three holes")
    if not isinstance(h1_end, str) or h1_end not in ("X20", "X60"):
        raise ValueError("Confirm whether H1 is at X=20 or X=60 on detection face A; do not infer back-view mirroring")
    if any(isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or
           not math.isclose(value, 80, abs_tol=1e-6) for value in (length_mm, width_mm)):
        raise ValueError("R3 references require the nominal 80 by 80 mm specimen")
    holes = []
    for index, (diameter, depth) in enumerate(SPECIMENS[specimen]):
        position_x = 20 + index * 20 if h1_end == "X20" else 60 - index * 20
        holes.append({"hole_id": f"H{index + 1}", "position_mm": [position_x, 40],
                      "nominal_diameter_mm": diameter, "nominal_depth_from_A_mm": depth,
                      "reference_only": True})
    return {"schema": "ut-hole-reference/1", "specimen_id": specimen, "h1_end": h1_end,
            "label": "已知孔位参照 / 非检测结果", "reference_only": True, "feeds_diagnosis": False,
            "detection_face": "A_UNDRILLED_SURFACE", "workpiece_mm": [80, 80, 30], "holes": holes,
            "source": "R3_DRAWING_AND_USER_REPORTED_PHI5_BACK_DRILL_17MM",
            "depth_definition": "FROM_FACE_A_TO_HOLE_BOTTOM; PHI5=30-17=13MM"}
