import argparse
import csv
import hashlib
import html
import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .echo import EchoConfig, Waveform, analyze, finite_number, write_lvgl_csv


def read_ila(path, acquisition):
    period = acquisition.get("ila_sample_period_ns")
    adc_period = acquisition.get("adc_sample_period_ns")
    if (not finite_number(period) or not finite_number(adc_period) or
            not 0 < period <= adc_period or adc_period / period not in (1, 2, 4)):
        raise ValueError("Explicit and consistent ILA/ADC sample periods are required")
    if acquisition.get("source") != "PHYSICAL_ADC_ILA":
        raise ValueError("Expected the explicit physical ILA acquisition provenance record")
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source)
        header, radix = next(reader), next(reader)
        if len(set(header)) != len(header) or len(header) != len(radix):
            raise ValueError("Invalid ILA header or radix")
        required = ("Sample in Buffer", "TRIGGER", "ad_data_r[7:0]", "ad_otr_r", "gain_val_1[7:0]")
        if any(name not in header for name in required):
            raise ValueError("The reviewed raw ILA columns are required")
        if any("HEX" not in radix[header.index(name)] for name in required[2:]):
            raise ValueError("ADC, overrange and gain must have explicit hexadecimal radix")
        samples, triggers, gains, overrange = [], [], set(), False
        for row in reader:
            if len(row) != len(header):
                raise ValueError("Incomplete ILA row")
            record = dict(zip(header, row))
            if int(record["Sample in Buffer"]) != len(samples):
                raise ValueError("ILA sample numbers must be contiguous")
            trigger = int(record["TRIGGER"])
            if trigger not in (0, 1):
                raise ValueError("Invalid digital trigger flag")
            if trigger:
                triggers.append(len(samples))
            samples.append(int(record["ad_data_r[7:0]"], 16))
            gains.add(int(record["gain_val_1[7:0]"], 16))
            overrange |= int(record["ad_otr_r"], 16) != 0
    if len(triggers) != 1 or len(gains) != 1 or len(samples) != acquisition.get("ila_samples_per_frame"):
        raise ValueError("Expected one trigger, constant gain and the declared sample count")
    return Waveform(tuple(samples), triggers[0], period / 1000, "RECORDED", gains.pop(), overrange, Path(path).name)


def gate_peak(frame, start, end, baseline=128):
    candidates = [(index, value) for index, value in enumerate(frame.samples)
                  if start <= (index - frame.trigger_index) * frame.sample_period_us <= end]
    if not candidates:
        return None
    index, value = max(candidates, key=lambda pair: abs(pair[1] - baseline))
    return {"time_us": round((index - frame.trigger_index) * frame.sample_period_us, 6),
            "amplitude_codes": abs(value - baseline)}


