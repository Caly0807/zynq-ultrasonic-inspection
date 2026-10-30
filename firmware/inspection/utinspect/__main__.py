import argparse
import glob
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from .camera import Camera
from .echo import EchoConfig, analyze, load_csv, synthetic_waveform
from .scan import ExchangeCapture, ManualStage, PoseFile, ReplayCapture, ScanPlan, Scanner, SimulatedStage, request_control, scan_status
from .vision import Intrinsics, Tracker, atomic_json, calibrate_images, generate_targets, synthetic_scene


ROOT = Path(__file__).resolve().parents[1]


def settings(path):
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else {}


def output_directory(argument, prefix):
    return Path(argument) if argument else ROOT / "runs" / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S_%f}"


def demo_locator(position_mm, settled_ns, stop_event):
    image, intrinsics = synthetic_scene(position_mm)
    tracker = Tracker(intrinsics, tip_offset_mm=(0., 0., -35.), contact_tolerance_mm=5.0)
    observation, _ = tracker.locate(image, "DEMO")
    return observation


def show_lvgl(path):
    executable = ROOT.parent / "lvgl_ui/build/desktop/ut_lvgl.exe"
    if os.name != "nt" or not executable.is_file():
        raise ValueError("The existing Windows LVGL executable is unavailable")
    option = "--scan" if path.name == "summary.csv" else "--csv"
    subprocess.Popen([str(executable), option, str(path.resolve())], cwd=executable.parent)


