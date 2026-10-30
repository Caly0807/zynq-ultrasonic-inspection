import csv
import hashlib
import html
import json
import math
import os
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .echo import EchoConfig, load_csv
from .scan import ScanPlan, TERMINAL_STATES, scan_status


SUMMARY_FIELDS = ["point_id", "stage_x_mm", "world_x_mm", "world_y_mm", "world_z_mm", "source", "status", "depth_mm", "waveform"]
COLORS = {"SUSPECT": "#ff7b72", "NO_INDICATION": "#50d6b0", "UNCALIBRATED": "#edc66c", "INVALID": "#b8a9e5"}
LABELS = {"SUSPECT": "疑似指示", "NO_INDICATION": "门区内无指示", "UNCALIBRATED": "未标定", "INVALID": "无效"}


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Report coordinates and depths must be finite numbers")
    return value


def _reject_constant(value):
    raise ValueError(f"Non-finite JSON value: {value}")


def _same_number(actual, expected):
    if not math.isclose(_number(actual), _number(expected), rel_tol=1e-9, abs_tol=1e-7):
        raise ValueError("Scan record numeric fields disagree")


def _local_file(root, relative):
    path = root / relative
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError(f"Missing or unsafe scan file: {relative}")
    return path


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _kind(point):
    return point["diagnosis"]["status"].removeprefix("DEMO_")


def _text(value):
    return html.escape(str(value), quote=True)


def _mm(value):
    return "—" if value is None else f"{value:.3f}".rstrip("0").rstrip(".")


@dataclass
class ValidatedScan:
    root: Path
    manifest: dict
    plan: ScanPlan
    echo: EchoConfig
    points: list
    hashes: dict
    warnings: list


