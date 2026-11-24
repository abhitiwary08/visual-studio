"""Start the dashboard:  python run.py   (then open http://127.0.0.1:8765)"""
import argparse
import threading
import webbrowser

import uvicorn

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"Video studio running at {url}  (Ctrl+C to stop)")
    # Local only: never bind to 0.0.0.0, the dashboard can upload to your YouTube channel.
    uvicorn.run("app.server:app", host="127.0.0.1", port=args.port, log_level="warning")
