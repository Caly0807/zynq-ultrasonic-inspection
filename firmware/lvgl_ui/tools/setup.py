from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile
import argparse
import hashlib

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
LVGL_SHA256 = "1d2f63e1e66c0d24e82b14e42656b88f0ae12ea5efdb5962341f3dcade753ef5"
FONT_SHA256 = "faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9"


def download(url, target, expected_hash):
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        print(f"Downloading {url}", flush=True)
        with urlopen(url, timeout=120) as response, target.open("wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
    actual_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        raise RuntimeError(f"Checksum mismatch: {target}; delete this incomplete dependency file and retry")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-font-source", action="store_true")
    args = parser.parse_args()
    archive_path = VENDOR / "lvgl-v9.4.0.zip"
    download("https://codeload.github.com/lvgl/lvgl/zip/refs/tags/v9.4.0", archive_path, LVGL_SHA256)
    source_root = (VENDOR / "lvgl").resolve()
    with ZipFile(archive_path) as archive:
        for entry in archive.infolist():
            relative = Path(*Path(entry.filename).parts[1:])
            if entry.is_dir() or not relative.parts:
                continue
            if relative.parts[0] != "src" and relative.as_posix() not in ("lvgl.h", "lv_version.h", "lvgl_private.h", "LICENCE.txt", "LICENSE.txt", "lv_conf_template.h"):
                continue
            target = (source_root / relative).resolve()
            if not target.is_relative_to(source_root):
                raise RuntimeError("Unsafe archive entry")
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(archive.read(entry))
    if args.with_font_source:
        download("https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf", VENDOR / "noto/NotoSansSC-Regular.otf", FONT_SHA256)
    print("Pinned LVGL 9.4.0 runtime is ready. Generated CJK fonts are already included in assets/.")


if __name__ == "__main__":
    main()