def validate_scan(directory):
    root = Path(directory).resolve(strict=True)
    _local_file(root, "manifest.json")
    manifest = scan_status(root)
    if manifest["status"] not in TERMINAL_STATES:
        raise ValueError("Only finished scans can be exported; stop the software task first")
    run_id = manifest["run_id"]
    if not isinstance(run_id, str) or len(run_id) != 32 or any(character not in "0123456789abcdef" for character in run_id):
        raise ValueError("Invalid scan run ID")
    if manifest.get("stage") not in ("SIMULATED", "MANUAL"):
        raise ValueError("Unknown scan stage adapter")
    if manifest.get("hardware_motion_driver") is not False or manifest.get("transmit_enabled_by_this_software") is not False:
        raise ValueError("Unsupported hardware/transmit declaration")
    try:
        plan, echo = ScanPlan(**manifest["plan"]), EchoConfig(**manifest["echo"])
    except (KeyError, TypeError) as error:
        raise ValueError("Invalid scan plan or echo configuration") from error
    count = manifest.get("point_count")
    if type(count) is not int or not 0 <= count <= plan.point_count:
        raise ValueError("Invalid committed point count")
    if manifest["status"] == "COMPLETE" and count != plan.point_count:
        raise ValueError("COMPLETE scan does not contain all planned points")
    journal = root / "points.jsonl"
    points = []
    hashes = {"manifest.json": _digest(root / "manifest.json")}
    if journal.exists():
        journal = _local_file(root, "points.jsonl")
        for line_number, line in enumerate(journal.read_text(encoding="utf-8").splitlines(), 1):
            try:
                point = json.loads(line, parse_constant=_reject_constant)
                if not isinstance(point, dict):
                    raise ValueError("Point must be an object")
                points.append(point)
            except ValueError as error:
                raise ValueError(f"Invalid committed journal line {line_number}; no automatic repair performed") from error
        hashes["points.jsonl"] = _digest(journal)
    if len(points) != count:
        raise ValueError("Manifest and committed point journal disagree")
    summary_path = _local_file(root, "summary.csv")
    with summary_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != SUMMARY_FIELDS:
            raise ValueError("Unexpected scan summary columns")
        rows = list(reader)
    if len(rows) != count:
        raise ValueError("Summary and committed point journal disagree")
    hashes["summary.csv"] = _digest(summary_path)
    missing_original_hashes = 0
    for index, (point, row, position) in enumerate(zip(points, rows, plan.positions())):
        try:
            if type(point["point_index"]) is not int or point["point_index"] != index:
                raise ValueError("Unexpected point index")
            if point["point_id"] != f"{run_id}_{index:05d}" or row["point_id"] != point["point_id"]:
                raise ValueError("Point ID does not belong to this run")
            _same_number(point["stage_x_mm"], position)
            _same_number(float(row["stage_x_mm"]), position)
            source = point["source"]
            if source not in ("DEMO", "RECORDED", "LIVE") or row["source"] != source:
                raise ValueError("Inconsistent point source")
            for name in ("vision", "vision_after_capture"):
                observation = point[name]
                coordinates = observation["position_mm"]
                if observation["valid"] is not True or len(coordinates) != 3:
                    raise ValueError("Invalid committed probe position")
                for coordinate in coordinates:
                    _number(coordinate)
                if observation["source"] not in ("DEMO", "RECORDED", "LIVE"):
                    raise ValueError("Unknown pose source")
                if source != "DEMO" and (observation["source"] == "DEMO" or manifest["stage"] == "SIMULATED"):
                    raise ValueError("Simulation cannot be promoted to recorded/live scan")
                if source == "LIVE" and observation["source"] != "LIVE":
                    raise ValueError("LIVE point lacks LIVE positioning")
            for field, coordinate in zip(("world_x_mm", "world_y_mm", "world_z_mm"), point["vision"]["position_mm"]):
                _same_number(float(row[field]), coordinate)
            diagnosis = point["diagnosis"]
            if diagnosis["source"] != source or diagnosis["frame_id"] != point["frame_id"]:
                raise ValueError("Diagnosis does not belong to this frame")
            if row["status"] != diagnosis["status"] or _kind(point) not in LABELS:
                raise ValueError("Unknown or inconsistent diagnostic status")
            if diagnosis["status"].startswith("DEMO_") != (source == "DEMO") and diagnosis["status"] != "INVALID":
                raise ValueError("Diagnostic DEMO provenance mismatch")
            if diagnosis["calibrated"] is not echo.calibrated or diagnosis["calibration_id"] != echo.calibration_id:
                raise ValueError("Calibration metadata disagree")
            if not echo.calibrated and _kind(point) in ("SUSPECT", "NO_INDICATION"):
                raise ValueError("Uncalibrated data cannot contain accepted diagnostic decisions")
            depth = diagnosis["depth_mm"]
            if depth is None:
                if row["depth_mm"] != "":
                    raise ValueError("Summary depth disagrees")
            else:
                if _number(depth) < 0 or not echo.calibrated or _kind(point) != "SUSPECT":
                    raise ValueError("Invalid diagnostic depth")
                _same_number(float(row["depth_mm"]), depth)
            expected_waveform = f"waveforms/{index:05d}.csv"
            if point["waveform"] != expected_waveform or row["waveform"] != expected_waveform:
                raise ValueError("Waveform path does not match this point")
            waveform_path = _local_file(root, expected_waveform)
            digest = _digest(waveform_path)
            if "waveform_sha256" in point:
                if point["waveform_sha256"] != digest:
                    raise ValueError("Waveform SHA-256 mismatch")
            else:
                missing_original_hashes += 1
            frame = load_csv(waveform_path)
            if len(frame.samples) != 4096 or (frame.source == "DEMO") != (source == "DEMO"):
                raise ValueError("Raw waveform length or provenance disagrees")
            hashes[expected_waveform] = digest
        except (KeyError, TypeError, ValueError, StopIteration, IndexError, AttributeError) as error:
            raise ValueError(f"Invalid scan point {index}: {error}") from error
    warnings = ["单轴离散采样复盘，不是二维 C 扫；未采样区没有检测结论。",
                "无指示不等于合格；疑似指示需结合耦合、标定和人工复核。",
                "本报告保留历史诊断，不重新运行判决或控制硬件；LIVE 导入后按 RECORDED 展示。",
                "SHA-256 仅核对文件一致性，不是防篡改数字签名。"]
    if missing_original_hashes:
        warnings.append(f"旧记录中 {missing_original_hashes} 点无采集提交时校验和；本次仅计算导出时 SHA-256。")
    if manifest["status"] != "COMPLETE":
        warnings.append(f"任务以 {manifest['status']} 结束，只报告 {count}/{plan.point_count} 个已提交点；不能视为完整扫描。")
    if manifest.get("cleanup_complete") is False:
        warnings.append("任务清理未成功，不能由此推断设备处于安全/停止状态。")
    if not echo.calibrated:
        warnings.append("当前参数未标定，特征和候选指示均不能当作有效探伤结论。")
    if echo.calibration_id.startswith("DEMO_") or any(point["source"] == "DEMO" for point in points):
        warnings.append("包含合成/模拟数据或演示标定；仅用于软件联调，不是实际缺陷证据。")
    if plan.point_count > 256:
        warnings.append("当前 LVGL 复盘最多导入 256 点；本报告未截断扫描数据。")
    orphan_count = len(list((root / "waveforms").glob("*.csv"))) - len(points)
    if orphan_count > 0:
        warnings.append(f"另有 {orphan_count} 个未提交波形文件未纳入报告，可能来自中断中的点。")
    return ValidatedScan(root, manifest, plan, echo, points, hashes, warnings)


