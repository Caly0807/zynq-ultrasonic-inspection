from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build/desktop"


def main():
    with tempfile.TemporaryDirectory(prefix="ut_map_smoke_", dir=BUILD) as directory:
        root = Path(directory)
        path = root / "map.csv"
        for source in ("DEMO", "RECORDED", "LIVE"):
            path.write_text(f"UTMAP,1,2,1,10,5,30,5,{source},MANUAL_UNVERIFIED\n"
                            "CELL,0,0,SUSPECT,1,12\nCELL,1,0,UNSCANNED,0,\n", encoding="ascii")
            screenshot = BUILD / f"preview_map_{source.lower()}_test.bmp"
            result = subprocess.run([str(BUILD / "ut_lvgl.exe"), "--map", str(path), "--screenshot", str(screenshot)],
                                    capture_output=True, text=True, encoding="utf-8", timeout=20)
            assert result.returncode == 0, result.stdout + result.stderr
            assert f"Map: source={source} / sampled=1 / suspect=1 / depth=1 / points=1" in result.stdout
            assert screenshot.stat().st_size > 800 * 480 * 4
            volume = BUILD / f"preview_map_{source.lower()}_3d_test.bmp"
            result = subprocess.run([str(BUILD / "ut_lvgl.exe"), "--map", str(path), "--map-3d", "--screenshot", str(volume)],
                                    capture_output=True, text=True, encoding="utf-8", timeout=20)
            assert result.returncode == 0 and volume.stat().st_size > 800 * 480 * 4, result.stdout + result.stderr
        for extra in (["--demo"], ["--watch"], ["--scan-review"], ["--point", "0"], ["--csv", "unused.csv"]):
            result = subprocess.run([str(BUILD / "ut_lvgl.exe"), "--map", str(path), *extra], capture_output=True, timeout=10)
            assert result.returncode == 2, extra
        path.write_text("UTMAP,1,2,1,10,5,30,5,DEMO,MANUAL_UNVERIFIED\nCELL,0,0,SUSPECT,1,12\n", encoding="ascii")
        result = subprocess.run([str(BUILD / "ut_lvgl.exe"), "--map", str(path), "--screenshot", str(root / "invalid.bmp")],
                                capture_output=True, timeout=10)
        assert result.returncode == 2 and not (root / "invalid.bmp").exists()
        for arguments in (["--map-3d"], ["--board-demo", "--demo"], ["--board-demo", "--settings", "--touch-diagnostics"], ["--reflector"]):
            result = subprocess.run([str(BUILD / "ut_lvgl.exe"), *arguments], capture_output=True, timeout=10)
            assert result.returncode == 2, arguments
        for arguments, name in (([], "2d"), (["--map-3d"], "3d"), (["--touch-diagnostics"], "touch")):
            output = root / (name + ".bmp")
            result = subprocess.run([str(BUILD / "ut_lvgl.exe"), "--board-demo", *arguments, "--screenshot", str(output)],
                                    capture_output=True, text=True, encoding="utf-8", timeout=20)
            assert result.returncode == 0 and "sampled=208 / suspect=23 / depth=23" in result.stdout and output.stat().st_size > 800 * 480 * 4, (arguments, result.returncode, result.stdout, result.stderr)
        output = root / "missing_directory" / "failed.bmp"
        result = subprocess.run([str(BUILD / "ut_lvgl.exe"), "--board-demo", "--screenshot", str(output)],
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        assert result.returncode == 1 and "Screenshot FAILED:" in result.stdout and result.stderr and not output.exists()
    print("PASS: LVGL 2D/3D snapshots, board DEMO, touch panel, source labels, bounded rendering, incomplete-file rejection and CLI isolation; no hardware")


if __name__ == "__main__":
    main()
