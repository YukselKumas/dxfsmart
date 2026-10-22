"""Free self-update through public GitHub Releases (no account, no server).

* check(): asks the GitHub API for the latest release of the public repo.
* install(): only in the packaged Windows exe. Downloads the new DXFsmart.exe
  next to the running one and swaps the files *without any helper script*:
  Windows allows renaming a running exe, so the running one becomes
  DXFsmart.old.exe, the download takes its name, the new version is started
  and this one exits. The next start removes DXFsmart.old.exe.
  (v1.0 used a cmd helper that could keep flashing console windows.)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from version import REPO, VERSION

API = f"https://api.github.com/repos/{REPO}/releases/latest"
_cache = {"at": 0.0, "data": None}
_lock = threading.Lock()


def _ver(text: str):
    parts = []
    for p in text.strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts + [0] * (3 - len(parts)))


def is_newer(latest: str, current: str = VERSION) -> bool:
    return _ver(latest) > _ver(current)


def frozen_exe():
    return sys.executable if getattr(sys, "frozen", False) and sys.platform == "win32" else None


def check(timeout: float = 4.0) -> dict:
    with _lock:
        if _cache["data"] is not None and time.monotonic() - _cache["at"] < 600:
            return _cache["data"]
    result = {"current": VERSION, "available": False, "latest": None, "url": f"https://github.com/{REPO}/releases/latest",
              "canInstall": False}
    try:
        req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "DXFsmart/" + VERSION})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        tag = data.get("tag_name") or ""
        asset = next((a for a in data.get("assets", []) if a.get("name", "").lower() == "dxfsmart.exe"), None)
        result.update(latest=tag.lstrip("vV"), url=data.get("html_url") or result["url"],
                      available=bool(tag) and is_newer(tag),
                      assetUrl=asset.get("browser_download_url") if asset else None,
                      assetSize=asset.get("size") if asset else None,
                      canInstall=bool(asset) and frozen_exe() is not None)
    except Exception as exc:  # offline, rate limited, proxy ... never block the app
        result["error"] = str(exc)[:120]
    with _lock:
        _cache.update(at=time.monotonic(), data=result)
    return result


def install(on_exit) -> None:
    exe = frozen_exe()
    info = check()
    if not exe or not info.get("available") or not info.get("assetUrl"):
        raise RuntimeError("Güncelleme bu kurulumda otomatik yapılamıyor.")
    target_dir = os.path.dirname(exe)
    new_path = os.path.join(target_dir, "DXFsmart.new.exe")
    req = urllib.request.Request(info["assetUrl"], headers={"User-Agent": "DXFsmart/" + VERSION})
    with urllib.request.urlopen(req, timeout=60) as resp, open(new_path, "wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    size = os.path.getsize(new_path)
    if info.get("assetSize") and size != info["assetSize"]:
        os.remove(new_path)
        raise RuntimeError("İndirilen dosya eksik; tekrar deneyin.")
    old_path = os.path.join(target_dir, "DXFsmart.old.exe")
    swap(exe, new_path, old_path)
    launch(exe)
    threading.Timer(1.0, on_exit).start()


def swap(exe, new_path, old_path):
    """Running exe -> .old, download -> exe name. Rolls back on failure."""
    try:
        if os.path.exists(old_path):
            os.remove(old_path)
    except OSError:
        old_path = old_path[:-4] + f".{int(time.time())}.exe"
    os.replace(exe, old_path)
    try:
        os.replace(new_path, exe)
    except OSError:
        os.replace(old_path, exe)
        raise RuntimeError("Yeni sürüm yerine konamadı (dosya kilitli olabilir). Programı kapatıp Releases sayfasından indirin.")


def launch(exe):
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([exe], cwd=os.path.dirname(exe), creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cleanup_leftovers():
    """Called at start-up: remove the previous version and stop a v1.0 helper loop."""
    exe = frozen_exe()
    if not exe:
        return
    folder = os.path.dirname(exe)
    # v1.0's cmd helper re-reads its script every loop; deleting it ends the loop.
    for path in [os.path.join(tempfile.gettempdir(), "dxfsmart_update.cmd"),
                 os.path.join(folder, "DXFsmart.new.exe")]:
        try:
            os.remove(path)
        except OSError:
            pass

    def remove_old():
        for _ in range(30):
            left = [f for f in os.listdir(folder) if f.lower().startswith("dxfsmart.old")]
            for f in left:
                try:
                    os.remove(os.path.join(folder, f))
                except OSError:
                    pass
            if not left:
                return
            time.sleep(2)
    threading.Thread(target=remove_old, daemon=True).start()