def render_scan_svg(scan):
    left, right = 90, 1040
    span = scan.plan.end_mm - scan.plan.start_mm
    def horizontal(position):
        return (left + right) / 2 if span == 0 else left + (position - scan.plan.start_mm) / span * (right - left)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="430" viewBox="0 0 1100 430" role="img">',
             '<rect width="1100" height="430" rx="20" fill="#111f30"/>',
             '<g font-family="Microsoft YaHei, sans-serif" fill="#c8d5e4">',
             '<text x="34" y="40" font-size="20">单轴采样分布 · 非二维 C 扫</text>',
             '<text x="34" y="67" font-size="13" fill="#8ea4ba">横轴为滑台设置坐标，不是未经标定的工件照片坐标；离散点之间不插值。</text>',
             f'<line x1="{left}" y1="127" x2="{right}" y2="127" stroke="#41556d" stroke-width="2"/>']
    for tick in range(6):
        position = scan.plan.start_mm + span * tick / 5
        coordinate = horizontal(position)
        parts.append(f'<text x="{coordinate:.2f}" y="166" text-anchor="middle" font-size="12">{_mm(position)} mm</text>')
        if span == 0:
            break
    for index, point in enumerate(scan.points):
        color, coordinate = COLORS[_kind(point)], horizontal(point["stage_x_mm"])
        title = f'{index + 1}: X={_mm(point["stage_x_mm"])} mm / {point["diagnosis"]["status"]}'
        parts.append(f'<circle cx="{coordinate:.2f}" cy="127" r="7" fill="{color}" stroke="#111f30" stroke-width="2"><title>{_text(title)}</title></circle>')
    depths = [point["diagnosis"]["depth_mm"] for point in scan.points if point["diagnosis"]["depth_mm"] is not None]
    parts.append('<text x="34" y="218" font-size="16">疑似反射深度估计（mm）</text>')
    if depths:
        limit = max(5., math.ceil(max(depths) / 5) * 5)
        for tick in range(4):
            vertical = 245 + tick * 45
            parts.append(f'<line x1="{left}" y1="{vertical}" x2="{right}" y2="{vertical}" stroke="#283e56"/><text x="72" y="{vertical + 4}" text-anchor="end" font-size="12">{_mm(limit * tick / 3)}</text>')
        for point in scan.points:
            depth = point["diagnosis"]["depth_mm"]
            if depth is not None:
                parts.append(f'<circle cx="{horizontal(point["stage_x_mm"]):.2f}" cy="{245 + depth / limit * 135:.2f}" r="6" fill="#ff7b72"><title>{_text(_mm(depth))} mm</title></circle>')
    else:
        parts.append('<text x="90" y="300" font-size="16" fill="#8ea4ba">没有可展示的已标定疑似深度；不以 0 mm 代替未知。</text>')
    parts.append('</g></svg>')
    return "".join(parts)


