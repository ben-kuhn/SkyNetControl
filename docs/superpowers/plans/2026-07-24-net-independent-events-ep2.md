# Net-Independent Events — EP2 (Frontend + Public View) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the event frontend off the EP1-deleted net-scoped routes onto top-level `/api/events`, add the net-independent Events home, per-event settings, global-defaults admin, and the anonymous public page — plus the backend deltas the public-view privacy model and per-event config require.

**Architecture:** Reuse-and-replumb the working event components: strip `netSlug`, swap `CurrentNetProvider`→`EventProvider` and `RequireNetRole`→`RequireEventRole`, lift routes out of `/nets/:slug/*` to top level. Backend adds event-config routes, a by-token public resolver, event-scoped forms catalog/render, and tightens message routes to CONTROL.

**Tech Stack:** FastAPI + SQLAlchemy (backend, pytest-covered); React 18 + TypeScript + Vite + react-router-dom (frontend, build-gated — no test harness).

## Global Constraints

- Backend changes are pytest-covered; run `.venv/bin/pytest -q`. Lint: `nix-shell --run "ruff check"` (line-length 120, select E/F) must pass.
- Frontend has **no test harness**; the gate is `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` (tsc typecheck + vite build) must pass with zero errors.
- Event auth model (EP1, unchanged): CONTROL = owner / co-operator / admin; READ = CONTROL + public-for-authenticated + anonymous-with-valid-`public_token`. 401 unauthenticated-on-control, 403 authenticated-non-control-on-private, 404 anonymous / bad-token / unknown (no enumeration signal).
- Secrets: `is_sensitive_key` fragments = `api_key`, `password`, `secret`, `token`. Config **reads mask sensitive values as `"***"`**; **writes encrypt** sensitive values via `secret_box.encrypt`. Never return a secret (even encrypted) to the frontend.
- PAT config UI: `pat_http_base_url` is the primary field; auth fields (`pat_http_auth_mode`/`username`/`password`/`token`) live behind an **Advanced** toggle, off by default, at both event and net scope; `pat_mailbox_path` retained alongside the HTTP keys.
- Public page shows event detail + map/positions + running log + weather only — **never** message content, enforced at the API.
- Existing owner-only routes already exist from EP1 (`POST`/`DELETE /{id}/operators`, `PATCH /{id}/visibility`, `POST /{id}/token/rotate`, `POST /{id}/transfer`) — the settings UI consumes them; no new backend for those.
- Keep the existing cursor-polling model (no WebSocket/SSE for event data).

---

## File Structure

**Backend (new/modified):**
- `backend/modules/events/config_routes.py` (new) — `GET`/`PUT` `/api/events/{id}/config`.
- `backend/modules/events/routes.py` (modify) — messages+attachment READ→CONTROL; add `GET /api/events/by-token/{token}`.
- `backend/modules/events/forms_routes.py` (new) — event-scoped `GET /api/events/{id}/forms/catalog` + `/render`.
- `backend/app.py` (modify) — mount the two new routers.
- Tests: `tests/test_event_config_routes.py`, `tests/test_event_message_gating.py`, `tests/test_event_by_token.py`, `tests/test_event_forms_routes.py` (new).

**Frontend (new):**
- `frontend/src/context/EventProvider.tsx` — event snapshot + `isControl` context.
- `frontend/src/components/RequireEventRole.tsx` — event-role route guard.
- `frontend/src/pages/events/EventSettingsPage.tsx` — settings.
- `frontend/src/pages/events/PublicEventPage.tsx` — `/e/:token`.
- `frontend/src/api/eventConfig.ts` — event-config API calls.

**Frontend (modified):** `api/events.ts`, `App.tsx`, `layouts/Sidebar.tsx`, `pages/ConfigPage.tsx`, `pages/events/EventsPage.tsx`, `pages/events/EventDashboardPage.tsx`, `pages/events/EventMapPage.tsx`, `pages/events/EventReportPage.tsx`, and the panels/hooks listed in Task 7. A net page gets the "New Event" shortcut (Task 8).

---

## BACKEND

### Task 1: Event-config routes (`GET`/`PUT /api/events/{id}/config`)

**Files:**
- Create: `backend/modules/events/config_routes.py`
- Modify: `backend/app.py` (mount router)
- Test: `tests/test_event_config_routes.py`

**Interfaces:**
- Consumes (EP1): `event_config_service.get_event_config`, `set_event_config_bulk` (or `set_event_config`); `event_auth.require_event_role`, `EventRole`, `EventContext`; `config_mgmt.service.is_sensitive_key`; `auth.secret_box.encrypt`.
- Produces: router `event_config_router` (prefix `/api/events`), routes `GET /{event_id}/config` → `dict[str,str]` (sensitive masked `***`), `PUT /{event_id}/config/bulk` (body `{"values": {k: v}}`) → `{"ok": true}`. Both `require_event_role(CONTROL)`.

First confirm the event-config service surface:

- [ ] **Step 1: Read the service to get exact names**

Run: `grep -n "def " backend/modules/events/event_config_service.py`
Expected: functions for get + bulk set. Use `get_event_config(db, event_id, key, default=None)` and the bulk setter (`set_event_config_bulk(db, event_id, values)` — if only a single `set_event_config(db, event_id, key, value)` exists, loop over it). Also find how to list all override rows for an event (an `EventConfig` query on `event_id`); if no list helper exists, query `EventConfig` directly.

- [ ] **Step 2: Write the failing test**

Create `tests/test_event_config_routes.py` (mirror the auth-fixture style of `tests/test_event_auth.py` — build an app, create an owner user + event, get an authed client). Include:

