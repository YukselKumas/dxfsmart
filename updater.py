"""Free self-update through public GitHub Releases (no account, no server).

* check(): asks the GitHub API for the latest release of the public repo.
* install(): only in the packaged Windows exe. Downloads the new DXFsmart.exe
  next to the running one, starts a tiny helper script that waits until this
  process exits, swaps the files and starts the new version.
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
    script = os.path.join(tempfile.gettempdir(), "dxfsmart_update.cmd")
    with open(script, "w", encoding="ascii", errors="ignore") as f:
        f.write("@echo off\r\n"
                ":wait\r\n"
                "timeout /t 1 /nobreak >nul\r\n"
                f'move /y "{new_path}" "{exe}" >nul 2>&1 || goto wait\r\n'
                f'start "" "{exe}"\r\n'
                'del "%~f0"\r\n')
    flags = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
    subprocess.Popen(["cmd", "/c", script], creationflags=flags, close_fds=True)
    threading.Timer(1.0, on_exit).start()
