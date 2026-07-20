"""Tree the fetched Standard Forms library into a browsable catalog of
COMPOSABLE forms (a .txt template that references a fillable input HTML)."""
import re
import threading
from pathlib import Path

from backend.modules.forms.library import forms_library_dir  # re-exported for tests to patch

_cache_lock = threading.Lock()
_cache: tuple[str, dict] | None = None  # (library_version, tree)

# "Form: InputForm.html[,Viewer.html]" — the first filename is the fillable input form.
_FORM_LINE_RE = re.compile(r"^\s*Form\s*:\s*(?P<input>[^,\r\n]+)", re.IGNORECASE | re.MULTILINE)


def clear_catalog_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def _input_form_for(template_path: Path) -> Path | None:
    """Parse the template's `Form:` line and resolve the input HTML beside it."""
    try:
        text = template_path.read_text(errors="replace")
    except OSError:
        return None
    m = _FORM_LINE_RE.search(text)
    if not m:
        return None
    input_name = m.group("input").strip()
    candidate = template_path.parent / input_name
    if candidate.is_file():
        return candidate
    return None


def _walk(dir_path: Path, base: Path) -> dict:
    node = {"name": dir_path.name if dir_path != base else "", "folders": [], "forms": []}
    try:
        entries = sorted(dir_path.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return node
    for entry in entries:
        if entry.is_dir():
            sub = _walk(entry, base)
            if sub["folders"] or sub["forms"]:  # prune empty branches
                node["folders"].append(sub)
        elif entry.suffix.lower() == ".txt":
            input_form = _input_form_for(entry)
            if input_form is not None:
                node["forms"].append({
                    "name": entry.stem,
                    "template_path": str(entry.relative_to(base)),
                    "input_form_path": str(input_form.relative_to(base)),
                })
    return node


def build_catalog(version: str = "") -> dict:
    """Composable-forms tree, cached by library version. The caller (route)
    passes the version so catalog.py stays free of any DB dependency."""
    global _cache
    with _cache_lock:
        if _cache is not None and _cache[0] == version:
            return _cache[1]
    base = forms_library_dir()
    tree = _walk(base, base) if base.is_dir() else {"name": "", "folders": [], "forms": []}
    with _cache_lock:
        _cache = (version, tree)
    return tree
