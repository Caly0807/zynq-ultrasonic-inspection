from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
LVGL = ROOT / "vendor" / "lvgl"
BUILD = ROOT / "build" / "desktop"


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(" ".join(map(str, command)) + "\n" + result.stdout + result.stderr)
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 4))
    arguments = parser.parse_args()
    compiler = shutil.which("gcc")
    archiver = shutil.which("ar")
    if not compiler or not archiver:
        raise SystemExit("MinGW gcc and ar must be on PATH.")
    if not (LVGL / "lvgl.h").exists():
        raise SystemExit("Run python tools/setup.py to download pinned LVGL 9.4.0.")
    object_root = BUILD / "obj"
    object_root.mkdir(parents=True, exist_ok=True)
    include_flags = ["-DLV_CONF_INCLUDE_SIMPLE", "-DLV_LVGL_H_INCLUDE_SIMPLE", "-I" + str(ROOT), "-I" + str(LVGL), "-I" + str(ROOT / "src")]
    flags = ["-std=c11", "-O2", "-ffunction-sections", "-fdata-sections", "-finput-charset=UTF-8"] + include_flags
    config_signature = hashlib.sha256((ROOT / "lv_conf.h").read_bytes() + repr(flags).encode() + run([compiler, "-dumpversion"]).encode()).hexdigest()
    signature_file = BUILD / "config.sha256"
    force = not signature_file.exists() or signature_file.read_text() != config_signature
    sources = sorted((LVGL / "src").rglob("*.c"))
    objects = [object_root / (source.relative_to(LVGL).as_posix().replace("/", "_") + ".o") for source in sources]
    pending = [(source, target) for source, target in zip(sources, objects) if force or not target.exists() or source.stat().st_mtime > target.stat().st_mtime]
    print(f"LVGL 9.4.0: {len(sources)} sources, {len(pending)} to compile, {arguments.jobs} workers", flush=True)
    started = time.monotonic()
    def compile_source(item):
        source, target = item
        run([compiler, *flags, "-c", str(source), "-o", str(target)])
    with ThreadPoolExecutor(max_workers=arguments.jobs) as executor:
        futures = [executor.submit(compile_source, item) for item in pending]
        for completed, future in enumerate(as_completed(futures), 1):
            future.result()
            if completed % 80 == 0:
                print(f"Compiled {completed}/{len(pending)}", flush=True)
    archive = BUILD / "liblvgl.a"
    response_file = BUILD / "archive.rsp"
    response_file.write_text("\n".join('"' + str(target).replace("\\", "/") + '"' for target in objects), encoding="utf-8")
    if pending or not archive.exists():
        run([archiver, "rcs", str(archive), "@" + str(response_file)])
    application_sources = [ROOT / "ports" / "desktop_win32.c", ROOT / "src" / "ut_model.c", ROOT / "src" / "ut_scan.c", ROOT / "src" / "ut_map.c", ROOT / "src" / "ut_board_demo.c", ROOT / "src" / "ut_ui.c", ROOT / "assets" / "ut_font.c"]
    executable = BUILD / "ut_lvgl.exe"
    run([compiler, *flags, "-Wall", "-Wextra", *map(str, application_sources), str(archive), "-Wl,--gc-sections", "-static-libgcc", "-mwindows", "-lgdi32", "-lcomdlg32", "-lwinmm", "-lm", "-o", str(executable)])
    signature_file.write_text(config_signature)
    (BUILD / "build_info.json").write_text(json.dumps({"lvgl": "9.4.0", "compiler": compiler, "source_count": len(sources), "elapsed_seconds": round(time.monotonic() - started, 2)}, indent=2))
    print(f"Built {executable} in {time.monotonic() - started:.1f}s", flush=True)


if __name__ == "__main__":
    main()
