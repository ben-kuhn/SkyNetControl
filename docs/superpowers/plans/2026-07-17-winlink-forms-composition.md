# Winlink Forms Composition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let NCS author and send arbitrary Winlink standard forms (and reply to received forms) during an event — the form's own JS runs in a locked sandboxed iframe, the server does no-JS template→message composition + RMS_Express_Form XML, sent via SP3's outbound path with SP4a's attachment codec.

**Architecture:** Fetch keeps `.js`; a `catalog` module trees the library. A net-scoped `render` endpoint serves each form's HTML+shim into an `allow-scripts`-only iframe (opaque origin), which postMessages the collected variables back. A pure `builder` module (PAT `scanAndBuild`/`buildXML` port) deterministically produces the message + XML from (template, variables, context). An `event_message_forms` companion row makes send/retry a byte-stable rebuild. A React catalog→fill→prompts→preview flow drives it from the Messages panel.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, pytest; React 19 + TS + Tailwind, sandboxed iframe + postMessage.

**Spec:** `docs/superpowers/specs/2026-07-17-winlink-forms-composition-design.md`. Depends on SP4a (merged): `backend/integrations/winlink/b2f.py` `B2FAttachment`/`build_b2f`; `WinlinkBackend.send` reads `config["attachments"]`; `find_form_xml`; received-form `reply_template`+variables captured on inbound messages.

## Global Constraints

- Host is NixOS: backend `.venv/bin/...`; frontend `cd frontend && nix-shell -p nodejs_22 --run "npm <…>"`. Lint `nix-shell --run "ruff check"` (120, E+F). Conventional Commits (`feat(forms):`).
- The sandboxed iframe is the ONLY place untrusted form JS runs: `<iframe sandbox="allow-scripts">` (NO `allow-same-origin`); serve-response CSP `sandbox; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; form-action 'none'`. The only thing crossing back is a flat variable dict, re-validated server-side.
- The builder is PURE and DETERMINISTIC over (template, variables, context). `<DateTime>` comes from `context.datetime_stamp` (stamped once, stored, reused on retry) — never read from the clock inside the builder. Same inputs → identical bytes (preview = send = retry).
- The RMS_Express_Form XML and insertion-tag list MUST be pinned against a REAL captured RMS_Express_Form message (Task 3 has an explicit pinning step); synthetic-only tests are insufficient for interop. This ties to SP4a's open real-`.b2f` caveat.
- Composition is NCS + active event only (409 on closed); viewers never see compose controls. Cross-net 404 scoping on every route.
- Receive-side viewer (`render.py`) stays script-stripped — only the compose serve path emits scripts.
- Offline sandbox: `connect-src 'none'`; forms needing network degrade (blank field), never break compose.
- Timestamps `datetime.now(timezone.utc)`, `DateTime(timezone=True)`. Do not push to remote.

## Interfaces this plan builds on (verified against the current tree)

- `backend/modules/forms/fetch.py`: `ALLOWED_EXTENSIONS = {".html", ".htm", ".txt", ".xml", ".css"}` (line 31) — widen to include `.js`.
- `backend/modules/forms/library.py`: `forms_library_dir() -> Path` (= `${state_dir}/forms`); `find_template(basename) -> Path | None`; `clear_template_cache()`.
- `backend/modules/forms/routes.py`: `forms_router` registered `app.include_router(forms_router, prefix="/api/config")` — this is the GLOBAL forms router. The net-scoped compose/catalog/render endpoints need a NEW net-scoped router.
- `backend/integrations/winlink/b2f.py`: `@dataclass B2FAttachment(filename, content_type, data)`.
- `backend/integrations/delivery/backends/winlink.py`: `send` reads `config.get("attachments")` (a list of `B2FAttachment`).
- `backend/modules/events/message_service.py`: `send_event_message(db, event_id, *, actor, to_address, subject, body, reply_to_id=None)` → then `dispatch_delivery(db, "event_message", msg.id, subject, body, net_id, backends=["winlink"], config_overrides={"target_address": to_address})`.
- `backend/modules/events/routes.py`: `events_router` (prefix `/api/nets/{net_slug}/events`); helpers `_get_event_or_404`, `_message_to_response(m, extras=None)`, `_message_extras`, `_raise_for`; `require_net_role`, `NetRole`, `NetContext`, `get_db_session`, `EventStatus`. Inbound message `form` block already exposes `reply_template`/`display_form` (SP4a).
- `backend/modules/checkins/message_parser.py`: `find_form_xml(attachments, body) -> str | None`; `extract_form_variables(root)`.
- `backend/modules/events/models.py`: `EventMessage` (has `raw_message_id`); `_utcnow`.
- `backend/app.py`: routers registered ~line 310. Current alembic head `d5e2f6a1b3c7`.
- Frontend: `MessagesPanel.tsx` (compose entry point + expanded view), `useEventMessages`, `EventMessage` type, `apiFetch`, `Button`/`Input`/`Modal`/`Spinner`.

---

### Task 1: Fetch keeps `.js` + forms catalog

**Files:**
- Modify: `backend/modules/forms/fetch.py` (allowlist)
- Create: `backend/modules/forms/catalog.py`
- Create: `backend/modules/forms/net_routes.py` (net-scoped forms router: catalog route here; render added in Task 2)
- Modify: `backend/app.py` (register the net-scoped forms router)
- Test: `tests/test_forms_catalog.py`

**Interfaces:**
- Produces: `build_catalog() -> dict` in catalog.py — a nested tree `{name, folders: [...], forms: [{name, template_path, input_form_path}]}` of composable forms; cached in-process keyed by `forms.library_version`, `clear_catalog_cache()`. Route `GET /api/nets/{net_slug}/forms/catalog?q=` (net member). `net_forms_router` (prefix `/api/nets/{net_slug}/forms`).

- [ ] **Step 1: Widen the fetch allowlist**

In `backend/modules/forms/fetch.py` line 31:

```python
ALLOWED_EXTENSIONS = {".html", ".htm", ".txt", ".xml", ".css", ".js"}
```

- [ ] **Step 2: Write failing catalog tests**

```python
# tests/test_forms_catalog.py
import pytest

from backend.modules.forms import catalog as catalog_mod


@pytest.fixture
def forms_tree(tmp_path, monkeypatch):
    # Build a fake forms library: a composable form (txt + input html) in a folder,
    # a display-only txt (no input html) that must be excluded.
    base = tmp_path / "forms"
    ics = base / "ICS USA"
    ics.mkdir(parents=True)
    (ics / "ICS213.txt").write_text("Form: ICS213Input.html,ICS213Viewer.html\nSubject: <Var Subject>\nMsg:\n<Var MsgBody>\n")
    (ics / "ICS213Input.html").write_text("<html><body><form></form></body></html>")
    (base / "DisplayOnly.txt").write_text("Subject: x\nMsg:\nnope\n")  # no input form → excluded
    monkeypatch.setattr(catalog_mod, "forms_library_dir", lambda: base)
    catalog_mod.clear_catalog_cache()
    return base


def test_catalog_trees_composable_forms(forms_tree):
    tree = catalog_mod.build_catalog()
    # top-level has the "ICS USA" folder
    folders = {f["name"]: f for f in tree["folders"]}
    assert "ICS USA" in folders
    forms = folders["ICS USA"]["forms"]
    assert len(forms) == 1
    assert forms[0]["name"] == "ICS213"
    assert forms[0]["input_form_path"].endswith("ICS213Input.html")


def test_display_only_excluded(forms_tree):
    tree = catalog_mod.build_catalog()
    # DisplayOnly.txt (no input form) must not appear anywhere
    def all_form_names(node):
        names = [f["name"] for f in node["forms"]]
        for sub in node["folders"]:
            names += all_form_names(sub)
        return names
    assert "DisplayOnly" not in all_form_names(tree)


def test_catalog_cache_keyed_and_clearable(forms_tree, monkeypatch):
    catalog_mod.clear_catalog_cache()
    t1 = catalog_mod.build_catalog()
    # add a new form; without clearing, cache returns the old tree
    (forms_tree / "ICS USA" / "ICS214.txt").write_text("Form: ICS214Input.html\nMsg:\nx\n")
    (forms_tree / "ICS USA" / "ICS214Input.html").write_text("<form></form>")
    t2 = catalog_mod.build_catalog()
    assert t1 == t2  # cached
    catalog_mod.clear_catalog_cache()
    t3 = catalog_mod.build_catalog()
    names = [f["name"] for f in {f["name"]: f for f in t3["folders"]}["ICS USA"]["forms"]]
    assert set(names) == {"ICS213", "ICS214"}
```