def run_vision(args):
    import cv2

    data = settings(args.config)
    intrinsics = Intrinsics.load(args.intrinsics) if args.intrinsics else None
    tracker = Tracker(intrinsics=intrinsics, **data.get("vision", {}))
    if args.camera < 0 or args.frames < 0 or args.width <= 0 or args.height <= 0:
        raise ValueError("Invalid camera index, frame limit or image dimensions")
    if args.save_calibration and not args.preview:
        raise ValueError("Calibration photographs require --preview and an explicit S keypress")
    output = Path(args.pose)
    atomic_json(output, {"valid": False, "reason": "camera_opening", "position_mm": None,
                         "timestamp_ns": time.monotonic_ns(), "source": "LIVE", "marker_ids": [], "reprojection_error_px": None})
    camera = Camera(args.camera, args.width, args.height, args.camera_name, args.ffmpeg)
    try:
        print("CAMERA_BACKEND=" + camera.backend, flush=True)
        frame_count, last_report = 0, 0.0
        while args.frames == 0 or frame_count < args.frames:
            ok, image = camera.read()
            if not ok or image is None:
                raise ValueError("Camera frame read failed")
            observation, annotated = tracker.locate(image, "LIVE", timestamp_ns=camera.timestamp_ns)
            atomic_json(output, asdict(observation))
            frame_count += 1
            if time.monotonic() - last_report >= 1:
                print(json.dumps(asdict(observation), ensure_ascii=False), flush=True)
                last_report = time.monotonic()
            if args.preview:
                label = "LIVE VISION / " + ("VALID TIP" if observation.valid else observation.reason)
                cv2.putText(annotated, label, (12, annotated.shape[0] - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                            (20, 150, 20) if observation.valid else (0, 90, 230), 1)
                cv2.imshow("Ultrasonic Studio - vision diagnostic (not final LVGL UI)", annotated)
                key = cv2.waitKey(1) & 255
                if key in (27, ord("q")):
                    break
                if key == ord("s") and args.save_calibration:
                    found, _ = cv2.findChessboardCornersSB(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (9, 6))
                    if not found:
                        print("NOT_SAVED: checkerboard 9x6 inner corners were not detected", flush=True)
                        continue
                    directory = Path(args.save_calibration)
                    directory.mkdir(parents=True, exist_ok=True)
                    filename = directory / f"checkerboard_{time.time_ns()}.png"
                    if not cv2.imwrite(str(filename), image):
                        raise OSError("Could not save the explicitly requested calibration photograph")
                    print(f"CALIBRATION_IMAGE_SAVED={filename}", flush=True)
    finally:
        camera.release()
        if args.preview:
            cv2.destroyAllWindows()
        atomic_json(output, {"valid": False, "reason": "camera_closed", "position_mm": None,
                             "timestamp_ns": time.monotonic_ns(), "source": "LIVE", "marker_ids": [], "reprojection_error_px": None})


def main():
    parser = argparse.ArgumentParser(description="Vision / single-axis scan / ultrasonic diagnostics. No motor or HV outputs.")
    commands = parser.add_subparsers(dest="command", required=True)
    targets = commands.add_parser("targets", help="Generate dimensioned SVG print targets")
    targets.add_argument("--out", default=str(ROOT / "assets"))
    targets.add_argument("--marker-mm", type=float, default=30)
    targets.add_argument("--square-mm", type=float, default=20)
    calibrate = commands.add_parser("calibrate", help="Camera intrinsics from saved checkerboard images")
    calibrate.add_argument("--images", required=True, help="Quoted image glob, e.g. calibration/*.jpg")
    calibrate.add_argument("--columns", type=int, default=9)
    calibrate.add_argument("--rows", type=int, default=6)
    calibrate.add_argument("--square-mm", type=float, default=20)
    calibrate.add_argument("--out", default=str(ROOT / "calibration/camera.json"))
    vision = commands.add_parser("vision", help="Live marker detection, optional calibrated tip position")
    vision.add_argument("--camera", type=int, default=0)
    vision.add_argument("--camera-name", help='Windows DirectShow device name, e.g. "PC Camera"')
    vision.add_argument("--ffmpeg", help="Optional installed FFmpeg executable for named Windows capture")
    vision.add_argument("--config")
    vision.add_argument("--intrinsics")
    vision.add_argument("--pose", default=str(ROOT / "runs/latest_pose.json"))
    vision.add_argument("--width", type=int, default=640)
    vision.add_argument("--height", type=int, default=480)
    vision.add_argument("--frames", type=int, default=0)
    vision.add_argument("--preview", action="store_true")
    vision.add_argument("--save-calibration", help="Only with --preview: press S to save a detected checkerboard view")
    workpiece = commands.add_parser("workpiece", help="Local PC workpiece/probe visual debug console; not a hardware controller")
    workpiece.add_argument("--camera", type=int, default=0)
    workpiece.add_argument("--camera-name")
    workpiece.add_argument("--ffmpeg")
    workpiece.add_argument("--image", help="Optional recorded image; never upgraded to LIVE")
    workpiece.add_argument("--width", type=int, default=640)
    workpiece.add_argument("--height", type=int, default=480)
    workpiece.add_argument("--length-mm", type=float, default=80)
    workpiece.add_argument("--width-mm", type=float, default=80)
    workpiece.add_argument("--thickness-mm", type=float, default=30)
    workpiece.add_argument("--dimensions-confirmed", action="store_true")
    workpiece.add_argument("--probe-mode", choices=("red", "round", "manual"), default="red",
                           help="Initial probe method, used only when no saved profile exists")
    workpiece.add_argument("--profile", default=str(ROOT / "calibration/workpiece_setup.json"),
                           help="Saved visual settings; loaded dimensions and detection settings override initial CLI values")
    workpiece.add_argument("--port", type=int, default=8765)
    workpiece.add_argument("--out")
    workpiece.add_argument("--preview", action="store_true")
    visual_check = commands.add_parser("workpiece-selftest", help="Synthetic visual pipeline self-check; no camera or hardware")
    visual_check.add_argument("--out")
    map_demo = commands.add_parser("map-demo", help="Create synthetic 2D/depth/3D review without camera or hardware")
    map_demo.add_argument("--out")
    manual_map = commands.add_parser("manual-map", help="Local manual-grid inspection station; no motor or HV outputs")
    manual_map.add_argument("--mode", choices=("DEMO", "RECORDED", "LIVE"), default="RECORDED")
    manual_map.add_argument("--config")
    manual_map.add_argument("--exchange")
    manual_map.add_argument("--port", type=int, default=8766)
    manual_map.add_argument("--out")
    manual_map.add_argument("--preview", action="store_true")
    echo = commands.add_parser("echo", help="Analyze a recorded waveform without upgrading it to LIVE")
    echo.add_argument("csv")
    echo.add_argument("--config")
    demo = commands.add_parser("demo", help="Fully synthetic end-to-end test, never an actual inspection")
    demo.add_argument("--out")
    demo.add_argument("--config")
    demo.add_argument("--report", action="store_true", help="Export a clearly marked offline DEMO report; does not open a browser")
    demo.add_argument("--show-ui", action="store_true")
    plan_preview = commands.add_parser("scan-plan", help="Validate parameters and preview positions without hardware activity")
    plan_preview.add_argument("--config")
    status = commands.add_parser("scan-status", help="Read a saved task snapshot; not a hardware/process health check")
    status.add_argument("run")
    control = commands.add_parser("scan-control", help="Request cooperative pause/resume/stop; NOT emergency stop or motor control")
    control.add_argument("run")
    control.add_argument("action", choices=("pause", "resume", "stop"))
    report = commands.add_parser("report", help="Validate a finished scan and export an offline single-axis review")
    report.add_argument("run")
    report.add_argument("--out")
    replay = commands.add_parser("scan-replay", help="Scan orchestration using recorded CSV, NOT new measurement")
    replay.add_argument("csv", nargs="+")
    replay.add_argument("--config", required=True)
    replay.add_argument("--out")
    replay.add_argument("--report", action="store_true")
    replay.add_argument("--manual", action="store_true")
    replay.add_argument("--pose", default=str(ROOT / "runs/latest_pose.json"))
    exchange = commands.add_parser("scan-exchange", help="MANUAL movement + external fresh-capture adapter; no bundled hardware driver")
    exchange.add_argument("--config", required=True)
    exchange.add_argument("--exchange", required=True)
    exchange.add_argument("--pose", default=str(ROOT / "runs/latest_pose.json"))
    exchange.add_argument("--out")
    exchange.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if args.command == "targets":
        generate_targets(args.out, args.marker_mm, args.square_mm)
        print(f"Print at 100% / actual size and physically measure black marker width: {args.marker_mm:g} mm. Output: {args.out}")
    elif args.command == "calibrate":
        result = calibrate_images(sorted(glob.glob(args.images)), args.columns, args.rows, args.square_mm, args.out)
        print(f"CAMERA_CALIBRATED: {len(result['accepted_images'])} views; RMS={result['rms_px']:.3f}px; {args.out}")
    elif args.command == "vision":
        run_vision(args)
    elif args.command == "workpiece":
        from .workpiece_app import run_workpiece

        run_workpiece(args)
    elif args.command == "workpiece-selftest":
        from .visual_selftest import run_visual_selftest

        output = output_directory(args.out, "visual_selftest")
        report = run_visual_selftest(output)
        print(json.dumps({"result": report["result"], "source": report["source"],
                          "report": str(output.resolve() / "visual_selftest.json"), "hardware_control": False}, ensure_ascii=False))
    elif args.command == "map-demo":
        from .mapping import demo_session, render_dashboard

        session = demo_session(output_directory(args.out, "map_demo"), complete=False)
        report_path = session.root / "report.html"
        report_path.write_text(render_dashboard(session.snapshot()), encoding="utf-8")
        print(json.dumps({"source": "DEMO", "hardware_control": False, "report": str(report_path),
                          "map_csv": str(session.root / "map.csv"), "points": len(session.points)}, ensure_ascii=False))
    elif args.command == "manual-map":
        from .mapping_app import run_manual_map

        args.out = output_directory(args.out, "manual_map")
        run_manual_map(args)
    elif args.command == "echo":
        print(json.dumps(analyze(load_csv(args.csv), EchoConfig(**settings(args.config).get("echo", {}))), indent=2, ensure_ascii=False))
    elif args.command == "scan-plan":
        data = settings(args.config)
        result = ScanPlan(**data.get("scan", {})).preview()
        result["echo"] = asdict(EchoConfig(**data.get("echo", {})))
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    elif args.command == "scan-status":
        print(json.dumps(scan_status(args.run), indent=2, ensure_ascii=False, allow_nan=False))
    elif args.command == "scan-control":
        print(json.dumps(request_control(args.run, args.action), indent=2, ensure_ascii=False))
    elif args.command == "report":
        from .report import build_report

        print(f"REPORT_OUTPUT={build_report(args.run, args.out)}")
    else:
        data = settings(getattr(args, "config", None))
        plan = ScanPlan(**data.get("scan", {}))
        echo_config = EchoConfig(**data.get("echo", {}))
        if args.command == "demo":
            echo_config = replace(echo_config, calibrated=True, calibration_id="DEMO_ONLY_NOT_FOR_MEASUREMENT")
            stage, locator = SimulatedStage(), demo_locator
            capture = ReplayCapture(synthetic_waveform(position) for position in plan.positions())
        elif args.command == "scan-replay":
            positions = list(plan.positions())
            if len(args.csv) not in (1, len(positions)):
                raise ValueError("Supply one replay CSV for all points, or exactly one CSV per scan point")
            files = args.csv * len(positions) if len(args.csv) == 1 else args.csv
            capture = ReplayCapture(load_csv(path) for path in files)
            stage = ManualStage() if args.manual else SimulatedStage()
            locator = PoseFile(args.pose) if args.manual else demo_locator
        else:
            stage, locator = ManualStage(), PoseFile(args.pose)
            capture = ExchangeCapture(args.exchange)
        destination = output_directory(args.out, args.command)
        result = Scanner(plan, echo_config, stage, locator, capture, destination).run()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"SESSION_OUTPUT={destination.resolve()}")
        if args.report:
            from .report import build_report

            print(f"REPORT_OUTPUT={build_report(destination)}")
        if args.command == "demo" and args.show_ui and result["point_count"]:
            show_lvgl(destination / "summary.csv")
        if result["status"] != "COMPLETE":
            return 3
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Stopped by operator; no motor/HV outputs are implemented.", file=sys.stderr)
        raise SystemExit(130)
    except (ValueError, OSError, TimeoutError, StopIteration) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
