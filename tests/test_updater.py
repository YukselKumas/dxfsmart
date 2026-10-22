import updater


def test_version_compare():
    assert updater.is_newer("v1.0.1", "1.0.0")
    assert updater.is_newer("1.10.0", "1.9.9")
    assert not updater.is_newer("v1.0.0", "1.0.0")
    assert not updater.is_newer("0.9.1", "1.0.0")


def test_check_never_raises_offline(monkeypatch):
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(updater.urllib.request, "urlopen", boom)
    updater._cache.update(at=0.0, data=None)
    r = updater.check()
    assert r["available"] is False and r["canInstall"] is False and "error" in r
    updater._cache.update(at=0.0, data=None)


def test_install_refuses_outside_exe():
    import pytest
    with pytest.raises(RuntimeError):
        updater.install(lambda: None)


def test_swap_replaces_exe_and_keeps_old(tmp_path):
    exe, new, old = tmp_path / "DXFsmart.exe", tmp_path / "DXFsmart.new.exe", tmp_path / "DXFsmart.old.exe"
    exe.write_text("v1"); new.write_text("v2"); old.write_text("stale")
    updater.swap(str(exe), str(new), str(old))
    assert exe.read_text() == "v2" and old.read_text() == "v1" and not new.exists()


def test_swap_rolls_back_when_download_missing(tmp_path):
    import pytest
    exe, new, old = tmp_path / "DXFsmart.exe", tmp_path / "DXFsmart.new.exe", tmp_path / "DXFsmart.old.exe"
    exe.write_text("v1")
    with pytest.raises(RuntimeError):
        updater.swap(str(exe), str(new), str(old))
    assert exe.read_text() == "v1"


def test_install_uses_no_cmd_helper():
    import inspect
    src = inspect.getsource(updater.install)
    assert "\"cmd\"" not in src and ".cmd" not in src and "goto" not in src
