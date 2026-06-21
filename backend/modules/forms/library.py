"""Helpers for locating Winlink Standard Forms templates on disk."""
from __future__ import annotations

import threading
from pathlib import Path

from backend.config import settings


_cache_lock = threading.Lock()
_template_index: dict[str, Path] | None = None  # lowercased basename → path


def forms_library_dir() -> Path:
    """Resolve the on-disk directory where the forms library is unpacked."""
    return Path(settings.state_dir) / "forms"


def clear_template_cache() -> None:
    """Drop the in-process index of available templates."""
    global _template_index
    with _cache_lock:
        _template_index = None


def _build_index() -> dict[str, Path]:
    base = forms_library_dir()
    index: dict[str, Path] = {}
    if not base.is_dir():
        return index
    for path in base.rglob("*"):
        if path.is_file():
            index.setdefault(path.name.lower(), path)
    return index


def find_template(filename: str) -> Path | None:
    """Return the on-disk path for a template basename, case-insensitive."""
    global _template_index
    if not filename:
        return None
    with _cache_lock:
        if _template_index is None:
            _template_index = _build_index()
        return _template_index.get(filename.lower())
