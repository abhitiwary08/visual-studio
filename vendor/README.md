# vendor

`libfribidi-0.dll`: FriBiDi 1.0.17 (MSYS2 UCRT64 build, `mingw-w64-ucrt-x86_64-fribidi-1.0.17-1`), licensed
LGPL-2.1, source at https://github.com/fribidi/fribidi. Windows only: it lets Pillow's bundled Raqm shape
Hindi text. `app/__init__.py` adds this folder to the DLL search path. Re-download with
`python tools/get_fribidi.py`.