```python
import pytest


@pytest.mark.asyncio
async def test_get_config_masks_secrets(app_ctx):
    app, settings, ids = app_ctx
    eid = ids["event"]
    async with _control_client(app, settings) as c:  # owner/operator/admin
        # seed via PUT
        await c.put(f"/api/events/{eid}/config/bulk", json={"values": {
            "net_address": "W0EVT",
            "pat_http_password": "s3cret",
        }})
        r = await c.get(f"/api/events/{eid}/config")
        assert r.status_code == 200
        body = r.json()
        assert body["net_address"] == "W0EVT"
        assert body["pat_http_password"] == "***"  # masked, never plaintext


@pytest.mark.asyncio
async def test_put_encrypts_secret_at_rest_and_roundtrips(app_ctx, db_session):
    app, settings, ids = app_ctx
    eid = ids["event"]
    async with _control_client(app, settings) as c:
        await c.put(f"/api/events/{eid}/config/bulk", json={"values": {"pat_http_password": "s3cret"}})
    # stored value is encrypted (not the plaintext); get_event_config decrypts it back
    from backend.modules.events.event_config_service import get_event_config
    assert get_event_config(db_session, eid, "pat_http_password") == "s3cret"


@pytest.mark.asyncio
async def test_config_requires_control(app_ctx):
    app, settings, ids = app_ctx
    eid, pub = ids["event"], ids["public_event"]
    # authenticated non-control on a public event: 403 on read, 403 on write
    async with _other_client(app, settings) as c:
        assert (await c.get(f"/api/events/{pub}/config")).status_code == 403
        assert (await c.put(f"/api/events/{pub}/config/bulk", json={"values": {}})).status_code == 403
```

