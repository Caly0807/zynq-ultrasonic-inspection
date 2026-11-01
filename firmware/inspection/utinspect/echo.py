import csv
import math
import statistics
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Waveform:
    samples: tuple[int, ...]
    trigger_index: int
    sample_period_us: float
    source: str
    gain: int = 128
    overrange: bool = False
    frame_id: str = ""
    acquired_monotonic_ns: int | None = None

    def __post_init__(self):
        if not 64 <= len(self.samples) <= 4096:
            raise ValueError("Expected 64..4096 samples")
        if any(type(value) is not int or not 0 <= value <= 255 for value in self.samples):
            raise ValueError("ADC samples must be integer codes in 0..255")
        if not 0 <= self.trigger_index < len(self.samples):
            raise ValueError("Invalid trigger index")
        if not math.isfinite(self.sample_period_us) or not 0 < self.sample_period_us <= 10:
            raise ValueError("Invalid sample period in microseconds")
        if self.source not in ("DEMO", "RECORDED", "LIVE"):
            raise ValueError("Source must be DEMO, RECORDED or LIVE")
        if type(self.gain) is not int or not 0 <= self.gain <= 224:
            raise ValueError("Gain must be an integer in 0..0xE0")
        if type(self.overrange) is not bool:
            raise ValueError("overrange must be a boolean")
        if self.acquired_monotonic_ns is not None and (type(self.acquired_monotonic_ns) is not int or self.acquired_monotonic_ns < 0):
            raise ValueError("Invalid acquisition timestamp")


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class EchoConfig:
    flaw_gate_us: tuple[float, float] = (2.5, 8.0)
    backwall_gate_us: tuple[float, float] = (8.5, 12.0)
    flaw_threshold_codes: float = 35.0
    backwall_threshold_codes: float = 12.0
    minimum_snr: float = 6.0
    sound_speed_mps: float = 5900.0
    time_zero_us: float = 0.0
    calibrated: bool = False
    calibration_id: str = ""
    baseline_gate_us: tuple[float, float] | None = None
    reference_gain_code: int | None = None

    def __post_init__(self):
        for gate in (self.flaw_gate_us, self.backwall_gate_us):
            if not isinstance(gate, (tuple, list)) or len(gate) != 2 or not all(finite_number(value) for value in gate):
                raise ValueError("Gate must contain two finite times")
            if not 0 <= gate[0] < gate[1]:
                raise ValueError("Invalid gate interval")
        if self.flaw_gate_us[1] >= self.backwall_gate_us[0]:
            raise ValueError("Flaw and backwall gates must not overlap")
        for value in (self.flaw_threshold_codes, self.backwall_threshold_codes, self.minimum_snr):
            if not finite_number(value) or value <= 0:
                raise ValueError("Thresholds and SNR must be finite and positive")
        if not finite_number(self.sound_speed_mps) or self.sound_speed_mps <= 0:
            raise ValueError("Invalid sound speed")
        if not finite_number(self.time_zero_us) or self.time_zero_us < 0:
            raise ValueError("Invalid time zero")
        if type(self.calibrated) is not bool:
            raise ValueError("calibrated must be a boolean")
        if self.reference_gain_code is not None and (type(self.reference_gain_code) is not int or not 0 <= self.reference_gain_code <= 224):
            raise ValueError("Reference gain must be an integer in 0..0xE0")
        if not isinstance(self.calibration_id, str):
            raise ValueError("calibration_id must be a string")
        if self.calibrated and not self.calibration_id.strip():
            raise ValueError("Approved calibration requires a calibration_id")
        if self.baseline_gate_us is not None:
            if not isinstance(self.baseline_gate_us, (tuple, list)) or len(self.baseline_gate_us) != 2:
                raise ValueError("Quiet baseline gate needs two finite times")
            start_us, end_us = self.baseline_gate_us
            if not all(finite_number(value) for value in (start_us, end_us)) or not 0 <= start_us < end_us:
                raise ValueError("Invalid quiet baseline gate")
            if any(start_us <= gate[1] and end_us >= gate[0] for gate in (self.flaw_gate_us, self.backwall_gate_us)):
                raise ValueError("Quiet baseline gate must not overlap diagnostic gates")


def load_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.reader(stream)
        header = next(rows)
        radix = next(rows)
        if len(set(header)) != len(header) or len(radix) != len(header):
            raise ValueError("Invalid CSV header/radix row")
        data_name = next((name for name in header if "ad_data" in name), None)
        if data_name is None or "TRIGGER" not in header or "Sample Period ns" not in header:
            raise ValueError("CSV needs ADC, TRIGGER and explicit Sample Period ns columns")
        data_column = header.index(data_name)
        base = 10 if "UNSIGNED" in radix[data_column] else 2 if "BINARY" in radix[data_column] else 16
        samples, triggers, periods, gains, provenance = [], [], set(), set(), set()
        overrange = False
        for row in rows:
            if len(row) != len(header):
                raise ValueError("Incomplete CSV row")
            record = dict(zip(header, row))
            if int(record["TRIGGER"]):
                triggers.append(len(samples))
            samples.append(int(record[data_name], base))
            periods.add(float(record["Sample Period ns"]) / 1000.0)
            gains.add(int(record.get("gain_val[7:0]", "80"), 16))
            provenance.add(record.get("Source Kind", "RECORDED").strip())
            overrange |= int(record.get("ad_otr_r", "0"), 16) != 0
    if len(triggers) != 1 or len(periods) != 1 or len(gains) != 1:
        raise ValueError("CSV needs one trigger and constant sample period/gain")
    source = "DEMO" if "DEMO" in provenance else "RECORDED"
    if provenance - {"DEMO", "RECORDED", "LIVE"}:
        raise ValueError("Unknown source provenance")
    return Waveform(tuple(samples), triggers[0], periods.pop(), source, gains.pop(), overrange, Path(path).name)