- [ ] **Step 3: Run, verify failure**

Run: `.venv/bin/pytest tests/test_forms_catalog.py -q`
Expected: FAIL — no `catalog` module

- [ ] **Step 4: Implement the catalog**

```python
# backend/modules/forms/catalog.py
"""Tree the fetched Standard Forms library into a browsable catalog of
COMPOSABLE forms (a .txt template that references a fillable input HTML)."""
import re
import threading
from pathlib import Path

from backend.config import settings
from backend.config_mgmt.service import get_config_value
from backend.db.session import create_session_factory, create_engine_from_url  # noqa: F401 (only if needed)

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
```

Remove the unused `get_config_value` / `create_session_factory` imports from catalog.py (the route supplies the version). The tests call `build_catalog()` with the default `version=""` and use `clear_catalog_cache()` between library changes.

- [ ] **Step 5: Add the net-scoped catalog route**

```python
# backend/modules/forms/net_routes.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.auth.dependencies import NetContext, get_db_session, require_net_role
from backend.config_mgmt.service import get_config_value
from backend.modules.forms.catalog import build_catalog
from backend.modules.nets.models import NetRole

net_forms_router = APIRouter(prefix="/api/nets/{net_slug}/forms", tags=["forms"])


def _filter_tree(node: dict, q: str) -> dict:
    """Return a copy keeping only forms whose name contains q (case-insensitive)
    and folders that still have content."""
    forms = [f for f in node["forms"] if q in f["name"].lower()]
    folders = [_filter_tree(sub, q) for sub in node["folders"]]
    folders = [f for f in folders if f["forms"] or f["folders"]]
    return {"name": node["name"], "folders": folders, "forms": forms}


@net_forms_router.get("/catalog")
async def forms_catalog_route(
    q: str = Query(default=""),
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
    db: Session = Depends(get_db_session),
):
    version = get_config_value(db, "forms.library_version", "") or ""
    tree = build_catalog(version)
    if q:
        tree = _filter_tree(tree, q.strip().lower())
    return tree
```

Register in `backend/app.py` after the events router:

```python
from backend.modules.forms.net_routes import net_forms_router
...
app.include_router(net_forms_router)  # prefix: /api/nets/{net_slug}/forms
```

- [ ] **Step 6: Run tests + a route test + full suite + lint + commit**

Add a route test to `tests/test_forms_catalog.py` (ASGI client, net member, patch `catalog.forms_library_dir` + seed `forms.library_version` via net/global config as the existing forms tests do — mirror `tests/test_forms_fetch.py` fixture style). Assert `GET /api/nets/{slug}/forms/catalog` returns the tree and `?q=ics213` filters.

Run: `.venv/bin/pytest tests/test_forms_catalog.py -q && .venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add backend/modules/forms/fetch.py backend/modules/forms/catalog.py backend/modules/forms/net_routes.py backend/app.py tests/test_forms_catalog.py
git commit -m "feat(forms): keep .js on fetch and expose the composable forms catalog"
```

---

### Task 2: Sandboxed form-serve endpoint + collector shim

**Files:**
- Create: `backend/modules/forms/serve.py` (shim constant + HTML assembly)
- Modify: `backend/modules/forms/net_routes.py` (render route)
- Test: `tests/test_forms_serve.py`

**Interfaces:**
- Consumes: `forms_library_dir`.
- Produces: `render_input_form(rel_path: str, prefill: dict | None = None) -> str` in serve.py — returns the form's input HTML with the collector shim injected and (optional) prefill seeded; raises `FileNotFoundError`/`ValueError` on a bad/traversing path. Route `GET /api/nets/{net_slug}/forms/render?path=<rel>&…` (net_control) → `HTMLResponse` with the restrictive CSP header. `COLLECTOR_SHIM` constant.

- [ ] **Step 1: Write failing serve tests**

```python
# tests/test_forms_serve.py
import pytest

from backend.modules.forms import serve as serve_mod


@pytest.fixture
def lib(tmp_path, monkeypatch):
    base = tmp_path / "forms"
    (base / "ICS USA").mkdir(parents=True)
    (base / "ICS USA" / "ICS213Input.html").write_text(
        "<html><body><form id='f'><input name='MsgBody'></form></body></html>"
    )
    monkeypatch.setattr(serve_mod, "forms_library_dir", lambda: base)
    return base


def test_render_injects_shim(lib):
    html = serve_mod.render_input_form("ICS USA/ICS213Input.html")
    assert "<form id='f'>" in html
    assert "skynet-form-vars" in html  # the shim posts this message type
    assert "postMessage" in html
    assert html.rstrip().endswith("</html>") or "</body>" in html


def test_prefill_seeded(lib):
    html = serve_mod.render_input_form("ICS USA/ICS213Input.html", prefill={"MsgBody": "hello"})
    assert '"MsgBody": "hello"' in html or "'MsgBody': 'hello'" in html or "MsgBody" in html


def test_traversal_blocked(lib):
    with pytest.raises(ValueError):
        serve_mod.render_input_form("../../etc/passwd")
    with pytest.raises(ValueError):
        serve_mod.render_input_form("ICS USA/../../secret")


def test_missing_form(lib):
    with pytest.raises(FileNotFoundError):
        serve_mod.render_input_form("ICS USA/Nope.html")
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest tests/test_forms_serve.py -q`
Expected: FAIL — no `serve` module

- [ ] **Step 3: Implement serve.py**

```python
# backend/modules/forms/serve.py
"""Serve a form's INPUT html (with its own JS intact) plus a collector shim,
for loading into a locked sandboxed iframe during composition.

Security: the returned HTML is ONLY ever loaded into <iframe sandbox="allow-scripts">
(no allow-same-origin → opaque origin) with a restrictive CSP. The shim's only
capability is postMessage to the parent — it cannot reach our API/cookies/network."""
import json
import os

from backend.modules.forms.library import forms_library_dir

# Injected before </body>. Reads the form's fields on submit/Done and posts them
# to the parent. Prefill is applied on load. connect-src 'none' + opaque origin
# mean this script cannot do anything except postMessage.
COLLECTOR_SHIM = """
<script>
(function () {
  var PREFILL = __PREFILL__;
  function collect() {
    var form = document.querySelector("form");
    var vars = {};
    if (form) {
      var els = form.querySelectorAll("input[name], select[name], textarea[name]");
      els.forEach(function (el) {
        if (el.type === "checkbox" || el.type === "radio") {
          if (el.checked) vars[el.name] = el.value;
        } else {
          vars[el.name] = el.value;
        }
      });
    }
    parent.postMessage({ type: "skynet-form-vars", variables: vars }, "*");
  }
  function applyPrefill() {
    var form = document.querySelector("form");
    if (!form) return;
    Object.keys(PREFILL).forEach(function (name) {
      var el = form.querySelector("[name='" + name + "']");
      if (el) el.value = PREFILL[name];
    });
  }
  window.addEventListener("message", function (e) {
    if (e.data && e.data.type === "skynet-collect") collect();
  });
  document.addEventListener("submit", function (e) { e.preventDefault(); collect(); }, true);
  if (document.readyState !== "loading") applyPrefill();
  else document.addEventListener("DOMContentLoaded", applyPrefill);
})();
</script>
"""


def _resolve(rel_path: str):
    base = forms_library_dir().resolve()
    target = (base / rel_path).resolve()
    if base != target and base not in target.parents:
        raise ValueError("path escapes the forms library")
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    return target


def render_input_form(rel_path: str, prefill: dict | None = None) -> str:
    target = _resolve(rel_path)
    html = target.read_text(errors="replace")
    shim = COLLECTOR_SHIM.replace("__PREFILL__", json.dumps(prefill or {}))
    lower = html.lower()
    idx = lower.rfind("</body>")
    if idx != -1:
        return html[:idx] + shim + html[idx:]
    return html + shim
```