Adapt the fixture helpers (`_control_client`, `_other_client`, `app_ctx`, `db_session`, and the `ids` keys) to the actual conftest/`test_event_auth.py` patterns in this repo; if `test_event_auth.py` builds its clients inline, copy that exact approach rather than inventing fixtures.

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv/bin/pytest -q tests/test_event_config_routes.py`
Expected: FAIL (routes/404 — `event_config_router` not mounted yet).

- [ ] **Step 4: Implement the router**

Create `backend/modules/events/config_routes.py`:

```python
"""Per-event config: masked read, encrypting bulk write. CONTROL-gated.

NOTE: unlike the per-net config route (which encrypts at the route because
set_net_config does not), the EVENT config service ALREADY encrypts sensitive
values on write (event_config_service.set_event_config_bulk, line 37) and
decrypts on read (get_event_config, line 18). So this route passes PLAINTEXT to
the service and must NOT pre-encrypt — doing so would double-encrypt and corrupt
the round-trip. The GET still masks sensitive rows as "***" so ciphertext never
leaves the server."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session
from backend.config_mgmt.service import is_sensitive_key
from backend.modules.events.event_auth import EventContext, EventRole, require_event_role
from backend.modules.events.event_config_service import set_event_config_bulk
from backend.modules.events.models import EventConfig

event_config_router = APIRouter(prefix="/api/events", tags=["events", "config"])


class ConfigBulkBody(BaseModel):
    values: dict[str, str]


@event_config_router.get("/{event_id}/config")
async def get_event_config_route(
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
) -> dict[str, str]:
    rows = db.query(EventConfig).filter(EventConfig.event_id == ctx.event.id).all()
    # Mask sensitive values as "***" — never return secrets (even the ciphertext).
    return {r.key: ("***" if is_sensitive_key(r.key) and r.value else r.value) for r in rows}


@event_config_router.put("/{event_id}/config/bulk")
async def put_event_config_bulk_route(
    body: ConfigBulkBody,
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
) -> dict[str, bool]:
    # Pass plaintext straight through — the service encrypts sensitive keys itself.
    set_event_config_bulk(db, ctx.event.id, body.values)
    return {"ok": True}
```

`set_event_config_bulk(db, event_id, values)` and the `EventConfig` model
(`backend/modules/events/models.py:227`, composite PK `(event_id, key)`) are
confirmed to exist with these signatures.

Mount in `backend/app.py` near the other event routers (after `app.include_router(events_router)`):

```python
from backend.modules.events.config_routes import event_config_router
...
app.include_router(event_config_router)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/pytest -q tests/test_event_config_routes.py`
Expected: PASS.

- [ ] **Step 6: Lint + commit**

Run: `nix-shell --run "ruff check backend/modules/events/config_routes.py backend/app.py tests/test_event_config_routes.py"`
```bash
git add backend/modules/events/config_routes.py backend/app.py tests/test_event_config_routes.py
git commit -m "feat(events): per-event config routes (masked read, encrypting write, CONTROL)"
```

---

### Task 2: Tighten message routes to CONTROL + snapshot audit

**Files:**
- Modify: `backend/modules/events/routes.py` (message list route ~L698-702; attachment route ~L822-826)
- Test: `tests/test_event_message_gating.py`

**Interfaces:**
- Consumes: EP1 `require_event_role`, `EventRole.CONTROL`/`READ`, `_event_to_response` (detail helper, `routes.py:171` — already omits messages).
- Produces: `GET /{event_id}/messages` and `GET /{event_id}/messages/{message_id}/attachments/{attachment_id}` become CONTROL-gated. No new response shape.

- [ ] **Step 1: Write the failing test**

Create `tests/test_event_message_gating.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_messages_list_requires_control(app_ctx):
    app, settings, ids = app_ctx
    pub = ids["public_event"]  # visibility=public, has a public_token
    tok = ids["public_token"]
    # anonymous with valid token on a PUBLIC event: was 200 (READ) — must now be 404/403 (CONTROL)
    async with _anon_client(app, settings) as c:
        assert (await c.get(f"/api/events/{pub}/messages?token={tok}")).status_code in (401, 404)
    # authenticated non-control on public event: 403
    async with _other_client(app, settings) as c:
        assert (await c.get(f"/api/events/{pub}/messages")).status_code == 403
    # control user: still 200
    async with _control_client(app, settings) as c:
        assert (await c.get(f"/api/events/{pub}/messages")).status_code == 200


@pytest.mark.asyncio
async def test_read_snapshot_excludes_message_content(app_ctx):
    app, settings, ids = app_ctx
    pub, tok = ids["public_event"], ids["public_token"]
    async with _anon_client(app, settings) as c:
        detail = (await c.get(f"/api/events/{pub}?token={tok}")).json()
        upd = (await c.get(f"/api/events/{pub}/updates?token={tok}")).json()
    for body in (detail, upd):
        assert "messages" not in body  # message content never on the READ path
```

Use the same fixture approach as `test_event_auth.py` (which already sets up a public event + token — reuse those keys/helpers; `_anon_client` = no auth header).

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest -q tests/test_event_message_gating.py`
Expected: FAIL on `test_messages_list_requires_control` (anon token currently gets 200 under READ).

- [ ] **Step 3: Change the two gates**

In `backend/modules/events/routes.py`, the message-list route (~L702) and the attachment route (~L826):

```python
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
```
(both were `EventRole.READ`). Change only the `EventRole.READ` → `EventRole.CONTROL` on those two routes. Leave `/updates`, `/positions`, `/weather`, `/report`, `GET /{event_id}` as READ.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest -q tests/test_event_message_gating.py`
Expected: PASS. Then run the existing message/attachment suites to catch any test that assumed anonymous/READ message access:
Run: `.venv/bin/pytest -q tests/test_event_message_routes.py tests/test_event_message_attachments_api.py`
Expected: PASS (fix any test that relied on READ-level message access by switching it to a control client — that is the corrected contract).

- [ ] **Step 5: Lint + commit**

```bash
git add backend/modules/events/routes.py tests/test_event_message_gating.py
git commit -m "fix(events): message list + attachment download require CONTROL (no public message content)"
```

---

### Task 3: Anonymous by-token public resolver (`GET /api/events/by-token/{token}`)

**Files:**
- Modify: `backend/modules/events/routes.py` (add route; place it BEFORE `@events_router.get("/{event_id}")` so `by-token` isn't captured by the `{event_id}` path param — FastAPI matches in declaration order and `by-token` would otherwise 422 as an int path)
- Test: `tests/test_event_by_token.py`

**Interfaces:**
- Consumes: `Event`, `EventStatus`, `_event_to_response(db, event, ctx)` with `ctx=None` (anonymous, non-control → emits no token/operators/messages), `secrets.compare_digest`.
- Produces: `GET /api/events/by-token/{token}` → the non-control detail dict for a `public` event whose `public_token` matches; else 404.

- [ ] **Step 1: Write the failing test**

Create `tests/test_event_by_token.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_by_token_returns_public_snapshot(app_ctx):
    app, settings, ids = app_ctx
    tok = ids["public_token"]
    async with _anon_client(app, settings) as c:
        r = await c.get(f"/api/events/by-token/{tok}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == ids["public_event"]
        assert body["is_control"] is False
        assert "public_token" not in body
        assert "operators" not in body
        assert "messages" not in body


@pytest.mark.asyncio
async def test_by_token_404s_private_and_bad_token(app_ctx):
    app, settings, ids = app_ctx
    async with _anon_client(app, settings) as c:
        assert (await c.get("/api/events/by-token/does-not-exist")).status_code == 404
        # a PRIVATE event's token must not resolve
        assert (await c.get(f"/api/events/by-token/{ids['private_token']}")).status_code == 404
```

If the auth fixture doesn't already expose a private event's token, extend it to create one (private event with a known `public_token`).

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest -q tests/test_event_by_token.py`
Expected: FAIL (404 route not found / matched by `{event_id}`).

- [ ] **Step 3: Implement the route** (insert immediately before `@events_router.get("/{event_id}")`)

```python
import secrets


@events_router.get("/by-token/{token}")
async def get_event_by_token_route(
    token: str,
    db: Session = Depends(get_db_session),
):
    # Anonymous public resolver: turn a public_token into the read snapshot so the
    # /e/{token} page can bootstrap, then poll sub-resources by id with ?token=.
    ev = (
        db.query(Event)
        .filter(Event.visibility == "public")
        .filter(Event.public_token == token)
        .first()
    )
    # constant-time confirm (query already filtered; this guards against any driver quirk)
    if ev is None or not secrets.compare_digest(token.encode("utf-8"), ev.public_token.encode("utf-8")):
        raise HTTPException(status_code=404, detail="Event not found")
    return _event_to_response(db, ev, None)  # ctx=None -> non-control: no token/operators
```

Confirm the detail helper name is `_event_to_response` (routes.py:171). If `updates_route` uses a separate `_snapshot`, use whichever base helper omits messages — `_event_to_response` does.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest -q tests/test_event_by_token.py`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
git add backend/modules/events/routes.py tests/test_event_by_token.py
git commit -m "feat(events): anonymous by-token public resolver for /e/{token} bootstrap"
```

---

### Task 4: Event-scoped forms catalog + render

**Files:**
- Create: `backend/modules/events/forms_routes.py`
- Modify: `backend/app.py` (mount)
- Test: `tests/test_event_forms_routes.py`

**Interfaces:**
- Consumes (confirmed): `from backend.modules.forms.catalog import build_catalog` (`build_catalog(version: str) -> dict`), `from backend.modules.forms.serve import render_input_form` (`render_input_form(path, prefill=dict) -> html str`, raises `FileNotFoundError`/`ValueError` on a bad path), `from backend.config_mgmt.service import get_config_value`. These are the exact helpers `net_routes.py` calls — reuse them directly, no refactor needed. `require_event_role(CONTROL)`.
- Produces: `GET /api/events/{event_id}/forms/catalog?q=` (JSON tree) and `GET /api/events/{event_id}/forms/render?path=&prefill=` (`HTMLResponse` with the sandbox CSP), CONTROL-gated — same response shapes as the net versions.

- [ ] **Step 1: (context — helpers already identified)**

The shared helpers are importable (see Interfaces). The only net-route-local pieces are the tiny `_filter_tree(node, q)` helper and the `_SANDBOX_CSP` string (`net_routes.py:15-18, 42-48`) — copy those two verbatim into the new module (6 + 4 lines; not worth a shared-module refactor).

- [ ] **Step 2: Write the failing test**

Create `tests/test_event_forms_routes.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_event_forms_catalog_requires_control(app_ctx):
    app, settings, ids = app_ctx
    eid, pub = ids["event"], ids["public_event"]
    async with _control_client(app, settings) as c:
        assert (await c.get(f"/api/events/{eid}/forms/catalog")).status_code == 200
    async with _other_client(app, settings) as c:
        assert (await c.get(f"/api/events/{pub}/forms/catalog")).status_code == 403


@pytest.mark.asyncio
async def test_event_forms_render_returns_html(app_ctx):
    app, settings, ids = app_ctx
    eid = ids["event"]
    async with _control_client(app, settings) as c:
        # use a template path known to the net render test; if none, assert 404 for a bogus path
        r = await c.get(f"/api/events/{eid}/forms/render?path=__does_not_exist__")
        assert r.status_code in (200, 404)  # exercises the route wiring + auth, not template contents
```

Mirror whatever `tests/` already asserts for net forms render (find it: `grep -rl "forms/render\|forms/catalog" tests/`), reusing the same fixtures/paths.

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/pytest -q tests/test_event_forms_routes.py`
Expected: FAIL (route not mounted).

- [ ] **Step 4: Implement**

Create `backend/modules/events/forms_routes.py` (mirrors `net_routes.py:15-61` exactly, swapping net auth for event CONTROL):

```python
"""Event-scoped forms catalog + render — same on-disk templates as the net forms
routes (backend/modules/forms/net_routes.py), CONTROL-gated by event."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session
from backend.config_mgmt.service import get_config_value
from backend.modules.events.event_auth import EventContext, EventRole, require_event_role
from backend.modules.forms.catalog import build_catalog
from backend.modules.forms.serve import render_input_form

