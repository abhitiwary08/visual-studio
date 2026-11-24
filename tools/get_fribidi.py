"""Windows only: download the FriBiDi DLL so Pillow can shape Hindi text (Raqm).

    python tools/get_fribidi.py

Fetches the MSYS2 UCRT64 build of FriBiDi (LGPL-2.1, https://github.com/fribidi/fribidi) and puts
libfribidi-0.dll in vendor/. The app adds vendor/ to the DLL search path at startup.
Needs the `zstandard` package to unpack the archive (pip install zstandard).
"""
from __future__ import annotations

import io
import sys
import tarfile
import urllib.request
from pathlib import Path

VERSION = "1.0.17-1"
URL = f"https://mirror.msys2.org/mingw/ucrt64/mingw-w64-ucrt-x86_64-fribidi-{VERSION}-any.pkg.tar.zst"
VENDOR = Path(__file__).resolve().parent.parent / "vendor"


def main() -> int:
    if sys.platform != "win32":
        print("Only needed on Windows. On Linux/macOS, Pillow finds the system FriBiDi.")
        return 0
    try:
        import zstandard
    except ImportError:
        print("Install zstandard first:  pip install zstandard")
        return 1
    print(f"Downloading {URL}")
    with urllib.request.urlopen(URL, timeout=60) as r:
        raw = r.read()
    data = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw)).read()
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        member = next((m for m in tar.getmembers() if m.name.endswith("/libfribidi-0.dll")), None)
        if member is None:
            print("libfribidi-0.dll not found in the package.")
            return 1
        VENDOR.mkdir(exist_ok=True)
        (VENDOR / "libfribidi-0.dll").write_bytes(tar.extractfile(member).read())
    print(f"Saved {VENDOR / 'libfribidi-0.dll'}. Restart the dashboard; Setup should now show text shaping as available.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