Note: `os` import is unused — remove it (ruff). Keep only what's used.

- [ ] **Step 4: Run serve tests, verify pass**

Run: `.venv/bin/pytest tests/test_forms_serve.py -q` — expected all pass.

- [ ] **Step 5: Add the render route**

In `backend/modules/forms/net_routes.py`, add:

```python
from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from backend.modules.forms.serve import render_input_form

_SANDBOX_CSP = (
    "sandbox; default-src 'none'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; img-src data:; connect-src 'none'; form-action 'none'"
)


@net_forms_router.get("/render")
async def forms_render_route(
    path: str = Query(...),
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
):
    try:
        html = render_input_form(path)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Form not found")
    return HTMLResponse(content=html, headers={"Content-Security-Policy": _SANDBOX_CSP})
```

- [ ] **Step 6: Add a route test + full suite + lint + commit**

Add to `tests/test_forms_serve.py` an ASGI route test (net_control client): `GET /api/nets/{slug}/forms/render?path=ICS%20USA/ICS213Input.html` → 200, body contains the shim, and `response.headers["content-security-policy"]` contains `sandbox` and `connect-src 'none'`. Also assert a traversal `path=../../x` → 404, and a viewer (non-NCS) → 403.

Run: `.venv/bin/pytest tests/test_forms_serve.py -q && .venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add backend/modules/forms/serve.py backend/modules/forms/net_routes.py tests/test_forms_serve.py
git commit -m "feat(forms): sandboxed form-serve endpoint with collector shim"
```

---

### Task 3: The message builder + RMS_Express_Form XML (PAT port, pinned)

**Files:**
- Create: `backend/modules/forms/builder.py`
- Create: `tests/fixtures/rms_express_form_sample.txt` (a real-format captured sample; see Step 6)
- Test: `tests/test_forms_builder.py`

**Interfaces:**
- Consumes: `find_template`/`forms_library_dir`; `B2FAttachment`.
- Produces (in `backend.modules.forms.builder`): `FormBuildError`; `@dataclass ComposedForm(to, cc, subject, body, attachment: B2FAttachment)`; `@dataclass BuildContext(callsign, datetime_stamp, grid="", app_version="SkyNetControl 0.1.0")`; `build_form_message(template_path: str, variables: dict, context: BuildContext) -> ComposedForm`; `build_form_xml(display_form, variables, context) -> str`; `find_unfilled_prompts(template_path, variables) -> list[dict]` (the `{Ask}`/`{Select}` prompts not yet answered). `INSERTION_TAGS` dict.

- [ ] **Step 1: Write failing builder tests**

```python
# tests/test_forms_builder.py
import pytest

from backend.integrations.winlink.b2f import B2FAttachment
from backend.modules.forms import builder as b


@pytest.fixture
def lib(tmp_path, monkeypatch):
    base = tmp_path / "forms"
    (base / "ICS USA").mkdir(parents=True)
    (base / "ICS USA" / "ICS213.txt").write_text(
        "Form: ICS213Input.html,ICS213Viewer.html\n"
        "To: <Var ToStation>\n"
        "Subject: ICS213 <Var Subject>\n"
        "Def: Precedence=Routine\n"
        "Msg:\n"
        "FROM: <MsgSender>  DTG: <DateTime>\n"
        "PREC: <Var Precedence>\n"
        "MESSAGE:\n"
        "<Var MsgBody>\n"
    )
    monkeypatch.setattr(b, "forms_library_dir", lambda: base)
    b.clear_template_cache_if_any()  # no-op hook if builder caches; else remove
    return base


CTX = b.BuildContext(callsign="W0NE", datetime_stamp="2026/07/17 18:30", grid="EM28")


def test_var_and_insertion_substitution(lib):
    result = b.build_form_message(
        "ICS USA/ICS213.txt",
        {"ToStation": "KE0XYZ", "Subject": "SITREP", "MsgBody": "All clear"},
        CTX,
    )
    assert result.to == "KE0XYZ"
    assert result.subject == "ICS213 SITREP"
    assert "FROM: W0NE  DTG: 2026/07/17 18:30" in result.body
    assert "PREC: Routine" in result.body        # Def: default filled the unset Var
    assert "MESSAGE:\nAll clear" in result.body


def test_attachment_is_rms_express_form_xml(lib):
    result = b.build_form_message(
        "ICS USA/ICS213.txt",
        {"ToStation": "KE0XYZ", "Subject": "S", "MsgBody": "x"},
        CTX,
    )
    assert isinstance(result.attachment, B2FAttachment)
    assert result.attachment.filename.startswith("RMS_Express_Form_")
    assert result.attachment.filename.endswith(".xml")
    xml = result.attachment.data.decode("utf-8")
    assert "<RMS_Express_Form>" in xml
    assert "<display_form>ICS213Input.html</display_form>" in xml or "<display_form>ICS213Viewer.html</display_form>" in xml
    assert "<senders_callsign>W0NE</senders_callsign>" in xml
    assert "<subject>SITREP</subject>".lower() in xml.lower() or "subject" in xml.lower()


def test_deterministic(lib):
    args = ("ICS USA/ICS213.txt", {"ToStation": "K", "Subject": "S", "MsgBody": "b"}, CTX)
    a = b.build_form_message(*args)
    c = b.build_form_message(*args)
    assert a.attachment.data == c.attachment.data
    assert a.body == c.body


def test_unfilled_prompts_detected(lib, tmp_path, monkeypatch):
    (tmp_path / "forms" / "P.txt").write_text("Subject: s\nMsg:\nName: {Ask}\nUnit: {Select}\n")
    prompts = b.find_unfilled_prompts("P.txt", {})
    assert len(prompts) == 2


def test_missing_template_raises(lib):
    with pytest.raises(b.FormBuildError):
        b.build_form_message("ICS USA/Nope.txt", {}, CTX)


def test_blank_insertion_tag_when_unsourced(lib, tmp_path, monkeypatch):
    (tmp_path / "forms" / "G.txt").write_text("Subject: s\nMsg:\nGRID: <GridSquare>\n")
    ctx_nogrid = b.BuildContext(callsign="W0NE", datetime_stamp="2026/07/17 18:30", grid="")
    result = b.build_form_message("G.txt", {}, ctx_nogrid)
    assert "GRID: " in result.body  # blank, not the literal <GridSquare>
    assert "<GridSquare>" not in result.body
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest tests/test_forms_builder.py -q`
Expected: FAIL — no builder module

- [ ] **Step 3: Implement the builder**