def audit_run(directory, holes_csv=None):
    root = Path(directory)
    acquisition = json.loads((root / "acquisition.json").read_text(encoding="utf-8"))
    paths = sorted(root.glob("raw_ila_*.csv"))
    if not 1 <= len(paths) <= 32 or acquisition.get("frame_count") != len(paths):
        raise ValueError("Raw frame count differs from acquisition provenance")
    quality_path = root / "quality.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.exists() else {}
    known = {record["filename"]: record["sha256"] for record in quality.get("frames", [])}
    if known and set(known) != {path.name for path in paths}:
        raise ValueError("Existing raw reference checksum inventory is incomplete")
    frames, records = [], []
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if known and digest != known[path.name]:
            raise ValueError("Raw file differs from the preserved checksum: " + path.name)
        frame = read_ila(path, acquisition)
        frames.append(frame)
        diagnosis = analyze(frame, EchoConfig())
        baseline = diagnosis.get("baseline", statistics.median(frame.samples))
        repetitions = [gate_peak(frame, start, end, baseline) for start, end in
                       ((8.5, 12), (18.5, 22.5), (28.5, 32.5), (38.5, 42.5), (48.5, 52.5), (58.5, 62.5))]
        records.append({"file": path.name, "sha256": digest, "preserved_checksum_matches": True if known else None,
                        "samples": len(frame.samples), "trigger_index": frame.trigger_index,
                        "min_code": min(frame.samples), "max_code": max(frame.samples), "gain_code": frame.gain,
                        "overrange": frame.overrange, "rail_hits": sum(value in (0, 255) for value in frame.samples),
                        "default_diagnosis": diagnosis, "repeated_echo_peaks": repetitions})
    candidates = Counter(record["default_diagnosis"].get("candidate", "INVALID") for record in records)
    warnings = []
    if candidates["SUSPECT"]:
        warnings.append("DEFAULT_FLAW_GATE_CROSSES_OPERATOR_DECLARED_REFERENCE_NOT_VALIDATED_DEFECTS")
    if candidates["INVALID"] or any(not record["default_diagnosis"].get("backwall_visible") for record in records):
        warnings.append("MISSING_BACKWALL_NOT_A_VALID_HEALTHY_BASELINE")
    if any(record["overrange"] or record["rail_hits"] for record in records):
        warnings.append("CLIPPING_OR_OVERRANGE")
    if acquisition["ila_sample_period_ns"] != acquisition["adc_sample_period_ns"]:
        warnings.append("HELD_ADC_REGISTER_VALUES_ARE_NOT_INDEPENDENT_ILA_RATE_ADC_SAMPLES")
    targets = []
    if holes_csv is not None:
        with Path(holes_csv).open(encoding="utf-8-sig", newline="") as source:
            for hole in csv.DictReader(source):
                expected = float(hole["theoretical_tof_us_at_5900_mps"])
                if not finite_number(expected) or not 0 < expected < 100:
                    raise ValueError("Invalid theoretical target time")
                amplitudes = [gate_peak(frame, max(0, expected - .4), expected + .4)["amplitude_codes"] for frame in frames]
                targets.append({"specimen_id": hole["specimen_id"], "hole_id": hole["hole_id"],
                                "reference_depth_mm": float(hole["reference_h_mm_at_T30"]),
                                "theoretical_tof_us": expected, "assumptions": "NOMINAL_T30_C5900_ZERO0_NOT_CALIBRATED",
                                "reference_peak_range_codes": [min(amplitudes), max(amplitudes)],
                                "reference_crosses_default_flaw_threshold": any(value >= 35 for value in amplitudes),
                                "actual_hole_waveform_available": False, "detected": None})
    result = {"schema": "ut-reference-audit/1", "created_utc": datetime.now(timezone.utc).isoformat(),
              "source_directory": str(root.resolve()), "source": "RECORDED_FROM_PHYSICAL_ADC_ILA",
              "provenance_basis": "PRESERVED_RECORDS_NOT_NEW_HARDWARE_ACCEPTANCE",
              "specimen_role": acquisition.get("specimen_role"), "raw_files_modified": False,
              "frame_count": len(frames), "ila_sample_period_ns": acquisition["ila_sample_period_ns"],
              "adc_sample_period_ns": acquisition["adc_sample_period_ns"],
              "min_code": min(record["min_code"] for record in records), "max_code": max(record["max_code"] for record in records),
              "default_candidate_counts": dict(candidates), "frames": records, "target_readiness": targets,
              "warnings": warnings, "calibrated": False, "accepted_as_calibration": False,
              "real_defect_detection_verified": False, "spatial_scan_performed": False,
              "operations": "READ_ONLY_SOURCE_FILES_DERIVED_REPORTS_NO_BOARD_TX_MOTION_FLASH_OR_SD"}
    return result, frames


def waveform_svg(frames, start=-1, end=18):
    width, height = 960, 260
    paths = []
    for frame in frames:
        points = [(56 + ((index - frame.trigger_index) * frame.sample_period_us - start) / (end - start) * 880,
                   228 - value / 255 * 204) for index, value in enumerate(frame.samples)
                  if start <= (index - frame.trigger_index) * frame.sample_period_us <= end]
        paths.append('<polyline fill="none" stroke="#46c5b6" stroke-opacity=".45" stroke-width="1" points="' +
                     " ".join(f"{position_x:.1f},{position_y:.1f}" for position_x, position_y in points) + '"/>')
    ticks = ''.join(f'<text x="{56 + position * 880 / 4:.1f}" y="252" fill="#b9c9d5">{start + (end - start) * position / 4:g} us</text>'
                    for position in range(5))
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Recorded uncalibrated ADC waveforms">' + \
           '<rect width="960" height="260" rx="12" fill="#142431"/>' + ''.join(paths) + ticks + '</svg>'


