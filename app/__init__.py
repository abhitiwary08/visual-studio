"""Video studio pipeline.

On Windows, Pillow's wheels include Raqm (needed to shape Hindi conjuncts and matras) but load the
FriBiDi library at runtime. If vendor/ holds libfribidi-0.dll (see tools/get_fribidi.py), make it
findable before Pillow is imported anywhere.
"""
import os
import sys
from pathlib import Path

_VENDOR = Path(__file__).resolve().parent.parent / "vendor"
if sys.platform == "win32" and (_VENDOR / "libfribidi-0.dll").exists():
    os.environ["PATH"] = str(_VENDOR) + os.pathsep + os.environ.get("PATH", "")
    try:
        os.add_dll_directory(str(_VENDOR))
    except (AttributeError, OSError):
        pass