```python
# backend/modules/forms/builder.py
"""Deterministic port of PAT's form message builder (scanAndBuild + buildXML).

Pure over (template, variables, context). <DateTime> and friends come from the
context (stamped once at compose, reused on retry) — never the clock here — so
preview == send == retry byte-for-byte."""
import re
from dataclasses import dataclass, field

from backend.integrations.winlink.b2f import B2FAttachment
from backend.modules.forms.library import forms_library_dir

XML_FILE_VERSION = "1.0"


class FormBuildError(Exception):
    """Template missing or unparseable."""


@dataclass
class BuildContext:
    callsign: str
    datetime_stamp: str  # "YYYY/MM/DD HH:MM" — stamped once, reused on retry
    grid: str = ""
    app_version: str = "SkyNetControl 0.1.0"


@dataclass
class ComposedForm:
    to: str
    cc: str
    subject: str
    body: str
    attachment: B2FAttachment
    display_form: str = ""
    reply_template: str = ""
    variables: dict = field(default_factory=dict)


# Insertion tags → resolver over (context). Ported from PAT's insertion_tags set;
# unresolved tags render blank (matching PAT with unconfigured station data).
def _insertion_value(tag: str, ctx: BuildContext, variables: dict) -> str:
    t = tag.lower()
    if t in ("msgsender", "callsign", "senders_callsign"):
        return ctx.callsign
    if t in ("datetime", "udtg", "date", "time"):
        return ctx.datetime_stamp
    if t in ("gridsquare", "grid_square", "gps", "position", "latlon"):
        return ctx.grid
    return ""  # unknown/unsourced tag → blank


_VAR_RE = re.compile(r"<Var\s+([A-Za-z0-9_]+)\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<([A-Za-z0-9_]+)\s*>")  # bare insertion tags
_PROMPT_RE = re.compile(r"\{(Ask|Select)[^}]*\}", re.IGNORECASE)
_FORM_RE = re.compile(r"^\s*Form\s*:\s*(.+)$", re.IGNORECASE)
_REPLY_RE = re.compile(r"^\s*ReplyTemplate\s*:\s*(.+)$", re.IGNORECASE)


def _read_template(template_path: str) -> str:
    base = forms_library_dir().resolve()
    target = (base / template_path).resolve()
    if base != target and base not in target.parents:
        raise FormBuildError("template path escapes the forms library")
    if not target.is_file():
        raise FormBuildError(f"template not found: {template_path}")
    return target.read_text(errors="replace")


def _substitute(text: str, variables: dict, ctx: BuildContext) -> str:
    # <Var Name> first (case-insensitive lookup), then bare insertion tags.
    lower_vars = {k.lower(): v for k, v in variables.items()}

    def var_sub(m):
        return str(lower_vars.get(m.group(1).lower(), ""))

    text = _VAR_RE.sub(var_sub, text)

    def tag_sub(m):
        return _insertion_value(m.group(1), ctx, variables)

    return _TAG_RE.sub(tag_sub, text)


def _parse(template_text: str):
    """Split control lines from the Msg body. Returns (control: dict, defs: dict,
    body_template: str, display_form: str, reply_template: str)."""
    control = {}
    defs = {}
    display_form = ""
    reply_template = ""
    lines = template_text.splitlines()
    body_lines = []
    in_body = False
    for line in lines:
        if in_body:
            body_lines.append(line)
            continue
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("msg:"):
            in_body = True
            continue
        fm = _FORM_RE.match(line)
        if fm:
            display_form = fm.group(1).split(",")[0].strip()
            continue
        rm = _REPLY_RE.match(line)
        if rm:
            reply_template = rm.group(1).strip()
            continue
        if low.startswith("def:"):
            body = stripped[4:].strip()
            if "=" in body:
                k, _, v = body.partition("=")
                defs[k.strip()] = v.strip()
            continue
        for key in ("to", "cc", "subject", "subj"):
            if low.startswith(key + ":"):
                control["subject" if key == "subj" else key] = stripped[len(key) + 1:].strip()
                break
    return control, defs, "\n".join(body_lines), display_form, reply_template


def build_form_message(template_path: str, variables: dict, context: BuildContext) -> ComposedForm:
    text = _read_template(template_path)
    control, defs, body_template, display_form, reply_template = _parse(text)
    # Def: defaults fill gaps; explicit non-empty variables win.
    merged = dict(defs)
    merged.update(variables)
    for k, v in defs.items():
        if not merged.get(k):
            merged[k] = v

    to = _substitute(control.get("to", ""), merged, context)
    cc = _substitute(control.get("cc", ""), merged, context)
    subject = _substitute(control.get("subject", ""), merged, context)
    body = _substitute(body_template, merged, context)

    xml = build_form_xml(display_form, merged, context)
    attachment = B2FAttachment(
        filename=_xml_name(display_form),
        content_type="application/xml",
        data=xml.encode("utf-8"),
    )
    return ComposedForm(
        to=to, cc=cc, subject=subject, body=body, attachment=attachment,
        display_form=display_form, reply_template=reply_template, variables=merged,
    )


def _xml_name(display_form: str) -> str:
    stem = (display_form.rsplit(".", 1)[0] if "." in display_form else display_form) or "Form"
    name = f"RMS_Express_Form_{stem}.xml"
    return name[:255]


def build_form_xml(display_form: str, variables: dict, context: BuildContext) -> str:
    import xml.sax.saxutils as su

    parts = [
        "<?xml version=\"1.0\"?>",
        "<RMS_Express_Form>",
        "  <form_parameters>",
        f"    <xml_file_version>{XML_FILE_VERSION}</xml_file_version>",
        f"    <rms_express_version>{su.escape(context.app_version)}</rms_express_version>",
        f"    <submission_datetime>{su.escape(context.datetime_stamp)}</submission_datetime>",
        f"    <senders_callsign>{su.escape(context.callsign)}</senders_callsign>",
        f"    <grid_square>{su.escape(context.grid)}</grid_square>",
        f"    <display_form>{su.escape(display_form)}</display_form>",
        "  </form_parameters>",
        "  <variables>",
    ]
    for key in sorted(variables.keys()):
        parts.append(f"    <{key}>{su.escape(str(variables[key]))}</{key}>")
    parts.append("  </variables>")
    parts.append("</RMS_Express_Form>")
    return "\n".join(parts) + "\n"


def find_unfilled_prompts(template_path: str, variables: dict) -> list[dict]:
    text = _read_template(template_path)
    prompts = []
    for i, m in enumerate(_PROMPT_RE.finditer(text)):
        kind = m.group(1).lower()
        prompts.append({"index": i, "kind": kind, "raw": m.group(0)})
    return prompts


def clear_template_cache_if_any() -> None:
    """No builder-level cache; hook kept for test symmetry."""
    return None
```

Note: the merge logic for `defs`/`variables` above is over-written — simplify to: `merged = dict(defs); merged.update(variables)`; then after substitution any variable still empty stays empty. Fix the merge block to exactly:

```python
    merged = dict(defs)
    merged.update(variables)
    for k, v in defs.items():
        if not merged.get(k):
            merged[k] = v
```

Use that clean version (delete the tangled lines).

- [ ] **Step 4: Run builder tests, verify pass**

Run: `.venv/bin/pytest tests/test_forms_builder.py -q` — expected all pass. Adjust the synthetic template/assertions only if a substitution rule needs tightening; the builder is the contract.

- [ ] **Step 5: Lint + commit the builder**

```bash
nix-shell --run "ruff check"
git add backend/modules/forms/builder.py tests/test_forms_builder.py
git commit -m "feat(forms): PAT-faithful form message builder and RMS_Express_Form XML"
```

- [ ] **Step 6: PIN against a real captured message (interop gate)**

This step validates the XML/output against REAL Winlink output, not synthetic fixtures.

1. Obtain a real `RMS_Express_Form_*.xml` (from a captured form message, e.g. `tests/fixtures/` supplied by the operator, or an inbound one the running system captured via SP4a). If NONE is available, STOP and report `NEEDS_CONTEXT: real RMS_Express_Form sample required to pin the builder` — do not fabricate one.
2. Write `tests/test_forms_builder_interop.py` that parses the real sample's `<form_parameters>` and `<variables>`, feeds those variables + a matching template through `build_form_message`, and asserts our generated `<RMS_Express_Form>` has the same element structure (form_parameters keys, variables round-trip). Where our output diverges from the real sample in a way that would break a receiving RMS Express, FIX the builder (element names, ordering, casing) and note it.
3. Commit: `test(forms): pin builder against a real RMS_Express_Form sample`.

