import argparse
import os
import webbrowser
from pathlib import Path
from threading import Timer

import uvicorn


def load_config():
    path = Path(__file__).with_name(".env")
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip().startswith("TRAVEL_"):
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main():
    parser = argparse.ArgumentParser(description="Start Adaptive Travel Agent locally")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    load_config()
    if not args.no_browser:
        timer = Timer(1, webbrowser.open, args=(f"http://127.0.0.1:{args.port}",))
        timer.daemon = True
        timer.start()
    uvicorn.run("travel.main:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
