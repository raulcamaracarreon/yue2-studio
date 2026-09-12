#!/usr/bin/env python3
"""Small environment diagnostic for YuE2 Studio."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

repo = Path(__file__).resolve().parents[1]

def env_path(name, default):
    return Path(os.path.expanduser(os.environ.get(name, str(default))))

home = Path.home()
base = env_path("YUE2_STUDIO_HOME", home / "YuE2-Studio")
sheet_dir = env_path("SHEETSAGE2_DIR", base / "models" / "SheetSage2")
sheet_py = env_path("SHEETSAGE2_PYTHON", home / "venvs" / "sheetsage2" / "bin" / "python")
work = env_path("SHEETSAGE2_WORK_ROOT", home / ".cache" / "yue2-studio" / "sheetsage2")

print("YuE2 Studio doctor")
print("repo:", repo)
print("python:", sys.executable)
print("base:", base)
print("SheetSage2 dir:", sheet_dir, "OK" if (sheet_dir / "infer.py").exists() else "MISSING infer.py")
print("SheetSage2 python:", sheet_py, "OK" if sheet_py.exists() else "MISSING")
print("SheetSage2 work:", work)
print("ffmpeg:", shutil.which("ffmpeg") or "MISSING")
try:
    import torch
    print("torch:", torch.__version__)
    print("cuda:", torch.cuda.is_available())
    if torch.cuda.is_available():
        print("gpu:", torch.cuda.get_device_name(0))
except Exception as exc:
    print("torch check failed:", exc)

if sheet_py.exists():
    try:
        out = subprocess.check_output([str(sheet_py), "-c", "import torch; print(torch.__version__, torch.cuda.is_available())"], text=True, stderr=subprocess.STDOUT, timeout=20)
        print("SheetSage2 torch:", out.strip())
    except Exception as exc:
        print("SheetSage2 env check failed:", exc)
