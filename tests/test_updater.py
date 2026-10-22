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
