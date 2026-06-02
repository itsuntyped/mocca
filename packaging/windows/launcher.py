"""Windows launcher for the frozen (PyInstaller) build of Mocca.

It starts the local web server, opens the default browser, and shows a small
system-tray icon (Open / Quit). Data is kept in a per-user folder so the app
works even when installed under Program Files.

This file is only used by the packaged build. During development, run Mocca the
normal way: `python scripts/run.py`.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8000  # Reassigned in main() to a free port.


def _data_dir() -> Path:
    # Per-user, writable location for config / db / models / logs.
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "Mocca"


# Point Mocca at the per-user data dir BEFORE importing the app: src/paths.py
# reads MOCCA_DATA_DIR at import time.
os.environ.setdefault("MOCCA_DATA_DIR", str(_data_dir()))

# When frozen, make the bundled `src` package importable from the archive root.
if getattr(sys, "frozen", False):
    sys.path.insert(0, sys._MEIPASS)


def _free_port(preferred: int = 8000) -> int:
    # Use the preferred port if it's free, otherwise let the OS choose one.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _wait_then_open(url: str) -> None:
    # Poll the port until the server accepts connections, then open the browser.
    for _ in range(120):
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                break
        except OSError:
            time.sleep(0.25)
    webbrowser.open(url)


def _tray_image():
    # Use the app logo for the tray icon; fall back to a simple drawn icon.
    try:
        from PIL import Image
        from src.paths import STATIC_DIR
        logo = STATIC_DIR / "images" / "mocca.png"
        if logo.exists():
            return Image.open(logo)
    except Exception:
        pass
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (64, 64), (29, 29, 32))
        ImageDraw.Draw(img).ellipse((12, 12, 52, 52), fill=(232, 179, 65))
        return img
    except Exception:
        return None


def main() -> None:
    global PORT
    PORT = _free_port()
    url = f"http://{HOST}:{PORT}"

    import uvicorn
    from src.server import app

    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_config=None))

    # The server runs off the main thread so the tray icon can own the main loop.
    threading.Thread(target=server.run, daemon=True).start()
    threading.Thread(target=_wait_then_open, args=(url,), daemon=True).start()

    # System tray icon with Open / Quit. If pystray isn't available, just keep
    # running until the server stops.
    try:
        import pystray
        def on_open(icon, item):
            webbrowser.open(url)
        def on_quit(icon, item):
            server.should_exit = True
            icon.stop()
        icon = pystray.Icon(
            "Mocca", _tray_image(), "Mocca",
            menu=pystray.Menu(
                pystray.MenuItem("Open Mocca", on_open, default=True),
                pystray.MenuItem("Quit", on_quit),
            ),
        )
        icon.run()
    except Exception:
        try:
            while not server.should_exit:
                time.sleep(0.5)
        except KeyboardInterrupt:
            server.should_exit = True


if __name__ == "__main__":
    main()
