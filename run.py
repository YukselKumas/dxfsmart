"""DXFsmart launcher.

Works both as `python run.py` and as the packaged DXFsmart.exe (PyInstaller).
Starts the local server on the first free port 8000-8010 and opens the
browser when the server answers. Everything runs on this computer only.
"""
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from urllib.request import urlopen

VERSION = "0.9.1"


def base_dir():
    # PyInstaller unpacks bundled files to sys._MEIPASS
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def free_port():
    for candidate in range(8000, 8011):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return candidate
    return None


def main():
    root = base_dir()
    sys.path.insert(0, root)
    os.chdir(root)
    port = free_port()
    if port is None:
        print("8000-8010 portlari kullanimda. Acik DXFsmart pencerelerini kapatip tekrar deneyin.")
        input("Kapatmak icin Enter'a basin...")
        return
    address = f"http://127.0.0.1:{port}"
    print(f"DXFsmart {VERSION} aciliyor: {address}")
    print("Tarayici kendiliginden acilacak. Programi kapatmak icin bu pencereyi kapatin.")
    print("(Bu pencerede Ctrl+C programi durdurur.)", flush=True)

    def open_when_ready():
        for _ in range(200):
            try:
                with urlopen(address + "/api/health", timeout=1) as response:
                    if json.load(response).get("status") == "ok":
                        webbrowser.open(address)
                        print("Hazir.", flush=True)
                        return
            except Exception:
                pass
            time.sleep(0.3)
    threading.Thread(target=open_when_ready, daemon=True).start()

    import uvicorn
    from app import app  # imported directly so the frozen .exe needs no import strings
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                            loop="asyncio", http="h11", ws="none", lifespan="on")
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