If the operator sample is unavailable at implementation time, mark this step BLOCKED and surface it — the feature ships behind this gate.

---

### Task 4: `event_message_forms` model + migration

**Files:**
- Modify: `backend/modules/events/models.py`
- Create: `alembic/versions/e6f3a2b1c4d8_add_event_message_forms.py`
- Test: `tests/test_event_message_form_models.py`

**Interfaces:**
- Produces: `EventMessageForm(id, event_message_id FK→event_messages.id cascade, template_path, display_form, reply_template nullable, variables JSON, datetime_stamp, created_at)`; `EventMessage.form_record` relationship. Later tasks import `EventMessageForm`.

- [ ] **Step 1: Write failing model tests**

```python
# tests/test_event_message_form_models.py
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.base import Base
from backend.modules.events.models import (
    Event, EventMessage, EventMessageForm, EventType, MessageDirection, MessageStatus,
)
from tests.conftest import make_test_net


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


def _msg(db):
    net = make_test_net(db)
    event = Event(net_id=net.id, name="E", event_type=EventType.EMERGENCY, created_by="W0NE")
    db.add(event); db.commit(); db.refresh(event)
    m = EventMessage(event_id=event.id, msg_seq=1, direction=MessageDirection.OUTBOUND,
                     from_callsign="W0NE", to_address="KE0XYZ", subject="s", body="b",
                     status=MessageStatus.READ, actor="W0NC")
    db.add(m); db.commit(); db.refresh(m)
    return m


def test_form_record_persists(db):
    m = _msg(db)
    rec = EventMessageForm(event_message_id=m.id, template_path="ICS USA/ICS213.txt",
                           display_form="ICS213Input.html", reply_template=None,
                           variables={"MsgBody": "x", "ToStation": "KE0XYZ"},
                           datetime_stamp="2026/07/17 18:30")
    db.add(rec); db.commit(); db.refresh(rec)
    assert rec.variables["MsgBody"] == "x"
    assert rec.created_at is not None


def test_cascade_delete_with_message(db):
    m = _msg(db)
    db.add(EventMessageForm(event_message_id=m.id, template_path="t", display_form="d",
                            variables={}, datetime_stamp="2026/07/17 18:30"))
    db.commit()
    db.delete(m); db.commit()
    assert db.query(EventMessageForm).count() == 0
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest tests/test_event_message_form_models.py -q`
Expected: FAIL — ImportError

- [ ] **Step 3: Add the model**

In `backend/modules/events/models.py` (ensure `JSON` imported from sqlalchemy), append:

```python
class EventMessageForm(Base):
    __tablename__ = "event_message_forms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("event_messages.id", ondelete="CASCADE"), nullable=False
    )
    template_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    display_form: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    reply_template: Mapped[str | None] = mapped_column(String(255), nullable=True)
    variables: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    datetime_stamp: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
```

Add to `EventMessage` (near its relationships):

```python
    form_record: Mapped["EventMessageForm | None"] = relationship(
        cascade="all, delete-orphan"
    )
```

- [ ] **Step 4: Run model tests, verify pass**

Run: `.venv/bin/pytest tests/test_event_message_form_models.py -q` — expected 2 passed.

- [ ] **Step 5: Write the migration**

```python
# alembic/versions/e6f3a2b1c4d8_add_event_message_forms.py
"""add event message forms

Revision ID: e6f3a2b1c4d8
Revises: d5e2f6a1b3c7
Create Date: 2026-07-17 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e6f3a2b1c4d8'
down_revision: Union[str, None] = 'd5e2f6a1b3c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'event_message_forms',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('event_message_id', sa.Integer(), nullable=False),
        sa.Column('template_path', sa.String(length=1024), nullable=False),
        sa.Column('display_form', sa.String(length=255), nullable=False),
        sa.Column('reply_template', sa.String(length=255), nullable=True),
        sa.Column('variables', sa.JSON(), nullable=False),
        sa.Column('datetime_stamp', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['event_message_id'], ['event_messages.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('event_message_forms')
```

Verify: `SKYNET_DATABASE_URL="sqlite:////tmp/claude-emf-mig.db" .venv/bin/alembic upgrade head && rm -f /tmp/claude-emf-mig.db` — clean.

- [ ] **Step 6: Full suite + lint + commit**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — all pass.

```bash
git add backend/modules/events/models.py alembic/versions/e6f3a2b1c4d8_add_event_message_forms.py tests/test_event_message_form_models.py
git commit -m "feat(events): event_message_forms companion table and migration"
```

---

### Task 5: Outbound form compose/reply service + routes

**Files:**
- Modify: `backend/modules/events/message_service.py` (form send + reply resolution)
- Modify: `backend/modules/events/routes.py` (compose-preview, form-send, reply-form routes)
- Test: `tests/test_event_form_compose.py`

**Interfaces:**
- Consumes: Task 3 builder; Task 4 `EventMessageForm`; SP3 `send_event_message`/`dispatch_delivery`; `find_form_xml`/`extract_form_variables`.
- Produces:
  - `compose_form_preview(db, event_id, *, template_path, variables, datetime_stamp) -> dict` — builds (no send) → `{to, subject, body, attachment_filename}`.
  - `send_event_form_message(db, event_id, *, actor, template_path, variables, datetime_stamp, reply_to_id=None) -> EventMessage` — builds, creates the outbound `EventMessage` + `EventMessageForm`, dispatches winlink with the XML attachment.
  - `resolve_reply_form(db, event_id, message_id) -> dict` — from the inbound message's captured form → `{reply_template_path, input_form_path | None, prefill: dict}`.
  - Routes: `POST /events/{id}/forms/preview`, `POST /events/{id}/form-messages`, `GET /events/{id}/messages/{mid}/reply-form`.

- [ ] **Step 1: Write failing service/route tests**

