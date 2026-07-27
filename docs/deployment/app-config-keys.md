# App configuration keys

SkyNetControl has two layers of configuration:

1. **Environment variables** (`SKYNET_*`) — bootstrap-only: database URL, JWT secret, app base URL, optional secrets key, trusted proxies. Documented in [secrets.md](secrets.md) and [operations.md](operations.md).
2. **App configuration** (DB-stored key/value) — everything else. OAuth providers, SMTP, net basics, scanner, delivery routing, integration API keys. Edited through the in-app `/config` page (admin only), the first-boot wizard at `/setup`, or via `PUT /api/config/{key}`. Sensitive values (anything matching `api_key`/`password`/`secret`/`token`) are encrypted at rest under `SKYNET_SECRETS_KEY` (or the JWT secret fallback) and masked as `"***"` on GET.

This doc lists every key the codebase reads from layer 2, where it's used, and what value format the code expects.

## Core operational keys

| Key | Used by | Type | Description |
|-----|---------|------|-------------|
| `default_net_control` | Schedule | callsign string | Default NCS assigned to new sessions when not set per-session. |
| `pat_mailbox_path` | Check-ins, Winlink delivery, Scanner | filesystem path | Directory where PAT stores Winlink messages. |
| `net_address` | Check-ins, Winlink delivery, Scanner | email string | Winlink address for the net (e.g., `w0ne@winlink.org`). |
| `claude_api_key` | Activities (chat) | API key string (encrypted) | Anthropic API key for the activity-brainstorm chat. If unset, the chat returns 503 and the UI shows a banner. |
| `registration_open` | OAuth callback | `"true"` / `"false"` | Default `"true"`. When `"false"`, the callback refuses new OAuth subjects (existing users keep signing in). Toggle from the **Net Operations** group on `/config`. |

## Check-in modes

| Key | Used by | Type | Description |
|-----|---------|------|-------------|
| `checkins.modes` | Check-ins | JSON list of strings | Modes shown in the Add Check-in dropdown and recognized by the message parser. If unset, falls back to a built-in default list ("Voice", "Winlink", "VARA FM", etc.). |

Example value: `["Voice", "Winlink", "VARA FM", "VARA HF"]`

## Delivery backends

The reminder and roster `mark_sent` flows dispatch to whichever backends are listed here.

| Key | Used by | Type | Description |
|-----|---------|------|-------------|
| `delivery.backends` | Delivery service | JSON list of backend names | Which backends to dispatch to. Empty list = no delivery. |

Supported backend names: `"email"`, `"groupsio"`, `"winlink"`.

### Email backend

Connection details live in `app_config` under the `smtp.*` keys (host / port / username / password / from_address / use_tls), configured via the SMTP form on `/config`. `smtp.password` is encrypted at rest. This `delivery.email.to_address` key sets the recipient.

