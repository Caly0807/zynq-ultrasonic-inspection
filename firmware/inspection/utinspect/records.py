import json
from dataclasses import dataclass
from pathlib import Path


def atomic_json(path, payload, *, compact=False):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=None if compact else 2,
                                   separators=(",", ":") if compact else None, allow_nan=False), encoding="utf-8")
    temporary.replace(destination)


@dataclass(frozen=True)
class Observation:
    valid: bool
    reason: str
    position_mm: tuple[float, float, float] | None
    timestamp_ns: int
    source: str
    marker_ids: tuple[int, ...]
    reprojection_error_px: float | None = None