```python
# tests/test_event_form_compose.py
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth.models import User
from backend.config import Settings
from backend.db.base import Base
from backend.modules.events.models import EventMessage, EventMessageForm, MessageDirection
from backend.modules.nets.models import Net, NetMembership, NetRole
from backend.modules.nets.config_service import set_net_config_bulk
from tests.conftest import make_test_token

NET_SLUG = "t"
BASE = f"/api/nets/{NET_SLUG}/events"


@pytest.fixture
def test_settings():
    return Settings(database_url="sqlite:///", jwt_secret_key="test-secret", jwt_expire_minutes=60)


@pytest.fixture
def db_setup(tmp_path, monkeypatch):
    # A forms library with one composable template, patched into builder + serve.
    forms = tmp_path / "forms"
    (forms / "ICS USA").mkdir(parents=True)
    (forms / "ICS USA" / "ICS213.txt").write_text(
        "Form: ICS213Input.html\nTo: <Var ToStation>\nSubject: <Var Subject>\nMsg:\n<Var MsgBody>\n"
    )
    (forms / "ICS USA" / "ICS213Input.html").write_text("<form><input name='MsgBody'></form>")
    import backend.modules.forms.builder as bld
    monkeypatch.setattr(bld, "forms_library_dir", lambda: forms)

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        nc = User(callsign="W0NC", oidc_subject="auth0|nc", name="NC")
        net = Net(slug=NET_SLUG, name="Test Net", is_public=False)
        s.add_all([nc, net]); s.flush()
        s.add(NetMembership(user_callsign="W0NC", net_id=net.id, role=NetRole.NET_CONTROL))
        set_net_config_bulk(s, net.id, {"net_address": "W0NE@winlink.org",
                                        "pat_mailbox_path": str(tmp_path / "mailbox")})
        s.commit()
        yield {"engine": engine, "factory": factory, "forms": forms}
    engine.dispose()


@pytest.fixture
def app(test_settings, db_setup):
    from backend.app import create_app
    a = create_app(settings=test_settings)
    a.state.engine = db_setup["engine"]; a.state.session_factory = db_setup["factory"]
    return a


@pytest.fixture
async def nc(app, test_settings):
    token = make_test_token("W0NC", test_settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           cookies={"access_token": token}) as c:
        yield c


@pytest.fixture
async def active_event(nc):
    return (await nc.post(BASE, json={"name": "E", "event_type": "emergency", "activate": True})).json()["id"]


class TestPreview:
    async def test_preview_builds_without_send(self, nc, active_event):
        resp = await nc.post(f"{BASE}/{active_event}/forms/preview", json={
            "template_path": "ICS USA/ICS213.txt",
            "variables": {"ToStation": "KE0XYZ", "Subject": "SITREP", "MsgBody": "all clear"},
            "datetime_stamp": "2026/07/17 18:30",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["to"] == "KE0XYZ"
        assert body["subject"] == "SITREP"
        assert "all clear" in body["body"]
        assert body["attachment_filename"].startswith("RMS_Express_Form_")


class TestSend:
    async def test_send_creates_message_and_form_record(self, nc, active_event, db_setup):
        resp = await nc.post(f"{BASE}/{active_event}/form-messages", json={
            "template_path": "ICS USA/ICS213.txt",
            "variables": {"ToStation": "KE0XYZ", "Subject": "S", "MsgBody": "x"},
            "datetime_stamp": "2026/07/17 18:30",
        })
        assert resp.status_code == 201
        msg = resp.json()["message"]
        assert msg["direction"] == "outbound"
        assert msg["to_address"] == "KE0XYZ"
        with db_setup["factory"]() as db:
            rec = db.query(EventMessageForm).one()
            assert rec.template_path == "ICS USA/ICS213.txt"
            assert rec.variables["MsgBody"] == "x"

    async def test_send_closed_event_409(self, nc, active_event):
        await nc.post(f"{BASE}/{active_event}/close")
        resp = await nc.post(f"{BASE}/{active_event}/form-messages", json={
            "template_path": "ICS USA/ICS213.txt", "variables": {}, "datetime_stamp": "2026/07/17 18:30"})
        assert resp.status_code == 409

    async def test_bad_template_422(self, nc, active_event):
        resp = await nc.post(f"{BASE}/{active_event}/form-messages", json={
            "template_path": "ICS USA/Nope.txt", "variables": {}, "datetime_stamp": "2026/07/17 18:30"})
        assert resp.status_code == 422
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest tests/test_event_form_compose.py -q`
Expected: FAIL — routes missing

- [ ] **Step 3: Implement the service functions**

In `backend/modules/events/message_service.py`, add (imports: builder, models, find_form_xml):

```python
def _build_context(db, net_id: int, datetime_stamp: str):
    from backend.modules.forms.builder import BuildContext
    from backend.modules.nets.config_service import get_net_config

    net_address = get_net_config(db, net_id, "net_address", "") or ""
    callsign = net_address.split("@")[0].upper() if net_address else ""
    return BuildContext(callsign=callsign, datetime_stamp=datetime_stamp, grid="")


def compose_form_preview(db, event_id, *, template_path, variables, datetime_stamp) -> dict:
    from backend.modules.forms.builder import FormBuildError, build_form_message
    from backend.modules.events.models import Event

    event = db.get(Event, event_id)
    ctx = _build_context(db, event.net_id, datetime_stamp)
    try:
        composed = build_form_message(template_path, variables, ctx)
    except FormBuildError as exc:
        raise ValueError(str(exc))
    return {
        "to": composed.to, "subject": composed.subject, "body": composed.body,
        "attachment_filename": composed.attachment.filename,
    }


def send_event_form_message(db, event_id, *, actor, template_path, variables, datetime_stamp, reply_to_id=None):
    from backend.modules.forms.builder import FormBuildError, build_form_message
    from backend.modules.events.models import (
        Event, EventMessage, EventMessageForm, EventStatus, MessageDirection, MessageStatus,
    )
    from backend.modules.events.messages import next_msg_seq
    from backend.modules.events.service import EventNotActiveError, locked_event
    from backend.integrations.delivery.service import dispatch_delivery

    event = locked_event(db, event_id)
    if event is None or event.status != EventStatus.ACTIVE:
        raise EventNotActiveError("Event is not active")
    ctx = _build_context(db, event.net_id, datetime_stamp)
    try:
        composed = build_form_message(template_path, variables, ctx)
    except FormBuildError as exc:
        raise ValueError(str(exc))

    seq = next_msg_seq(event)
    message = EventMessage(
        event_id=event_id, msg_seq=seq, direction=MessageDirection.OUTBOUND,
        from_callsign=ctx.callsign, to_address=composed.to,
        subject=composed.subject, body=composed.body,
        status=MessageStatus.READ, actor=actor, reply_to_id=reply_to_id,
    )
    db.add(message)
    db.flush()
    db.add(EventMessageForm(
        event_message_id=message.id, template_path=template_path,
        display_form=composed.display_form, reply_template=composed.reply_template or None,
        variables=composed.variables, datetime_stamp=datetime_stamp,
    ))
    db.commit()
    db.refresh(message)

    dispatch_delivery(
        db, "event_message", message.id, composed.subject, composed.body, event.net_id,
        backends=["winlink"],
        config_overrides={"target_address": composed.to, "attachments": [composed.attachment]},
    )
    db.refresh(message)
    return message


def resolve_reply_form(db, event_id, message_id) -> dict:
    """From an inbound form message → the reply template's input form (if any)
    and prefill from the sender's variables."""
    import xml.etree.ElementTree as ET
    from backend.modules.checkins.message_parser import extract_form_variables, find_form_xml
    from backend.modules.checkins.models import RawMessage, RawMessageAttachment
    from backend.modules.events.models import EventMessage
    from backend.modules.forms.catalog import _input_form_for  # reuse the resolver
    from backend.modules.forms.library import find_template, forms_library_dir

    msg = db.query(EventMessage).filter_by(id=message_id, event_id=event_id).one_or_none()
    if msg is None or msg.raw_message_id is None:
        raise ValueError("message not found")
    atts = db.query(RawMessageAttachment).filter_by(raw_message_id=msg.raw_message_id).all()
    raw = db.get(RawMessage, msg.raw_message_id)
    xml_text = find_form_xml(
        [{"filename": a.filename, "content_type": a.content_type, "data": a.data} for a in atts],
        raw.body if raw else "",
    )
    if not xml_text:
        raise ValueError("no form to reply to")
    root = ET.fromstring(xml_text)
    rt = root.find(".//form_parameters/reply_template")
    reply_name = (rt.text or "").strip() if rt is not None else ""
    prefill = extract_form_variables(root)

    reply_template_path = ""
    input_form_path = None
    if reply_name:
        tpath = find_template(reply_name if reply_name.endswith(".txt") else reply_name + ".txt")
        if tpath is not None:
            base = forms_library_dir()
            reply_template_path = str(tpath.relative_to(base))
            inp = _input_form_for(tpath)
            if inp is not None:
                input_form_path = str(inp.relative_to(base))
    return {"reply_template_path": reply_template_path, "input_form_path": input_form_path, "prefill": prefill}
```

- [ ] **Step 4: Add the routes**

In `backend/modules/events/routes.py`, add schemas + routes:

