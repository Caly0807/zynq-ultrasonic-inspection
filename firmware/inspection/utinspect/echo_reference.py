import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class NormalReference:
    reference_id: str
    amplitude_envelope_codes: tuple[float, ...]
    sample_period_us: float
    trigger_index: int
    gain_code: int
    contrast_codes: float = 6.0
    estimated_time_zero_us: float | None = None
    thickness_mm: float = 30.0

    def __post_init__(self):
        if not isinstance(self.reference_id, str) or not 1 <= len(self.reference_id) <= 96:
            raise ValueError("Normal reference needs a bounded identity")
        if len(self.amplitude_envelope_codes) != 4096 or any(type(value) not in (int, float) or
                not math.isfinite(value) or not 0 <= value <= 255 for value in self.amplitude_envelope_codes):
            raise ValueError("Normal reference needs a complete finite amplitude envelope")
        if type(self.sample_period_us) not in (int, float) or not math.isfinite(self.sample_period_us) or not 0 < self.sample_period_us <= 10:
            raise ValueError("Invalid normal-reference sample period")
        if type(self.trigger_index) is not int or not 0 <= self.trigger_index < 4096:
            raise ValueError("Invalid normal-reference trigger index")
        if type(self.gain_code) is not int or not 0 <= self.gain_code <= 224:
            raise ValueError("Invalid normal-reference gain")
        for value in (self.contrast_codes, self.thickness_mm):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("Normal-reference contrast and thickness must be finite and positive")
        if self.estimated_time_zero_us is not None and (type(self.estimated_time_zero_us) not in (int, float) or
                not math.isfinite(self.estimated_time_zero_us) or self.estimated_time_zero_us < 0):
            raise ValueError("Invalid estimated acoustic zero")

    def matches(self, frame):
        return (len(frame.samples) == 4096 and frame.trigger_index == self.trigger_index and
                abs(frame.sample_period_us - self.sample_period_us) < 1e-9 and frame.gain == self.gain_code)

    def select_peak(self, frame, baseline, interval, threshold, noise):
        margin = max(self.contrast_codes, noise * 3)
        candidates = [(index, abs(value - baseline)) for index, value in enumerate(frame.samples)
                      if interval[0] <= (index - frame.trigger_index) * frame.sample_period_us <= interval[1]
                      and abs(value - baseline) >= threshold
                      and abs(value - baseline) >= self.amplitude_envelope_codes[index] + margin]
        if not candidates:
            return None
        index, amplitude = max(candidates, key=lambda candidate: candidate[1])
        return {"time_us": (index - frame.trigger_index) * frame.sample_period_us,
                "amplitude_codes": amplitude, "excess_over_reference_codes": amplitude - self.amplitude_envelope_codes[index]}

    def payload(self):
        return {"schema": "ut-normal-envelope/1", "calibration_approved": False, **asdict(self)}


def load_normal_reference(path):
    content = json.loads(Path(path).read_text(encoding="utf-8"))
    if content.get("schema") != "ut-normal-envelope/1" or content.get("calibration_approved") is not False:
        raise ValueError("Expected an unapproved normal-reference envelope, not a calibration approval")
    fields = {name: content[name] for name in NormalReference.__dataclass_fields__ if name in content}
    fields["amplitude_envelope_codes"] = tuple(fields["amplitude_envelope_codes"])
    return NormalReference(**fields)


def reference_identity(payload):
    encoded = json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "NORMAL-" + hashlib.sha256(encoded).hexdigest()[:24]
