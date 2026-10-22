"""DXFsmart desktop launcher.

* Packaged DXFsmart.exe / `python run.py`: opens its own application window
  (pywebview -> Microsoft Edge WebView2, built into Windows 10/11). No browser,
  no console. Closing the window quits the program.
* If no window engine is available it falls back to the default browser.
* DXFSMART_HEADLESS=1 runs only the local server (used by the CI smoke test).
Everything runs on this computer (127.0.0.1); DXF files are never uploaded.
"""
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from urllib.request import urlopen

SPLASH = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{height:100%;margin:0;background:#070a17;color:#eef1ff;font:15px "Segoe UI",system-ui,sans-serif;overflow:hidden}
body{display:grid;place-items:center;background:
 radial-gradient(600px 360px at 30% 20%,rgba(124,58,237,.35),transparent 60%),
 radial-gradient(600px 360px at 80% 90%,rgba(236,72,153,.25),transparent 60%),#070a17}
.box{display:grid;gap:18px;justify-items:center}
h1{margin:0;font:700 40px "Bahnschrift","Segoe UI",sans-serif;background:linear-gradient(100deg,#fff,#ffd6a3 45%,#ffb3d5 75%,#cdb7ff);-webkit-background-clip:text;color:transparent}
.bar{width:260px;height:6px;border-radius:9px;background:rgba(255,255,255,.08);overflow:hidden}
.bar i{display:block;height:100%;width:40%;border-radius:9px;background:linear-gradient(90deg,#ffb03b,#ec4899,#7c3aed);animation:m 1.2s ease-in-out infinite}
@keyframes m{0%{transform:translateX(-110%)}100%{transform:translateX(260%)}}
p{margin:0;color:#8189ad;font-size:13px}
</style></head><body><div class="box"><h1>DXFsmart</h1><div class="bar"><i></i></div><p>Hazırlanıyor…</p></div></body></html>"""


def base_dir():
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


def wait_ready(address, seconds=90):
    end = time.time() + seconds
    while time.time() < end:
        try:
            with urlopen(address + "/api/health", timeout=1) as response:
                if json.load(response).get("status") == "ok":
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def start_server(port):
    import uvicorn
    from app import app  # imported directly: the frozen exe needs no import strings

    class ThreadServer(uvicorn.Server):
        def install_signal_handlers(self):  # not allowed outside the main thread
            pass

        def capture_signals(self):
            import contextlib
            return contextlib.nullcontext()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                            loop="asyncio", http="h11", ws="none", lifespan="on")
    server = ThreadServer(config)
    threading.Thread(target=server.run, daemon=True).start()
    return server


def redirect_output():
    """Windowed exe has no console (sys.stdout is None), which breaks logging.
    Send all output to %LOCALAPPDATA%/DXFsmart/log.txt instead."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    folder = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "DXFsmart")
    try:
        os.makedirs(folder, exist_ok=True)
        log = open(os.path.join(folder, "log.txt"), "w", encoding="utf-8", buffering=1)
    except Exception:
        log = open(os.devnull, "w")
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def main():
    redirect_output()
    root = base_dir()
    sys.path.insert(0, root)
    os.chdir(root)
    from version import VERSION
    port = free_port()
    if port is None:
        print("8000-8010 portlari kullanimda. Acik DXFsmart pencerelerini kapatip tekrar deneyin.")
        return
    address = f"http://127.0.0.1:{port}"
    start_server(port)

    if os.environ.get("DXFSMART_HEADLESS") == "1":
        print(f"DXFsmart {VERSION} (sunucu) {address}", flush=True)
        while True:
            time.sleep(3600)

    try:
        import webview
    except Exception:
        webview = None

    if webview is not None:
        try:
            webview.settings["ALLOW_DOWNLOADS"] = True
        except Exception:
            pass
        window = webview.create_window(f"DXFsmart {VERSION}", html=SPLASH, width=1480, height=920,
                                       min_size=(1100, 700), background_color="#070a17")

        def load_app():
            if wait_ready(address):
                window.load_url(address)
            else:
                window.load_html("<body style='background:#070a17;color:#fff;font:16px Segoe UI;padding:40px'>"
                                 "Sunucu başlatılamadı. Programı kapatıp tekrar açın.</body>")
        try:
            webview.start(load_app, private_mode=False)
            os._exit(0)  # window closed: stop the server too
        except Exception:
            pass  # no WebView2 runtime -> fall back to the browser below

    if wait_ready(address):
        webbrowser.open(address)
    print(f"DXFsmart {VERSION} tarayicida acildi: {address}. Kapatmak icin bu pencereyi kapatin.", flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