def write_lvgl_csv(path, frame):
    if len(frame.samples) != 4096:
        raise ValueError("Existing LVGL reader requires exactly 4096 samples")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", encoding="ascii", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Sample in Buffer", "Sample in Window", "TRIGGER", "ad_data_r[7:0]", "ad_otr_r", "md1213_ina_OBUF", "gain_val[7:0]", "Sample Period ns", "Source Kind"])
        writer.writerow(["Radix - UNSIGNED", "UNSIGNED", "UNSIGNED", "HEX", "HEX", "HEX", "HEX", "UNSIGNED", "ASCII"])
        for index, value in enumerate(frame.samples):
            writer.writerow([index, index, int(index == frame.trigger_index), f"{value:02X}", int(frame.overrange), int(index == frame.trigger_index), f"{frame.gain:02X}", f"{frame.sample_period_us * 1000:.6f}", frame.source])
    temporary.replace(destination)


def analyze(frame, config):
    result = {"source": frame.source, "frame_id": frame.frame_id, "status": "INVALID", "reason": "", "depth_mm": None, "calibration_id": config.calibration_id, "calibrated": config.calibrated}
    if config.reference_gain_code is not None and frame.gain != config.reference_gain_code:
        result["reason"] = "calibration_gain_mismatch"
        return result
    baseline_samples = frame.samples[max(0, frame.trigger_index - 256):max(0, frame.trigger_index - 8)]
    if config.baseline_gate_us is not None:
        baseline_start, baseline_end = config.baseline_gate_us
        last_time = (len(frame.samples) - 1 - frame.trigger_index) * frame.sample_period_us
        if baseline_end > last_time:
            result["reason"] = "baseline_gate_outside_acquisition_window"
            return result
        baseline_samples = [value for index, value in enumerate(frame.samples) if baseline_start <= (index - frame.trigger_index) * frame.sample_period_us <= baseline_end]
    if len(baseline_samples) < 32:
        result["reason"] = "insufficient_baseline_select_a_verified_quiet_gate"
        return result
    baseline = float(statistics.median(baseline_samples))
    noise = max(0.5, statistics.median(abs(value - baseline) for value in baseline_samples) * 1.4826)
    result.update(baseline=baseline, noise_codes=noise)
    peaks = {}
    selected_samples = []
    for gate_name, interval in (("flaw", config.flaw_gate_us), ("backwall", config.backwall_gate_us)):
        candidates = [(index, value) for index, value in enumerate(frame.samples) if interval[0] <= (index - frame.trigger_index) * frame.sample_period_us <= interval[1]]
        last_time = (len(frame.samples) - 1 - frame.trigger_index) * frame.sample_period_us
        if not candidates or interval[1] > last_time:
            result["reason"] = "gate_outside_acquisition_window"
            return result
        selected_samples.extend(value for _, value in candidates)
        peak_index, peak_value = max(candidates, key=lambda pair: abs(pair[1] - baseline))
        peaks[gate_name] = {"amplitude_codes": abs(peak_value - baseline), "time_us": (peak_index - frame.trigger_index) * frame.sample_period_us}
    result.update(peaks)
    if frame.overrange or any(value in (0, 255) for value in selected_samples):
        result.update(status="INVALID", reason="adc_overrange_or_clipping")
        return result
    flaw_hit = peaks["flaw"]["amplitude_codes"] >= max(config.flaw_threshold_codes, noise * config.minimum_snr)
    backwall_hit = peaks["backwall"]["amplitude_codes"] >= max(config.backwall_threshold_codes, noise * config.minimum_snr)
    result.update(flaw_gate_crossed=flaw_hit, backwall_visible=backwall_hit)
    candidate = "SUSPECT" if flaw_hit else "NO_INDICATION" if backwall_hit else "INVALID"
    reason = "flaw_gate_threshold_crossed" if flaw_hit else "no_flaw_gate_indication" if backwall_hit else "backwall_missing_check_coupling"
    result.update(candidate=candidate, reason=reason)
    if config.calibrated:
        result["status"] = candidate
        if flaw_hit and peaks["flaw"]["time_us"] > config.time_zero_us:
            result["depth_mm"] = (peaks["flaw"]["time_us"] - config.time_zero_us) * config.sound_speed_mps / 2000.0
    else:
        result["status"] = "UNCALIBRATED" if candidate != "INVALID" else "INVALID"
    if frame.source == "DEMO":
        result["status"] = "DEMO_" + result["status"]
    return result


def synthetic_waveform(position_mm):
    samples = []
    for index in range(4096):
        time_us = (index - 256) * 0.04
        value = 128 + math.sin(index * 1.71)
        for echo_time, amplitude in ((10.2, 65.0), (5.6, 52.0 if 20 <= position_mm <= 30 else 2.0)):
            distance = time_us - echo_time
            value += amplitude * math.exp(-distance * distance / 0.04) * math.cos(distance * 24)
        samples.append(int(round(max(0, min(255, value)))))
    return Waveform(tuple(samples), 256, 0.04, "DEMO", frame_id=f"synthetic-{position_mm:g}")
