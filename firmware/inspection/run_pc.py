import os
import site
import subprocess
import sys
from pathlib import Path


root = Path(__file__).resolve().parent
if os.name == "nt" and sys.prefix != sys.base_prefix:
    raise SystemExit(subprocess.call([sys._base_executable, str(Path(__file__).resolve()), *sys.argv[1:]]))
packages = root / ".venv" / ("Lib/site-packages" if os.name == "nt" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
if not packages.is_dir():
    raise SystemExit("Run setup_pc.ps1 first; the isolated dependencies are missing")
site.addsitedir(str(packages))
sys.path.insert(0, str(packages))

from utinspect.__main__ import main


try:
    raise SystemExit(main())
except KeyboardInterrupt:
    print("Stopped by operator. No motor or HV outputs.", file=sys.stderr)
    raise SystemExit(130)
except (ValueError, OSError, TimeoutError, StopIteration) as error:
    print("ERROR: " + str(error), file=sys.stderr)
    raise SystemExit(2)