def render_waveform_svg(frame, config):
    first_time = max(-2., -frame.trigger_index * frame.sample_period_us)
    last_time = min((len(frame.samples) - 1 - frame.trigger_index) * frame.sample_period_us,
                    max(20., config.backwall_gate_us[1] * 1.4))
    extent = max(frame.sample_period_us, last_time - first_time)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="290" viewBox="0 0 1100 290" role="img">',
             '<rect width="1100" height="290" rx="20" fill="#111f30"/>',
             '<g font-family="Microsoft YaHei, sans-serif" fill="#a8bbcf" font-size="12">']
    for tick in range(5):
        vertical = 35 + tick * 48
        parts.append(f'<line x1="70" y1="{vertical}" x2="1050" y2="{vertical}" stroke="#283e56"/><text x="54" y="{vertical + 4}" text-anchor="end">{round(255 - tick * 255 / 4)}</text>')
    for gate, color in ((config.flaw_gate_us, "#ff7b72"), (config.backwall_gate_us, "#50d6b0")):
        start, end = max(first_time, gate[0]), min(last_time, gate[1])
        if end > start:
            parts.append(f'<rect x="{70 + (start - first_time) / extent * 980:.2f}" y="35" width="{(end - start) / extent * 980:.2f}" height="192" fill="{color}" fill-opacity="0.1"/>')
    for tick in range(6):
        parts.append(f'<text x="{70 + tick * 196}" y="257" text-anchor="middle">{_mm(first_time + extent * tick / 5)} μs</text>')
    coordinates = []
    for index, sample in enumerate(frame.samples):
        time_us = (index - frame.trigger_index) * frame.sample_period_us
        if first_time <= time_us <= last_time:
            coordinates.append(f"{70 + (time_us - first_time) / extent * 980:.2f},{35 + (255 - sample) / 255 * 192:.2f}")
    parts.append(f'<polyline points="{" ".join(coordinates)}" fill="none" stroke="#6ce7d4" stroke-width="1.4"/>')
    parts.append('<text x="70" y="20">ADC 码值 · 原始样本局部视图（红：缺陷门；绿：底波门）</text></g></svg>')
    return "".join(parts)


def _waveform_link(scan, destination, relative):
    source = scan.root / relative
    try:
        return quote(Path(os.path.relpath(source, destination)).as_posix(), safe="/.")
    except ValueError:
        return source.as_uri()


