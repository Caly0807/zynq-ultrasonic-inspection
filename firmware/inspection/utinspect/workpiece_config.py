import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .vision import atomic_json
from .workpiece import VisualSettings, WorkpieceSpec, order_corners


def read_profile(path):
    profile_path = Path(path)
    if profile_path.stat().st_size > 65536:
        raise ValueError("Visual profile exceeds 64 KiB")
    data = json.loads(profile_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1:
        raise ValueError("Unsupported visual profile schema")
    try:
        spec = WorkpieceSpec(**data["workpiece"])
        settings = VisualSettings(**data["vision"])
    except (KeyError, TypeError) as error:
        raise ValueError("Invalid workpiece or vision settings in profile") from error
    reference = data.get("face_reference")
    if reference is not None:
        if not isinstance(reference, dict):
            raise ValueError("Invalid saved face reference")
        shape, identity = reference.get("image_size"), reference.get("camera_identity")
        if not isinstance(shape, list) or len(shape) != 2 or any(type(value) is not int or value <= 0 for value in shape):
            raise ValueError("Invalid reference image size")
        if not isinstance(identity, str) or not 1 <= len(identity) <= 256:
            raise ValueError("Invalid reference camera identity")
        try:
            corners = np.asarray(reference.get("normalized_corners"), np.float32)
        except (TypeError, ValueError) as error:
            raise ValueError("Invalid normalized reference corners") from error
        if corners.shape != (4, 2) or not np.isfinite(corners).all() or np.any(corners < 0) or np.any(corners > 1):
            raise ValueError("Invalid normalized reference corners")
        order_corners(corners * np.array([shape[0] - 1, shape[1] - 1], np.float32))
    return spec, settings, reference


def write_profile(path, spec, settings, face, camera_identity):
    reference = None
    if face is not None:
        reference = {"image_size": [face.width, face.height], "camera_identity": camera_identity,
                     "normalized_corners": (face.corners / np.array([face.width - 1, face.height - 1])).tolist()}
    data = {"schema": 1, "workpiece": asdict(spec), "vision": asdict(settings), "face_reference": reference,
            "restore_policy": "EXPLICIT_CONFIRMATION_ONLY_CONTACT_CONFIRMATION_NEVER_RESTORED"}
    atomic_json(Path(path), data)
    return reference