| Key | Type | Description |
|-----|------|-------------|
| `delivery.email.to_address` | email string | Target address (typically the net's group list). |

### Groups.io backend

| Key | Type | Description |
|-----|------|-------------|
| `delivery.groupsio.api_key` | API key string | Groups.io API token. |
| `delivery.groupsio.group_name` | string | Groups.io group identifier. |

### Winlink backend

| Key | Type | Description |
|-----|------|-------------|
| `delivery.winlink.target_address` | email string | Winlink address to send to (e.g., the group's Winlink alias). |

Also reads `net_address` (above). The backend has two transport modes:

- **File handoff (default).** Reads `pat_mailbox_path` and writes outbound `.b2f` files into `{pat_mailbox_path}/out/`; PAT sends them on its own schedule. Inbound is polled from `{pat_mailbox_path}/in/` by the scanner. Requires the app to share PAT's mailbox filesystem.
- **PAT HTTP transport.** When `pat_transport_enabled` is on (see below), the backend instead posts outbound messages to PAT's HTTP API and the scanner fetches inbound over HTTP — no shared mailbox filesystem is needed, so PAT can run on a separate host. Outbound is marked `queued` until an operator-driven connect confirms it left PAT's outbox, at which point it flips to `sent`.

### PAT HTTP transport

Drives PAT (the Winlink client) over its built-in HTTP API instead of the file handoff, and adds operator-triggered radio connections with a live session log (the **Connect (PAT)** control in an event's Messages panel — NCS only). All keys are **per-net with a global fallback**: the global values are set on the `/config` admin page (key name as shown below) and are inherited by any net that has no per-net override, **including net-free events** created from the top-level Events section. Per-net overrides are set from the **PAT transport** section of a net's settings page. When `pat_transport_enabled` is off (default), behavior is unchanged from the file handoff above.

| Key | Type | Description |
|-----|------|-------------|
| `pat_transport_enabled` | `"true"` / `"false"` | Default `"false"`. When true (and a base URL is set), outbound/inbound use PAT's HTTP API and the Connect control is available. |
| `pat_http_base_url` | URL string | PAT's HTTP endpoint, e.g. `http://shack:8080`. Reach it over a trusted network (VPN/LAN) or a reverse proxy — PAT's own API is unauthenticated. |
| `pat_http_auth_mode` | `none` / `basic` / `token` | Default `none`. Use `basic` or `token` when PAT is fronted by an authenticating reverse proxy. |
| `pat_http_username` | string | Username for `basic` auth. |
| `pat_http_password` | string (encrypted) | Password for `basic` auth. Encrypted at rest; write-only in the UI (blank leaves it unchanged). |
| `pat_http_token` | string (encrypted) | Bearer token for `token` auth. Encrypted at rest; write-only. |
| `pat_http_timeout_seconds` | integer | Default `15`. Per-request timeout for ordinary PAT calls (a radio connect uses a longer session timeout). |

Connect method: the operator picks a saved PAT **connect alias** (read from PAT's config over the API) or builds one from mode + gateway + optional frequency. Use the **Test connection** button on the net settings page to confirm the endpoint is reachable.

## Callbook lookup

For looking up callsign info during manual check-in entry (Add Check-in modal).

| Key | Used by | Type | Description |
|-----|---------|------|-------------|
| `callbook.providers` | Callbook service | JSON list of provider names | Providers to query in order. |
| `callbook.{name}.username` | Callbook service | string | Per-provider credentials (e.g., `callbook.qrz.username`). |
| `callbook.{name}.password` | Callbook service | string | Per-provider credentials. |

Supported provider names depend on `backend/integrations/callbook/service.py` — check there for the current list.

## Weather overlay

Per-net weather layers on the live event map, for emergency/skywarn nets: an animated precipitation-radar loop (RainViewer) and active NWS watch/warning polygons. Configured from the **Weather overlay** section of a net's settings page. All keys are **per-net with a global fallback** (global values on `/config` are inherited by any net or net-free event that has no per-net override); default off is a zero-cost no-op (no layers render, no external calls). No API key is needed — radar and NWS alerts are both free/keyless, so these keys are stored plaintext.

| Key | Type | Description |
|-----|------|-------------|
| `weather.enabled` | `"true"` / `"false"` | Default `"false"`. Master switch. When on, the map layer control gains "Weather: Radar" and "Weather: Warnings" toggles (off by default, operator-toggled) and the backend polls NWS alerts. |
| `weather.alert_states` | JSON list of 2-letter state codes (optional) | Explicit NWS coverage area, e.g. `["MN","WI"]`. If unset, the coverage state is auto-derived from the event location (its posts' centroid / APRS range center) via one NWS `/points` lookup. |
| `weather.nws_contact` | string (optional) | Contact embedded in the NWS API `User-Agent` (`SkyNetControl (<contact>)`), per NWS etiquette. Defaults to `net_address` if unset. |

- **Radar** is fetched client-side directly from RainViewer (no backend involvement, no key); a RainViewer outage only blanks the radar layer.
- **Warnings** are proxied by the backend (`GET /api/nets/{slug}/events/{id}/weather`), which fetches `api.weather.gov/alerts/active` for the coverage area with a short shared cache (~60s) and degrades gracefully — an NWS problem surfaces as a status chip (`stale`/`unavailable`), never an error. US-only (NWS); radar is global.
- Both layers are viewer-visible (read-only) on active events.

## Global config defaults for net-free events (EP2)

Starting with the live-events feature (EP2), events can be created independently of any net (from the top-level **Events** section). These "net-free" events inherit their PAT transport, weather overlay, APRS, and delivery settings from the **global** config values — i.e., the same `pat_transport_enabled`, `pat_http_base_url`, `weather.enabled`, etc. keys documented above, but set on the `/config` admin page rather than on a per-net settings page.

Per-event overrides (set from the event's Settings panel) always win over the global defaults.

## Anonymous public event page

When an operator marks an event **public**, a read-only viewer page becomes available at:

```
/e/{public_token}
```

where `{public_token}` is a random UUID generated on first publish and stored in `events.public_token`. The page shows the live map, activity log, and weather layers. **Messages are never shown** on the public page, regardless of the event's message visibility setting.

The public link requires no login and can be shared freely. Operators can **rotate** the token (generating a fresh UUID) from the event's Settings panel; the old URL immediately returns 404. The token survives net reassignment and event renames.

To find the current public URL for an event, read `GET /api/events/{id}` → `public_token` field, then construct `/e/{public_token}`.

## Editing values

**Via the UI:** Sign in as an admin and visit `/config`. Each key/value pair is editable; new keys can be added.

**Via the API:**

```bash
# Set a value
curl -X PUT https://your-host/api/config/net_address \
  -H "Authorization: Bearer <PAT>" \
  -H "Content-Type: application/json" \
  -d '"w0ne@winlink.org"'

# List all values
curl https://your-host/api/config/ \
  -H "Authorization: Bearer <PAT>"
```

(Or use cookie auth from a signed-in session.)

## Discovering new keys

This list reflects the keys the code reads as of the last update to this doc. To find anything added since, grep the backend:

```bash
grep -rn "get_config_value\b" backend/ | grep -v __pycache__
```

Each `get_config_value(db, "<key>")` call is a place the app reads a config value.