def build_report(directory, output=None):
    scan = validate_scan(directory)
    destination = Path(output).resolve() if output else scan.root / "report"
    if scan.root.is_relative_to(destination) or destination.is_relative_to(scan.root / "waveforms") or destination.is_relative_to(scan.root / "control"):
        raise ValueError("Report destination would conflict with source data")
    if destination.exists():
        raise FileExistsError(f"Report destination already exists: {destination}")
    categories = Counter(_kind(point) for point in scan.points)
    sources = Counter("DEMO" if point["source"] == "DEMO" else "RECORDED" for point in scan.points)
    label = "DEMO · 包含模拟数据，非实测" if sources["DEMO"] else "RECORDED · 历史数据复盘" if scan.points else "NO DATA · 无已提交点"
    created = datetime.now(timezone.utc).isoformat()
    report = {"schema": 1, "report_type": "SINGLE_AXIS_REVIEW_NOT_C_SCAN", "created_at_utc": created,
              "run_id": scan.manifest["run_id"], "run_status": scan.manifest["status"], "evidence_label": label,
              "committed_points": len(scan.points), "planned_points": scan.plan.point_count,
              "counts": dict(categories), "display_source_counts": dict(sources), "plan": asdict(scan.plan),
              "echo": asdict(scan.echo), "warnings": scan.warnings, "source_file_sha256": scan.hashes,
              "points": [{**point, "display_source": "DEMO" if point["source"] == "DEMO" else "RECORDED"} for point in scan.points],
              "task_error": scan.manifest.get("error"), "task_stop_error": scan.manifest.get("stop_error")}
    scan_svg = render_scan_svg(scan)
    selected = next((point for point in scan.points if _kind(point) == "SUSPECT"), scan.points[0] if scan.points else None)
    waveform_svg = render_waveform_svg(load_csv(scan.root / selected["waveform"]), scan.echo) if selected else ""
    rows = []
    for point in scan.points:
        diagnosis = point["diagnosis"]
        rows.append(f'<tr><td>{point["point_index"] + 1}</td><td>{_mm(point["stage_x_mm"])}</td>'
                    f'<td>{_text(", ".join(_mm(coordinate) for coordinate in point["vision"]["position_mm"]))}</td>'
                    f'<td><span style="color:{COLORS[_kind(point)]}">{_text(diagnosis["status"])}</span></td>'
                    f'<td>{_mm(diagnosis["depth_mm"])}</td><td>{"DEMO" if point["source"] == "DEMO" else "RECORDED"}</td>'
                    f'<td><a href="{_text(_waveform_link(scan, destination, point["waveform"]))}">原始波形</a></td></tr>')
    warning_html = "".join(f"<li>{_text(warning)}</li>" for warning in scan.warnings)
    calibration = f"记录声明已标定 · {scan.echo.calibration_id}" if scan.echo.calibrated else "未标定 · 不作有效判决"
    if scan.echo.calibration_id.startswith("DEMO_"):
        calibration = "演示标定 · 不用于测量"
    cards = "".join(f'<div class="card"><span>{LABELS[kind]}</span><strong style="color:{COLORS[kind]}">{categories[kind]}</strong></div>' for kind in LABELS)
    waveform_title = f'参考点 #{selected["point_index"] + 1} · X={_mm(selected["stage_x_mm"])} mm · {_text(selected["diagnosis"]["status"])}' if selected else "无参考波形"
    task_errors = " · ".join(str(value) for value in (scan.manifest.get("error"), scan.manifest.get("stop_error")) if value)
    error_html = f'<p class="error">任务异常：{_text(task_errors)}</p>' if task_errors else ""
    document = _render_html(scan, label, created, calibration, cards, waveform_title, scan_svg, waveform_svg,
                            warning_html, error_html, rows)
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if any(_digest(_local_file(scan.root, relative)) != digest for relative, digest in scan.hashes.items()):
        raise ValueError("Source data changed during report generation")
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "report.html").write_text(document, encoding="utf-8")
    (destination / "report.json").write_text(payload, encoding="utf-8")
    (destination / "scan.svg").write_text(scan_svg, encoding="utf-8")
    if waveform_svg:
        (destination / "waveform.svg").write_text(waveform_svg, encoding="utf-8")
    return destination