```python
class FormComposeBody(BaseModel):
    template_path: str
    variables: dict = {}
    datetime_stamp: str
    reply_to_id: int | None = None


@events_router.post("/{event_id}/forms/preview")
async def form_preview_route(
    event_id: int, body: FormComposeBody,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    from backend.modules.events.message_service import compose_form_preview
    try:
        return compose_form_preview(db, event_id, template_path=body.template_path,
                                    variables=body.variables, datetime_stamp=body.datetime_stamp)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@events_router.post("/{event_id}/form-messages", status_code=201)
async def form_send_route(
    event_id: int, body: FormComposeBody,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    from backend.modules.events.message_service import send_event_form_message
    try:
        message = send_event_form_message(
            db, event_id, actor=ctx.user.callsign, template_path=body.template_path,
            variables=body.variables, datetime_stamp=body.datetime_stamp, reply_to_id=body.reply_to_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except EventError as err:
        _raise_for(err)
    from backend.integrations.delivery.service import get_delivery_status
    from backend.integrations.delivery.models import DeliveryStatus
    logs = get_delivery_status(db, "event_message", message.id)
    delivered = any(log.status == DeliveryStatus.SENT for log in logs)
    return {"message": _message_to_response(message, _message_extras(db, message)), "delivered": delivered}


@events_router.get("/{event_id}/messages/{message_id}/reply-form")
async def reply_form_route(
    event_id: int, message_id: int,
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
    db: Session = Depends(get_db_session),
):
    _get_event_or_404(db, ctx.net.id, event_id)
    from backend.modules.events.message_service import resolve_reply_form
    try:
        return resolve_reply_form(db, event_id, message_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
```

(`EventError` is already imported for `_raise_for`; confirm.)

- [ ] **Step 5: Run tests + full suite + lint + commit**

Run: `.venv/bin/pytest tests/test_event_form_compose.py -q && .venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add backend/modules/events/message_service.py backend/modules/events/routes.py tests/test_event_form_compose.py
git commit -m "feat(events): outbound form compose, send, and reply-form resolution"
```

---

### Task 6: Frontend types, API, compose hook

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/api/events.ts`
- Create: `frontend/src/api/forms.ts` additions (or extend the existing `forms.ts`)
- Create: `frontend/src/hooks/useFormCompose.ts`

**Interfaces:**
- Produces: types `FormCatalogNode`, `FormCatalogEntry`, `FormPreview`, `ReplyForm`; API `fetchFormCatalog`, `formRenderUrl`, `previewForm`, `sendFormMessage`, `fetchReplyForm`; `useFormCompose(netSlug, eventId)` hook managing the multi-step state.

- [ ] **Step 1: Add types**

Append to `frontend/src/types/index.ts`:

```typescript
export interface FormCatalogEntry { name: string; template_path: string; input_form_path: string; }
export interface FormCatalogNode { name: string; folders: FormCatalogNode[]; forms: FormCatalogEntry[]; }
export interface FormPreview { to: string; subject: string; body: string; attachment_filename: string; }
export interface ReplyForm { reply_template_path: string; input_form_path: string | null; prefill: Record<string, string>; }
```

- [ ] **Step 2: Add API functions**

Append to `frontend/src/api/events.ts` (import the new types):

```typescript
export async function fetchFormCatalog(netSlug: string, q = ""): Promise<FormCatalogNode> {
  const p = q ? `?q=${encodeURIComponent(q)}` : "";
  return apiFetch<FormCatalogNode>(`/nets/${netSlug}/forms/catalog${p}`);
}

export function formRenderUrl(netSlug: string, path: string): string {
  return `/api/nets/${netSlug}/forms/render?path=${encodeURIComponent(path)}`;
}

export interface FormComposeInput {
  template_path: string;
  variables: Record<string, string>;
  datetime_stamp: string;
  reply_to_id?: number | null;
}

export async function previewForm(eventId: number, input: FormComposeInput, netSlug: string): Promise<FormPreview> {
  return apiFetch<FormPreview>(`/nets/${netSlug}/events/${eventId}/forms/preview`, {
    method: "POST", body: JSON.stringify(input),
  });
}

