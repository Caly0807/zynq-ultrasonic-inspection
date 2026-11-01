import math
import struct


RESULT = struct.Struct("<4sIIIIIQ10f")
STATUSES = {"INVALID": 0, "UNCALIBRATED": 1, "SUSPECT": 2, "NO_INDICATION": 3}
REASONS = {"insufficient_baseline_select_a_verified_quiet_gate": 1,
           "baseline_gate_outside_acquisition_window": 2, "gate_outside_acquisition_window": 3,
           "adc_overrange_or_clipping": 4, "flaw_gate_threshold_crossed": 5,
           "no_flaw_gate_indication": 6, "backwall_missing_check_coupling": 7, "calibration_gain_mismatch": 8,
           "baseline_gate_not_quiet": 9, "normal_reference_acquisition_mismatch": 10,
           "normal_reference_overlap": 11}


def encode_result(frame, diagnosis, config):
    if frame.mode != "PULSED" or diagnosis.get("source") != "LIVE":
        raise ValueError("Only real pulsed ADC diagnosis can be published to the live LCD bridge")
    status = STATUSES[diagnosis["status"]]
    calibrated = diagnosis.get("calibrated") is True
    if status in (2, 3) and not calibrated:
        raise ValueError("A final live diagnosis requires approved calibration")
    baseline_valid = "baseline" in diagnosis and "noise_codes" in diagnosis
    depth = diagnosis.get("depth_mm")
    if depth is not None and (not calibrated or status != 2 or not math.isfinite(depth) or depth <= 0):
        raise ValueError("A depth requires a finite calibrated suspect indication")
    estimate = diagnosis.get("estimated_depth_mm")
    if estimate is not None and (calibrated or status != 1 or not baseline_valid or
            diagnosis.get("flaw_gate_crossed") is not True or not math.isfinite(estimate) or estimate <= 0):
        raise ValueError("A reference-only depth needs an uncalibrated fresh reflector indication")
    flaw, backwall = diagnosis.get("flaw", {}), diagnosis.get("backwall", {})
    backwall_visible = diagnosis.get("backwall_visible") is True
    reference = max(0, (backwall.get("time_us", 0) - config.time_zero_us) * config.sound_speed_mps / 2000) if calibrated and backwall_visible else 0
    flags = (int(calibrated) | int(baseline_valid) << 1 | int(depth is not None) << 2 |
             int(diagnosis.get("flaw_gate_crossed") is True) << 3 | int(backwall_visible) << 4 |
             int(diagnosis.get("reason") == "adc_overrange_or_clipping") << 5 | int(estimate is not None) << 6)
    zero = diagnosis.get("estimated_time_zero_us", config.time_zero_us) if estimate is not None else config.time_zero_us
    values = (diagnosis.get("baseline", 128), diagnosis.get("noise_codes", 0), depth or estimate or 0,
              flaw.get("amplitude_codes", 0), flaw.get("time_us", 0), backwall.get("amplitude_codes", 0),
              backwall.get("time_us", 0), reference, zero, config.sound_speed_mps)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Non-finite diagnosis cannot be displayed")
    return RESULT.pack(b"UTD1", 1, frame.sequence, status, flags, REASONS.get(diagnosis.get("reason"), 0),
                       frame.monotonic_ns, *values)
