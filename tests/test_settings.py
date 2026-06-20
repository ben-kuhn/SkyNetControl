import importlib
import pytest


def _reload_settings():
    """Re-import the settings module so a fresh Settings() reads current env."""
    import backend.config
    importlib.reload(backend.config)
    return backend.config.settings


def test_state_dir_defaults_to_cwd(monkeypatch):
    monkeypatch.delenv("SKYNET_STATE_DIR", raising=False)
    s = _reload_settings()
    assert s.state_dir == "."


def test_state_dir_reads_env(monkeypatch):
    monkeypatch.setenv("SKYNET_STATE_DIR", "/var/lib/skynetcontrol")
    s = _reload_settings()
    assert s.state_dir == "/var/lib/skynetcontrol"