event_forms_router = APIRouter(prefix="/api/events", tags=["events", "forms"])

_SANDBOX_CSP = (
    "sandbox; default-src 'none'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; img-src data:; connect-src 'none'; form-action 'none'"
)


def _filter_tree(node: dict, q: str) -> dict:
    forms = [f for f in node["forms"] if q in f["name"].lower()]
    folders = [_filter_tree(sub, q) for sub in node["folders"]]
    folders = [f for f in folders if f["forms"] or f["folders"]]
    return {"name": node["name"], "folders": folders, "forms": forms}


@event_forms_router.get("/{event_id}/forms/catalog")
async def event_forms_catalog_route(
    q: str = Query(default=""),
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    version = get_config_value(db, "forms.library_version", "") or ""
    tree = build_catalog(version)
    if q:
        tree = _filter_tree(tree, q.strip().lower())
    return tree


@event_forms_router.get("/{event_id}/forms/render")
async def event_forms_render_route(
    path: str = Query(...),
    prefill: str = Query(default=""),
    ctx: EventContext = Depends(require_event_role(EventRole.CONTROL)),
    db: Session = Depends(get_db_session),
):
    prefill_dict: dict = {}
    if prefill:
        try:
            parsed = json.loads(prefill)
            if isinstance(parsed, dict):
                prefill_dict = parsed
        except (json.JSONDecodeError, ValueError):
            pass  # malformed prefill: treat as empty, do not 500
    try:
        html = render_input_form(path, prefill=prefill_dict)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Form not found")
    return HTMLResponse(content=html, headers={"Content-Security-Policy": _SANDBOX_CSP})
```

Mount in `app.py`:
```python
from backend.modules.events.forms_routes import event_forms_router
app.include_router(event_forms_router)
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest -q tests/test_event_forms_routes.py`
Expected: PASS. (No net-forms code changed — the shared helpers are imported, not modified — so the net forms suite is unaffected.)

- [ ] **Step 6: Lint + commit**

```bash
git add backend/modules/events/forms_routes.py backend/app.py tests/test_event_forms_routes.py
git commit -m "feat(events): event-scoped forms catalog + render (CONTROL)"
```

---

## FRONTEND

> The frontend gate for every task below: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` must pass (tsc + vite). There is no unit-test harness.

### Task 5: Re-plumb `api/events.ts` onto `/api/events` + add new API calls

**Files:**
- Modify: `frontend/src/api/events.ts`
- Create: `frontend/src/api/eventConfig.ts`

**Interfaces:**
- Produces: every event API function loses its `netSlug` parameter and targets `/events/...`; new PAT/forms calls target the event-scoped endpoints; new `eventConfig` module.

- [ ] **Step 1: Rewrite `api/events.ts` — drop `netSlug`, repoint at `/api/events`**

Transform every function by (a) removing the `netSlug` param and (b) replacing `` `/nets/${netSlug}/events/...` `` with `` `/events/...` ``. Endpoint map (EP1 surface, verified):

| Function | New path |
|---|---|
| `fetchEvents()` | `GET /events` |
| `createEvent(input)` | `POST /events` |
| `fetchEvent(id)` | `GET /events/${id}` |
| `updateEvent(id, body)` | `PATCH /events/${id}` |
| `activateEvent(id)` / `closeEvent` / `reopenEvent` | `POST /events/${id}/activate|close|reopen` |
| posts CRUD | `/events/${eventId}/posts[/${postId}]` |
| participants | `/events/${eventId}/participants[/${participantId}]` |
| log add/pin | `/events/${eventId}/log[/${entryId}]` |
| `fetchEventUpdates(id, since)` | `GET /events/${id}/updates?since=` |
| `fetchEventReport(id)` | `GET /events/${id}/report` |
| `fetchEventPositions(id, since)` | `GET /events/${id}/positions?since=` |
| `fetchEventMessages(id, since, includeDismissed)` | `GET /events/${id}/messages?...` |
| `composeEventMessage` / `setEventMessageStatus` / `rescanEventMailbox` | `/events/${id}/messages...`, `/events/${id}/rescan` |
| `eventAttachmentUrl(id, msgId, attId)` | `` `/api/events/${id}/messages/${msgId}/attachments/${attId}` `` |
| `previewForm` / `sendFormMessage` / `fetchReplyForm` | `/events/${id}/forms/preview`, `/events/${id}/form-messages`, `/events/${id}/messages/${msgId}/reply-form` |
| `fetchEventWeather(id)` | `GET /events/${id}/weather` |

PAT (now event-scoped — EP1 exposes `/api/events/{id}/pat/*`):
```ts
export async function fetchPatConnectOptions(eventId: number): Promise<PatConnectOptions> {
  return apiFetch<PatConnectOptions>(`/events/${eventId}/pat/connect-options`);
}
export async function patConnect(eventId: number, input: PatConnectInput): Promise<{ session_id: number }> {
  return apiFetch<{ session_id: number }>(`/events/${eventId}/pat/connect`, { method: "POST", body: JSON.stringify(input) });
}
export async function fetchPatSession(eventId: number, sessionId: number): Promise<PatSession> {
  return apiFetch<PatSession>(`/events/${eventId}/pat/sessions/${sessionId}`);
}
export async function abortPatSession(eventId: number, sessionId: number): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/events/${eventId}/pat/sessions/${sessionId}/abort`, { method: "POST" });
}
export async function testPatConnection(eventId: number): Promise<{ ok: boolean; error?: string }> {
  return apiFetch<{ ok: boolean; error?: string }>(`/events/${eventId}/pat/test`, { method: "POST" });
}
```
(The old `patConnect(netSlug, eventId|null, ...)` net/event branch collapses to the single event path.)

Forms catalog/render (now event-scoped — Task 4):
```ts
export async function fetchFormCatalog(eventId: number, q = ""): Promise<FormCatalogNode> {
  const p = q ? `?q=${encodeURIComponent(q)}` : "";
  return apiFetch<FormCatalogNode>(`/events/${eventId}/forms/catalog${p}`);
}
export function formRenderUrl(eventId: number, path: string, prefill?: Record<string, string>): string {
  let url = `/api/events/${eventId}/forms/render?path=${encodeURIComponent(path)}`;
  if (prefill && Object.keys(prefill).length > 0) url += `&prefill=${encodeURIComponent(JSON.stringify(prefill))}`;
  return url;
}
```

Add a token param for the public page's read calls (only the READ endpoints the public page uses need it). Give `fetchEvent`, `fetchEventUpdates`, `fetchEventPositions`, `fetchEventWeather` an optional trailing `token?: string` that appends `?token=` / `&token=`:
```ts
export async function fetchEventUpdates(id: number, since: number, token?: string): Promise<EventUpdates> {
  const t = token ? `&token=${encodeURIComponent(token)}` : "";
  return apiFetch<EventUpdates>(`/events/${id}/updates?since=${since}${t}`);
}
```
Add `fetchEventByToken`:
```ts
export async function fetchEventByToken(token: string): Promise<EventSnapshot> {
  return apiFetch<EventSnapshot>(`/events/by-token/${encodeURIComponent(token)}`);
}
```

Also add `activate` removal note: EP1's `createEvent` no longer takes `activate` on the backend model shape — keep `EventCreateInput` fields that the backend accepts (`name`, `event_type`, `description`, `scheduled_start`); drop `activate` if the backend `EventCreate` rejects it (verify against `backend/modules/events/routes.py` `EventCreate`).

- [ ] **Step 2: Create `frontend/src/api/eventConfig.ts`**

```ts
import { apiFetch } from "./client";