def _render_html(scan, label, created, calibration, cards, waveform_title, scan_svg, waveform_svg,
                 warning_html, error_html, rows):
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>单轴扫描复盘报告</title><style>
:root{{color-scheme:dark}}*{{box-sizing:border-box}}
body{{margin:0;background:#091422;color:#e0eaf4;font:15px/1.65 "Microsoft YaHei","Segoe UI",sans-serif}}
main{{max-width:1200px;margin:42px auto;padding:0 28px}}
header{{display:flex;justify-content:space-between;gap:24px;border-bottom:1px solid #2a4057;padding-bottom:24px}}
.eyebrow{{letter-spacing:3px;color:#50d6b0;font-size:12px}}h1{{font-size:32px;margin:10px 0}}
h2{{font-size:19px;margin:30px 0 14px}}p{{color:#9fb3c8;margin:8px 0}}
.badge{{display:inline-block;background:#27323a;border:1px solid #edc66c;color:#edc66c;border-radius:20px;padding:6px 15px;font-size:13px}}
.progress{{font-size:38px;font-weight:650;color:#50d6b0}}
.meta{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:24px 0}}
.meta>div,.card{{background:#111f30;border:1px solid #253b51;border-radius:16px;padding:18px}}
small,.card span{{display:block;color:#9fb3c8}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}}.card strong{{font-size:32px}}
svg{{width:100%;height:auto;display:block}}
.notice{{border-left:3px solid #edc66c;background:#1c2632;padding:12px 20px;border-radius:4px 12px 12px 4px}}
.notice ul{{padding-left:20px;margin:4px 0}}.notice li{{margin:7px 0;color:#d0c6aa}}
.table-wrap{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{padding:12px 10px;border-bottom:1px solid #283d53;text-align:left}}th{{background:#13243a;color:#a8bed4}}
a{{color:#6ce7d4}}code{{font-size:12px;overflow-wrap:anywhere}}
footer{{border-top:1px solid #2a4057;margin-top:30px;padding:20px 0;color:#8ea4ba;font-size:12px}}.error{{color:#ff7b72}}
@media(max-width:700px){{header{{display:block}}.cards,.meta{{grid-template-columns:repeat(2,1fr)}}main{{padding:0 16px}}}}
@media print{{:root{{color-scheme:light}}body{{background:white;color:#17283b}}main{{margin:0;max-width:none;padding:0}}
.meta>div,.card,.notice,th{{background:#f0f4f8;color:#17283b}}p,small,.card span,.notice li{{color:#425469}}
.card,.meta>div{{break-inside:avoid}}.table-wrap{{overflow:visible}}a{{color:#176a70}}header{{break-inside:avoid}}}}
</style></head><body><main>
<header><div><div class="eyebrow">ULTRASONIC STUDIO / INSPECTION REVIEW</div>
<h1>单轴扫描复盘报告</h1><div class="badge">{_text(label)}</div>
<p>离线可追溯记录 · 不是工件合格证 · 不是二维 C 扫</p></div>
<div><small>已提交 / 计划点数</small><div class="progress">{len(scan.points)} / {scan.plan.point_count}</div>
<small>任务状态：{_text(scan.manifest["status"])}</small></div></header>
<section class="meta"><div><small>扫描范围 / 设定步长</small>{_mm(scan.plan.start_mm)} → {_mm(scan.plan.end_mm)} mm / {_mm(scan.plan.step_mm)} mm</div>
<div><small>标定状态</small>{_text(calibration)}</div><div><small>设备边界</small>无硬件运动驱动 · 软件不使能发射</div></section>
<section class="cards">{cards}</section><h2>采样位置与反射深度</h2>{scan_svg}
<h2>{waveform_title}</h2>{waveform_svg}
<p>只展示参考点门区附近原始 ADC 样本。完整 4096 点数据见各行链接；不从照片推断内部损伤。</p>
<h2>使用边界与异常</h2><section class="notice"><ul>{warning_html}</ul></section>{error_html}
<h2>逐点追溯</h2><div class="table-wrap"><table><thead><tr><th>点</th><th>轴 X / mm</th><th>记录世界坐标 / mm</th>
<th>历史诊断</th><th>深度估计 / mm</th><th>复盘来源</th><th>数据</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<footer>批次 <code>{_text(scan.manifest["run_id"])}</code><br>生成时间（UTC）：{_text(created)}<br>
原始诊断、参数和文件 SHA-256 见同目录 report.json。报告无网络依赖，可用浏览器打印为 PDF。
分享时保留扫描目录及其 waveforms 子目录，避免原始数据链接失效。</footer></main></body></html>'''