def render_report(audit, frames):
    rows = ''.join('<tr>' + ''.join('<td>' + html.escape(str(value)) + '</td>' for value in
                    (record['file'], record['min_code'], record['max_code'], record['gain_code'],
                     record['default_diagnosis'].get('candidate', 'INVALID'), record['default_diagnosis']['reason'])) + '</tr>'
                   for record in audit['frames'])
    targets = ''.join('<tr>' + ''.join('<td>' + html.escape(str(value)) + '</td>' for value in
                     (record['specimen_id'], record['hole_id'], record['reference_depth_mm'], record['theoretical_tof_us'],
                      record['reference_peak_range_codes'], '有重叠风险' if record['reference_crosses_default_flaw_threshold'] else '仍需实测')) + '</tr>'
                      for record in audit['target_readiness'])
    warnings = ''.join('<li>' + html.escape(warning) + '</li>' for warning in audit['warnings'])
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>完整块原始数据复核</title>
<style>body{{background:#0b1620;color:#d9e6ec;font:16px/1.7 system-ui;margin:36px auto;max-width:1100px;padding:0 20px}}h1,h2{{color:#57cbbc}}section{{background:#142431;padding:20px;border-radius:14px;margin:20px 0}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border-bottom:1px solid #34505e;padding:8px;text-align:left}}.warn{{color:#ffd38a}}code{{overflow-wrap:anywhere}}svg{{width:100%}}</style>
<h1>完整块原始数据复核 · 未标定</h1><p>历史原始回放，不是当前实时采集，不是两块缺陷工件检出报告。</p>
<section><b>原始记录</b>：{audit['frame_count']} 帧，码值 {audit['min_code']}～{audit['max_code']}，
ILA {audit['ila_sample_period_ns']} ns / ADC {audit['adc_sample_period_ns']} ns。原始文件未修改。<br>
<code>{html.escape(audit['source_directory'])}</code></section>
<section class="warn"><h2>必须先解决</h2><ul>{warnings}</ul>
不能把前段强信号直接当作孔洞，也不能为了不报警而任意提高门限或屏蔽浅层。
孔位表只用于之后对照，不参与检测，更不会被填入二维/三维地图。</section>
<h2>前段信号与首个底波窗</h2>{waveform_svg(frames)}
<h2>完整窗口</h2>{waveform_svg(frames, -10, 70)}
<h2>逐帧复核</h2><table><tr><th>文件</th><th>最小</th><th>最大</th><th>增益码</th><th>默认候选</th><th>原因</th></tr>{rows}</table>
<h2>目标孔对应理论时窗风险</h2><p>仅按名义厚度30 mm、参考声速5900 m/s、零点0估算，±0.4 us窗内检查完整块信号；不是已校准盲区测量。</p>
<table><tr><th>试块</th><th>孔</th><th>参考h/mm</th><th>理论us</th><th>完整块峰值范围</th><th>准备状态</th></tr>{targets}</table>
<p class="warn">声学未标定；真实孔洞回波未提供；不得宣称检出率、孔深精度或无缺陷。</p></html>'''


def main():
    parser = argparse.ArgumentParser(description="Read-only audit of preserved raw ILA reference data; never calibrates or accesses hardware")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--holes-csv", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    audit, frames = audit_run(arguments.directory, arguments.holes_csv)
    arguments.out.mkdir(parents=True, exist_ok=False)
    replay = arguments.out / "recorded-replay"
    replay.mkdir()
    for index, frame in enumerate(frames, 1):
        write_lvgl_csv(replay / f"frame_{index:03d}.csv", frame)
    (arguments.out / "audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (arguments.out / "report.html").write_text(render_report(audit, frames), encoding="utf-8")
    print(json.dumps({key: audit[key] for key in ("frame_count", "min_code", "max_code", "default_candidate_counts", "warnings", "calibrated")}, indent=2))


if __name__ == "__main__":
    main()