export async function fetchEventConfig(eventId: number): Promise<Record<string, string>> {
  return apiFetch<Record<string, string>>(`/events/${eventId}/config`);
}

export async function saveEventConfig(eventId: number, values: Record<string, string>): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/events/${eventId}/config/bulk`, {
    method: "PUT",
    body: JSON.stringify({ values }),
  });
}
```

- [ ] **Step 3: Typecheck (will surface every caller that still passes `netSlug`)**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`
Expected: FAIL with many "Expected N arguments, but got N+1" errors at call sites — these are the exact files Task 7 fixes. This is expected mid-migration; do NOT try to make the build green in this task. (If your reviewer needs a green gate per task, combine Tasks 5–7 into one commit boundary — see note below.)

- [ ] **Step 4: Commit (API layer only)**

```bash
git add frontend/src/api/events.ts frontend/src/api/eventConfig.ts
git commit -m "feat(events-ui): repoint events API at /api/events; add event config + by-token calls"
```

> **Reviewer note:** Tasks 5, 6, 7 are one atomic migration — the build is red between them. Implement 5→6→7 back-to-back and treat Task 7's green build as the gate for all three. Keep them as separate commits for bisectability, but do not dispatch a per-task reviewer expecting a green build until Task 7.

---

### Task 6: `EventProvider` + `RequireEventRole`

**Files:**
- Create: `frontend/src/context/EventProvider.tsx`
- Create: `frontend/src/components/RequireEventRole.tsx`

**Interfaces:**
- Produces: `useEvent()` → `{ event: EventSnapshot, isControl: boolean, reload: () => void }`; `<EventProvider>` (reads `:eventId` from the route, fetches `GET /events/{id}`); `<RequireEventRole min="read"|"control">`.

- [ ] **Step 1: Read the current net-context + guard to mirror their shape**

Run: `sed -n '1,80p' frontend/src/components/RequireNetRole.tsx` and find the `CurrentNetProvider` (grep it) — match their loading/error/redirect conventions and how they read route params.

- [ ] **Step 2: Implement `EventProvider.tsx`**

```tsx
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { fetchEvent } from "../api/events";
import type { EventSnapshot } from "../types";

interface EventCtx { event: EventSnapshot; isControl: boolean; reload: () => void; }
const Ctx = createContext<EventCtx | null>(null);

export function useEvent(): EventCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useEvent must be used within EventProvider");
  return v;
}

export function EventProvider({ children }: { children: React.ReactNode }) {
  const { eventId } = useParams();
  const id = Number(eventId);
  const [event, setEvent] = useState<EventSnapshot | null>(null);
  const [error, setError] = useState<number | null>(null);

  const reload = useCallback(() => {
    fetchEvent(id).then((e) => { setEvent(e); setError(null); })
      .catch((err) => setError(err?.status ?? 500));
  }, [id]);

  useEffect(() => { reload(); }, [reload]);

  if (error === 401 || error === 403 || error === 404) {
    return <div className="p-8 text-center text-slate-400">Event not available.</div>;
  }
  if (!event) return null; // or a Spinner, matching CurrentNetProvider
  return <Ctx.Provider value={{ event, isControl: Boolean(event.is_control), reload }}>{children}</Ctx.Provider>;
}
```
Match the repo's actual `Spinner`/loading and error-page conventions; confirm `EventSnapshot` has `is_control` (add it to the type if missing — it's in the EP1 response).

- [ ] **Step 3: Implement `RequireEventRole.tsx`**

```tsx
import { useEvent } from "../context/EventProvider";

