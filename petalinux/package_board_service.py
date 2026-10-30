import argparse
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_MODULES = ("__init__", "records", "echo", "scan", "mapping", "mapping_app", "network", "device_app", "board_capture", "board_health", "basic_vision")
VISION_MODULES = ("camera", "vision", "workpiece", "workpiece_config", "workpiece_app", "device_vision")


def build_bundle(destination, with_vision=False):
    source_root = ROOT / "firmware/inspection"
    relative_paths = [f"utinspect/{name}.py" for name in CORE_MODULES + (VISION_MODULES if with_vision else ())]
    relative_paths += ["web/inspection.html", "web/camera_basic.html"] + (["web/workpiece.html"] if with_vision else [])
    content = {relative: (source_root / relative).read_bytes() for relative in sorted(relative_paths)}
    content["LICENSE"] = (ROOT / "LICENSE").read_bytes()
    manifest = {
        "schema": "ut-board-bundle/1", "with_vision": with_vision,
        "requires_python": ">=3.10", "core_dependencies": "stdlib only",
        "optional_vision_dependencies": ["numpy", "opencv with ArUco"] if with_vision else [],
        "default_source": "RECORDED", "hardware_control": False,
        "board_execution_verified": False,
        "diagnostic_dma_transport": "EXPLICIT_TEST_ONLY_NO_TRANSMIT_ABI",
        "basic_vision": "STDLIB_FOUR_CORNER_AND_RED_MARKER_WITH_NATIVE_YUYV_HELPER",
        "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in content.items()},
    }
    content["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    with temporary.open("wb") as output, gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for name, data in sorted(content.items()):
                information = tarfile.TarInfo(name)
                information.size = len(data)
                information.mode = 0o644
                archive.addfile(information, io.BytesIO(data))
    temporary.replace(destination)
    return {"archive": str(destination.resolve()), "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), **manifest}


def main():
    parser = argparse.ArgumentParser(description="Deterministic board-service bundle; excludes PC environments, data and credentials")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--with-vision", action="store_true")
    arguments = parser.parse_args()
    report = build_bundle(arguments.out, arguments.with_vision)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