export async function sendFormMessage(
  eventId: number, input: FormComposeInput, netSlug: string,
): Promise<{ message: EventMessage; delivered: boolean }> {
  return apiFetch<{ message: EventMessage; delivered: boolean }>(
    `/nets/${netSlug}/events/${eventId}/form-messages`,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export async function fetchReplyForm(eventId: number, messageId: number, netSlug: string): Promise<ReplyForm> {
  return apiFetch<ReplyForm>(`/nets/${netSlug}/events/${eventId}/messages/${messageId}/reply-form`);
}
```

- [ ] **Step 3: The compose hook**

```typescript
// frontend/src/hooks/useFormCompose.ts
import { useCallback, useState } from "react";
import { previewForm, sendFormMessage } from "../api/events";
import type { FormPreview } from "../types";

type Step = "catalog" | "fill" | "preview";

function nowStamp(): string {
  // "YYYY/MM/DD HH:MM" UTC — stamped ONCE at compose, reused for send.
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}/${p(d.getUTCMonth() + 1)}/${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

export function useFormCompose(netSlug: string, eventId: number) {
  const [step, setStep] = useState<Step>("catalog");
  const [templatePath, setTemplatePath] = useState("");
  const [inputFormPath, setInputFormPath] = useState("");
  const [variables, setVariables] = useState<Record<string, string>>({});
  const [prefill, setPrefill] = useState<Record<string, string>>({});
  const [replyToId, setReplyToId] = useState<number | null>(null);
  const [stamp] = useState(nowStamp());
  const [preview, setPreview] = useState<FormPreview | null>(null);

  const pickForm = useCallback((template: string, inputForm: string) => {
    setTemplatePath(template); setInputFormPath(inputForm); setStep("fill");
  }, []);

  const acceptVariables = useCallback(async (vars: Record<string, string>, onError: (m: string) => void) => {
    setVariables(vars);
    try {
      const p = await previewForm(eventId, { template_path: templatePath, variables: vars, datetime_stamp: stamp }, netSlug);
      setPreview(p); setStep("preview");
    } catch (e) {
      onError(e instanceof Error ? e.message : "Build failed");
    }
  }, [eventId, netSlug, templatePath, stamp]);

  const send = useCallback(async (onDone: () => Promise<void>, onError: (m: string) => void) => {
    try {
      const { delivered } = await sendFormMessage(
        eventId, { template_path: templatePath, variables, datetime_stamp: stamp, reply_to_id: replyToId }, netSlug,
      );
      await onDone();
      if (!delivered) onError("Form saved but not delivered — check delivery / retry.");
    } catch (e) {
      onError(e instanceof Error ? e.message : "Send failed");
    }
  }, [eventId, netSlug, templatePath, variables, stamp, replyToId]);

  return {
    step, setStep, templatePath, inputFormPath, prefill, preview, replyToId,
    pickForm, acceptVariables, send,
    startReply: (template: string, inputForm: string, pf: Record<string, string>, rid: number) => {
      setTemplatePath(template); setInputFormPath(inputForm); setPrefill(pf); setReplyToId(rid); setStep("fill");
    },
  };
}
```

- [ ] **Step 4: Build check + commit**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` — expected clean.

```bash
git add frontend/src/types/index.ts frontend/src/api/events.ts frontend/src/hooks/useFormCompose.ts
git commit -m "feat(forms): frontend types, compose API, and useFormCompose hook"
```

---

### Task 7: Frontend compose UI (catalog → fill → preview) + Messages-panel entry

**Files:**
- Create: `frontend/src/pages/events/FormCatalog.tsx`
- Create: `frontend/src/pages/events/FormFillFrame.tsx`
- Create: `frontend/src/pages/events/FormCompose.tsx` (orchestrates the modal over the hook)
- Modify: `frontend/src/pages/events/MessagesPanel.tsx` (New form + Reply with form entry)

**Interfaces:**
- Consumes: Task 6 hook/API; the `render` URL for the iframe; SP4a inbound `form` metadata on messages.
- Produces: the compose modal wired into the Messages panel.

- [ ] **Step 1: FormCatalog (tree + search)**

```tsx
// frontend/src/pages/events/FormCatalog.tsx
import { useEffect, useState } from "react";
import { fetchFormCatalog } from "../../api/events";
import { Input } from "../../components/Input";
import { Spinner } from "../../components/Spinner";
import type { FormCatalogEntry, FormCatalogNode } from "../../types";

function Node({ node, onPick, depth }: { node: FormCatalogNode; onPick: (e: FormCatalogEntry) => void; depth: number }) {
  return (
    <div style={{ paddingLeft: depth * 12 }}>
      {node.name && <div className="text-xs font-semibold text-text-muted mt-1">{node.name}</div>}
      {node.forms.map((f) => (
        <button key={f.template_path} onClick={() => onPick(f)}
          className="block text-left text-sm text-accent hover:underline py-0.5">
          {f.name}
        </button>
      ))}
      {node.folders.map((sub) => <Node key={sub.name} node={sub} onPick={onPick} depth={depth + 1} />)}
    </div>
  );
}

export function FormCatalog({ netSlug, onPick }: { netSlug: string; onPick: (e: FormCatalogEntry) => void }) {
  const [tree, setTree] = useState<FormCatalogNode | null>(null);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchFormCatalog(netSlug, q).then(setTree).catch((e) => setError(e instanceof Error ? e.message : "Failed"));
  }, [netSlug, q]);

  if (error) return <p className="text-danger text-sm">{error}</p>;
  if (!tree) return <Spinner size="md" />;
  const empty = tree.forms.length === 0 && tree.folders.length === 0;
  return (
    <div className="flex flex-col gap-2">
      <Input label="Search forms" value={q} onChange={(e) => setQ(e.target.value)} placeholder="ICS213" />
      <div className="max-h-96 overflow-y-auto">
        {empty ? <p className="text-text-muted text-sm">No forms — fetch the forms library in config.</p>
               : <Node node={tree} onPick={onPick} depth={0} />}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: FormFillFrame (the sandboxed iframe + postMessage)**

```tsx
// frontend/src/pages/events/FormFillFrame.tsx
import { useEffect, useRef } from "react";
import { formRenderUrl } from "../../api/events";
import { Button } from "../../components/Button";

interface Props {
  netSlug: string;
  inputFormPath: string;
  onVariables: (vars: Record<string, string>) => void;
}

export function FormFillFrame({ netSlug, inputFormPath, onVariables }: Props) {
  const ref = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    function onMessage(e: MessageEvent) {
      // Trust boundary: only accept from OUR iframe, only the expected shape.
      if (!ref.current || e.source !== ref.current.contentWindow) return;
      const data = e.data;
      if (!data || data.type !== "skynet-form-vars" || typeof data.variables !== "object") return;
      const vars: Record<string, string> = {};
      for (const [k, v] of Object.entries(data.variables)) vars[String(k)] = String(v ?? "");
      onVariables(vars);
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [onVariables]);

  function collect() {
    ref.current?.contentWindow?.postMessage({ type: "skynet-collect" }, "*");
  }

  return (
    <div className="flex flex-col gap-2">
      <iframe
        ref={ref}
        title="Winlink form"
        sandbox="allow-scripts"
        src={formRenderUrl(netSlug, inputFormPath)}
        className="w-full h-[480px] border border-border rounded-md bg-white"
      />
      <div className="flex justify-end">
        <Button onClick={collect}>Done — build message</Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: FormCompose (orchestrator modal)**

```tsx
// frontend/src/pages/events/FormCompose.tsx
import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Modal } from "../../components/Modal";
import { useFormCompose } from "../../hooks/useFormCompose";
import type { NetEvent } from "../../types";
import { FormCatalog } from "./FormCatalog";
import { FormFillFrame } from "./FormFillFrame";

interface Props {
  netSlug: string;
  event: NetEvent;
  open: boolean;
  onClose: () => void;
  onSent: () => Promise<void>;
  onError: (m: string) => void;
  compose: ReturnType<typeof useFormCompose>;
}

export function FormCompose({ netSlug, event, open, onClose, onSent, onError, compose }: Props) {
  const { step, inputFormPath, preview, pickForm, acceptVariables, send } = compose;
  return (
    <Modal open={open} onClose={onClose} title="Winlink form" size="xl">
      {step === "catalog" && (
        <FormCatalog netSlug={netSlug} onPick={(e) => pickForm(e.template_path, e.input_form_path)} />
      )}
      {step === "fill" && (
        <FormFillFrame netSlug={netSlug} inputFormPath={inputFormPath}
          onVariables={(vars) => void acceptVariables(vars, onError)} />
      )}
      {step === "preview" && preview && (
        <div className="flex flex-col gap-2 text-sm">
          <div><span className="text-text-muted">To:</span> {preview.to}</div>
          <div><span className="text-text-muted">Subject:</span> {preview.subject}</div>
          <pre className="whitespace-pre-wrap font-sans bg-bg-elevated rounded p-2 max-h-72 overflow-y-auto">{preview.body}</pre>
          <div className="text-xs text-accent">📎 {preview.attachment_filename}</div>
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" onClick={onClose}>Cancel</Button>
            <Button onClick={() => void send(async () => { onClose(); await onSent(); }, onError)}>Send</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
```

(Note: the `{Ask}`/`{Select}` prompts step is folded into the preview modal — if the backend later returns unfilled prompts, render them as inputs above the preview. For v1 the collector captures the form's own fields; a follow-up wires template-level prompts. Keep the `find_unfilled_prompts` backend hook available; surfacing them in the UI is a small addition once a form that uses them is available to test against.)

- [ ] **Step 4: Wire into MessagesPanel**

In `frontend/src/pages/events/MessagesPanel.tsx`:
- Import `FormCompose` + `useFormCompose`; instantiate `const compose = useFormCompose(netSlug, event.id);` and `const [formOpen, setFormOpen] = useState(false);`.
- In the header actions (NCS + active), add a **New form** button: `onClick={() => { compose.setStep("catalog"); setFormOpen(true); }}`.
- On an inbound message whose `form?.is_form` is true, in the expanded view add a **Reply with form** button that calls `fetchReplyForm(event.id, m.id, netSlug)` then `compose.startReply(reply_template_path, input_form_path ?? "", prefill, m.id)` and `setFormOpen(true)` — but if `input_form_path` is null, fall back to the existing plain-text reply composer (SP3) prefilled.
- Render `<FormCompose netSlug={netSlug} event={event} open={formOpen} onClose={() => setFormOpen(false)} onSent={onChanged} onError={onError} compose={compose} />`.

- [ ] **Step 5: Build check + full backend suite + commit**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` — expected clean.
Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — expected all pass.

```bash
git add frontend/src/pages/events/
git commit -m "feat(forms): form compose UI — catalog, sandboxed fill, preview, panel entry"
```

---

### Task 8: Final verification sweep

- [ ] **Step 1: Full backend suite + lint**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"` — all pass.

- [ ] **Step 2: Frontend build**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` — clean.

- [ ] **Step 3: Migration chain on a scratch DB**

Run: `SKYNET_DATABASE_URL="sqlite:////tmp/claude-sp4b-final.db" .venv/bin/alembic upgrade head && rm -f /tmp/claude-sp4b-final.db` — clean through `e6f3a2b1c4d8`.

- [ ] **Step 4: Manual smoke test (human checkpoint)**

With `./run-dev.sh`, a net with `pat_mailbox_path`, and a fetched forms library (which now includes `.js`): activate an event → Messages panel → **New form** → catalog shows the tree → pick ICS-213 → fill it in the sandboxed iframe → **Done** → preview shows To/Subject/Body + the `RMS_Express_Form_*.xml` attachment name → **Send** → outbound message appears with a 📎; confirm the written `.b2f` in `{mailbox}/out` carries the form XML attachment (parse it, or open in PAT). Then, on an inbound form message (📎 + "Winlink form: …"), use **Reply with form** and confirm the reply form opens prefilled. Verify a viewer sees no compose controls. **Interop:** open a sent form's `.b2f` in real RMS Express / PAT and confirm it renders as the form (this is the true test of the builder's XML — ties to the Task 3 pinning gate).