export function RequireEventRole({ min, children }: { min: "read" | "control"; children: React.ReactNode }) {
  const { isControl } = useEvent();
  if (min === "control" && !isControl) {
    return <div className="p-8 text-center text-slate-400">You don't have control of this event.</div>;
  }
  return <>{children}</>;
}
```
(READ is already enforced by the backend on the `fetchEvent` in `EventProvider`; `RequireEventRole` only needs to gate control-only pages client-side. The backend remains the real gate.)

- [ ] **Step 4: Typecheck** — still red until Task 7 (expected). Commit:

```bash
git add frontend/src/context/EventProvider.tsx frontend/src/components/RequireEventRole.tsx
git commit -m "feat(events-ui): EventProvider + RequireEventRole (event-scoped context/guard)"
```

---

### Task 7: Migrate event pages, panels, and hooks off `netSlug`

**Files (modify — the ~18 coupled files):**
- Hooks: `useEventUpdates.ts`, `useEventPositions.ts`, `useEventMessages.ts`, `useEventWeather.ts`, `usePatSession.ts`, `useFormCompose.ts`
- Pages/panels: `EventDashboardPage.tsx`, `EventMapPage.tsx`, `EventReportPage.tsx`, `MessagesPanel.tsx`, `MessageComposer.tsx`, `MapPanel.tsx`, `PostsPanel.tsx`, `CheckInBar.tsx`, `PatConnectModal.tsx`, `FormCatalog.tsx`, `FormCompose.tsx`, `FormFillFrame.tsx`

**Interface:** every one of these currently gets `netSlug` (from `useParams().slug` or a prop). Replace with the event id from `useEvent()` (or the existing `:eventId` param) and drop `netSlug` from every API call.

- [ ] **Step 1: Enumerate the exact call sites**

Run: `cd frontend && grep -rn "netSlug\|useParams().slug\|slug =" src/pages/events src/hooks/useEvent* src/hooks/usePatSession.ts src/hooks/useFormCompose.ts`
Work the list top to bottom.

- [ ] **Step 2: Per-file transform (mechanical)**

For each file:
- Remove `netSlug` props/params and any `const { slug } = useParams()` used only for events.
- Where the file needs the event id, take it from `useEvent().event.id` (pages/panels under `EventProvider`) or the hook's existing `eventId` argument.
- Update each API call to the new signature from Task 5 (no `netSlug`).
- For PAT: `PatConnectModal`/`usePatSession` now call `patConnect(eventId, input)`, `fetchPatSession(eventId, sessionId)`, etc. (event id, not netSlug; no null-branch).
- For forms: `FormCatalog` → `fetchFormCatalog(eventId, q)`; `FormFillFrame`/`FormCompose` → `formRenderUrl(eventId, path, prefill)`.

Example — `useEventUpdates.ts` (before → after):
```ts
// before: useEventUpdates(eventId, netSlug)  ... fetchEventUpdates(eventId, sinceRef.current, netSlug)
// after:
export function useEventUpdates(eventId: number) {
  // ...
  const u = await fetchEventUpdates(eventId, sinceRef.current);
  // ...
}
```
Update its caller (`EventDashboardPage`) to `useEventUpdates(event.id)`.

- [ ] **Step 3: Green build (the gate for Tasks 5–7)**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`
Expected: PASS (zero tsc errors). Fix every remaining `netSlug` reference until clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/events frontend/src/hooks
git commit -m "refactor(events-ui): drop netSlug; event pages/panels/hooks use EventProvider"
```

---

### Task 8: Top-level routing + Sidebar nav + net "New Event" shortcut

**Files:**
- Modify: `frontend/src/App.tsx`, `frontend/src/layouts/Sidebar.tsx`, `frontend/src/layouts/MobileMenu.tsx` (if it duplicates the nav list), and one net page for the shortcut (e.g. `frontend/src/pages/SchedulePage.tsx` header — confirm the best host).

**Interfaces:**
- Produces: top-level routes `/events`, `/events/:eventId`, `/events/:eventId/map`, `/events/:eventId/report`, `/events/:eventId/settings`, and public `/e/:token`; a global "Events" sidebar item; removal of the per-net Events route/nav.

- [ ] **Step 1: App.tsx — add a top-level authed Events shell, remove under-net event routes**

Inside the top-level protected block (sibling to `/users`, `/config`, `/nets` — the `ProtectedRoute` group, NOT inside `/nets/:slug/*`), add:
```tsx
<Route path="/events" element={<ProtectedRoute><EventsPage /></ProtectedRoute>} />
<Route path="/events/:eventId" element={<ProtectedRoute><EventProvider><RequireEventRole min="read"><EventDashboardPage /></RequireEventRole></EventProvider></ProtectedRoute>} />
<Route path="/events/:eventId/map" element={<ProtectedRoute><EventProvider><RequireEventRole min="read"><EventMapPage /></RequireEventRole></EventProvider></ProtectedRoute>} />
<Route path="/events/:eventId/report" element={<ProtectedRoute><EventProvider><RequireEventRole min="read"><EventReportPage /></RequireEventRole></EventProvider></ProtectedRoute>} />
<Route path="/events/:eventId/settings" element={<ProtectedRoute><EventProvider><RequireEventRole min="control"><EventSettingsPage /></RequireEventRole></EventProvider></ProtectedRoute>} />
```
Add the public route OUTSIDE every auth wrapper (sibling to `/login`, `/recovery`):
```tsx
<Route path="/e/:token" element={<PublicEventPage />} />
```
Remove the four `RequireNetRole`-wrapped `events*` routes under `/nets/:slug/*` (App.tsx:144-147) and the slug-less `/events` `SlugRedirect` alias (App.tsx:158). Add imports for `EventProvider`, `RequireEventRole`, `EventSettingsPage`, `PublicEventPage`.

- [ ] **Step 2: Sidebar.tsx — move Events to a global (non-admin) group**

Remove `{ label: "Events", subpath: "events", minRole: "viewer" }` from `netNavItems` (Sidebar.tsx:23). Add a global item rendered for any authenticated approved user (not admin-gated). The current `globalNavItems` are admin-only; introduce an Events entry that renders whenever `user` is present and approved:
```tsx
// Rendered above the admin divider, gated on an authenticated non-pending user:
{ label: "Events", absolutePath: "/events", minRole: null as const },
```
Wire its visibility to "logged-in and approved" rather than net role (follow how the sidebar already distinguishes `absolutePath` items; render this one when `user && !user.is_pending` — match the real user shape). Mirror the change in `MobileMenu.tsx` if it keeps its own list.

- [ ] **Step 3: Net "New Event" shortcut**

On a net page header (Schedule or the net home — pick the one an NCS lands on), add a link/button:
```tsx
import { Link } from "react-router-dom";
// ...
<Link to="/events?new=1" className="...btn-secondary...">New Event</Link>
```
`EventsPage` (Task 9) reads `?new=1` to auto-open its create form. The created event is net-independent; the shortcut is only an entry point.

- [ ] **Step 4: Green build**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/layouts/Sidebar.tsx frontend/src/layouts/MobileMenu.tsx frontend/src/pages/SchedulePage.tsx
git commit -m "feat(events-ui): top-level Events routes + global nav + net New Event shortcut; remove under-net events"
```

---

### Task 9: `EventsPage` — top-level list + create

**Files:** Modify `frontend/src/pages/events/EventsPage.tsx`

- [ ] **Step 1: Read the current page**

Run: `sed -n '1,200p' frontend/src/pages/events/EventsPage.tsx`
Note how it currently lists/creates (it took `netSlug`).

- [ ] **Step 2: Rewrite for net-free operation**

- Load via `fetchEvents()` (no arg) — this is the "mine" list (owner/co-operator).
- Create via `createEvent(input)` and navigate to `/events/${created.id}`.
- Link each row to `/events/${id}`.
- Read `useSearchParams()` `new=1` → open the create form on mount (the Task 8 shortcut).
- Optionally show the public directory (`GET /events/public`) as a secondary "Active public events" list — only if `fetchEvents` doesn't already cover the need; keep it minimal (YAGNI).

- [ ] **Step 3: Green build + commit**

```bash
cd frontend && nix-shell -p nodejs_22 --run "npm run build"
```
```bash
git add frontend/src/pages/events/EventsPage.tsx
git commit -m "feat(events-ui): top-level Events list + create"
```

---

### Task 10: Event settings page

**Files:** Create `frontend/src/pages/events/EventSettingsPage.tsx`

**Interfaces:** Consumes `useEvent()`, `fetchEventConfig`/`saveEventConfig` (Task 5), and EP1 owner-only API calls (add thin wrappers in `api/events.ts` if missing: `addOperator(eventId, callsign)` → `POST /events/${id}/operators`, `removeOperator(eventId, callsign)` → `DELETE`, `setEventVisibility(eventId, visibility)` → `PATCH /events/${id}/visibility`, `rotatePublicToken(eventId)` → `POST /events/${id}/token/rotate`, `transferEvent(eventId, callsign)` → `POST /events/${id}/transfer`, `deleteEvent(eventId)` → `DELETE /events/${id}`).

- [ ] **Step 1: Add the owner-action API wrappers to `api/events.ts`** (list above), then build to typecheck them.

- [ ] **Step 2: Read `NetSettingsPage.tsx` to reuse its section/field pattern**

Run: `sed -n '1,120p' frontend/src/pages/NetSettingsPage.tsx` and note `SettingsSection`, `ConfigField`, the field-group render, and the Advanced/`visibleWhen` mechanism (it already gates PAT auth fields via `visibleWhen`).

- [ ] **Step 3: Implement the page — stacked sections**

Sections (reusing `SettingsSection` + the config-field renderer from `NetSettingsPage`; `isOwner = event.owner === currentUser.callsign || currentUser.is_admin`):
- **General** (control): name, description, scheduled_start → `updateEvent`.
- **Visibility & public link** (owner only): a `private`|`public` control → `setEventVisibility`; when public, show `${location.origin}/e/${event.public_token}` with a Copy button and a **Rotate link** button → `rotatePublicToken` then `reload()`.
- **Config** (control): fields for `net_address`, `aprs.*`, `weather.*`, `pat_http_base_url`, `pat_mailbox_path`, and an **Advanced** toggle (reuse the `visibleWhen: (v) => v["pat_advanced"] === "true"` pattern, or a local `showAdvanced` state) revealing `pat_http_auth_mode` + `pat_http_username`/`pat_http_password`/`pat_http_token`. Load current values with `fetchEventConfig` (secrets arrive as `***`; leaving a `***` field untouched must NOT overwrite — only send changed fields), save with `saveEventConfig`.
- **Co-operators** (owner only): list `event.operators`, add-by-callsign → `addOperator` + `reload`, remove → `removeOperator` + `reload`.
- **Danger zone** (owner only): transfer ownership (callsign input → `transferEvent`), delete event (confirm → `deleteEvent` → navigate `/events`).

Non-owner operators: hide the owner-only sections (render them only when `isOwner`).

Secrets handling detail: initialize each secret field empty with a "leave blank to keep" placeholder when the loaded value is `***`; include a key in the PUT only when the operator typed a new value.

- [ ] **Step 4: Green build + commit**

```bash
cd frontend && nix-shell -p nodejs_22 --run "npm run build"
```
```bash
git add frontend/src/pages/events/EventSettingsPage.tsx frontend/src/api/events.ts
git commit -m "feat(events-ui): per-event settings page (config, visibility+link, co-ops, danger zone)"
```

---

### Task 11: Global-defaults sections on `ConfigPage`

**Files:** Modify `frontend/src/pages/ConfigPage.tsx`

- [ ] **Step 1: Read the current ConfigPage section/field pattern**

Run: `sed -n '1,230p' frontend/src/pages/ConfigPage.tsx`
Note the field descriptor shape and `handleSectionSave` (it already PUTs to `/config/bulk`).

- [ ] **Step 2: Add sections writing global AppConfig (no backend change)**

Add `SettingsSection`s (following the file's existing pattern) for:
- **PAT defaults**: `pat_http_base_url`, `pat_mailbox_path`, plus an **Advanced** toggle revealing `pat_http_auth_mode` + `pat_http_username`/`pat_http_password`/`pat_http_token` (same Advanced pattern as NetSettingsPage/EventSettingsPage).
- **APRS defaults**: `aprs.server`, `aprs.port`.
- **Weather defaults**: the weather host / NWS keys (`weather.nws_contact`, any host key — confirm names in `backend/integrations/weather/service.py`).
- **Delivery defaults**: the delivery keys the events path reads (confirm the exact keys in `backend/integrations/delivery/service.py`; e.g. `delivery.backends`, `delivery.email.*`).

These save through the existing `/config/bulk` route (already accepts arbitrary keys; encrypts sensitive). Sensitive fields render masked (`***` from the GET) with the same "leave blank to keep" handling.

- [ ] **Step 3: Green build + commit**

```bash
cd frontend && nix-shell -p nodejs_22 --run "npm run build"
```
```bash
git add frontend/src/pages/ConfigPage.tsx
git commit -m "feat(config-ui): global PAT/APRS/weather/delivery defaults for net-free events"
```

---

### Task 12: Public event page `/e/:token`

**Files:** Create `frontend/src/pages/events/PublicEventPage.tsx`

**Interfaces:** Consumes `fetchEventByToken(token)` (Task 5) + the token-aware read calls (`fetchEventUpdates(id, since, token)`, `fetchEventPositions(id, since, token)`, `fetchEventWeather(id, token)`), and the read-only map/log/weather presentational components.

- [ ] **Step 1: Identify the read-only presentational pieces**

The dashboard's map (`MapPanel`/`EventMap`), the log view, and the weather overlay must render without control affordances. If those components currently assume `useEvent()`/control, pass the event + data as props in read-only mode, or guard control UI behind `isControl` (already the case after Task 7). Prefer feeding them data via props on the public page rather than mounting `EventProvider` (the public page has a token, not an authed session).

- [ ] **Step 2: Implement the page**

```tsx
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { fetchEventByToken, fetchEventUpdates, fetchEventPositions, fetchEventWeather } from "../../api/events";
import type { EventSnapshot } from "../../types";

export function PublicEventPage() {
  const { token } = useParams();
  const [event, setEvent] = useState<EventSnapshot | null>(null);
  const [notFound, setNotFound] = useState(false);
  // ... log/positions/weather state + since cursors (mirror the dashboard hooks, but pass token)

  useEffect(() => {
    if (!token) return;
    fetchEventByToken(token).then(setEvent).catch(() => setNotFound(true));
  }, [token]);

  // poll updates/positions/weather with the token on an interval, same cadence as the dashboard,
  // holding last-known on a failed poll (never blank mid-event).

  if (notFound) return <div className="min-h-screen grid place-items-center text-slate-400">Event not found.</div>;
  if (!event) return null;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="p-4 border-b border-slate-800">
        <h1 className="text-lg font-semibold">{event.name}</h1>
        <p className="text-sm text-slate-400">{event.status}</p>
      </header>
      {/* Live map + positions, weather overlay, running log — READ ONLY. No messages panel. */}
    </div>
  );
}
```
No sidebar/AppShell chrome. **No messages panel** — do not call `fetchEventMessages` here (it's CONTROL-gated and would 403/404 anyway). Reuse the same poll cadence and "hold last-known on failure" as the dashboard hooks.

- [ ] **Step 3: Green build + commit**

```bash
cd frontend && nix-shell -p nodejs_22 --run "npm run build"
```
```bash
git add frontend/src/pages/events/PublicEventPage.tsx
git commit -m "feat(events-ui): anonymous public event page /e/:token (map+log+weather, no messages)"
```

---

### Task 13: Final verification sweep

**Files:** none (verification + docs).

- [ ] **Step 1: Full backend suite + lint**

Run: `.venv/bin/pytest -q && nix-shell --run "ruff check"`
Expected: all pass (EP1's 1542 + the new EP2 backend tests).

- [ ] **Step 2: Frontend build**

Run: `cd frontend && nix-shell -p nodejs_22 --run "npm run build"`
Expected: clean tsc + vite build.

- [ ] **Step 3: Grep-guard — no residual net-scoped event coupling in the frontend**

Run: `cd frontend && grep -rn "nets/.*events\|netSlug" src/api/events.ts src/pages/events src/hooks/useEvent* | grep -v "//"`
Expected: NO matches (all event calls are `/api/events`; no `netSlug` left in the event UI).

- [ ] **Step 4: Update deployment docs**

Add the new global-config keys (PAT/APRS/weather/delivery defaults) and the `/e/{token}` public page to `docs/deployment/app-config-keys.md` if they're not already documented as net keys; note they now double as global defaults.

- [ ] **Step 5: Manual smoke checklist (record in the PR/merge notes; needs a running stack)**

Create an event from the top-level Events section AND from the net "New Event" shortcut. Run it as owner (check-in, log, map, messages, PAT connect if hardware). Add a co-operator; confirm they get control and can't see owner-only sections until made owner. Toggle the event public; open `/e/{token}` in a logged-out browser → map/log/weather visible, **no messages**. Rotate the token → old link 404s. Set a global default in `/config`; create a fresh event → confirm it inherits the default. Set a per-event override → confirm it wins.

- [ ] **Step 6: Commit docs**

```bash
git add docs/deployment/app-config-keys.md
git commit -m "docs(events): EP2 config keys + public page"
```

---

## Notes for the executor

- **Backend before frontend.** Tasks 1–4 land the endpoints the frontend calls; do them first so the frontend has real routes.
- **Tasks 5–7 are one red-to-green migration** (build is broken between them). Implement back-to-back; Task 7's green build gates all three.
- **Secrets never overwritten by `***`.** In both the event settings and global-config UIs, a masked (`***`) field left untouched must be omitted from the PUT — only send fields the operator actually changed.
- **Verify names before coding**, per the `grep`/`sed` steps: the event-config service's bulk setter, the `EventConfig` model import path, the net forms shared helpers, and the delivery/weather global key names. The plan cites likely names; confirm against the tree.
