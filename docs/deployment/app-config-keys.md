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

Also reads `pat_mailbox_path` and `net_address` (above) — the Winlink backend writes outbound `.b2f` files into the PAT mailbox.

## Callbook lookup

For looking up callsign info during manual check-in entry (Add Check-in modal).

| Key | Used by | Type | Description |
|-----|---------|------|-------------|
| `callbook.providers` | Callbook service | JSON list of provider names | Providers to query in order. |
| `callbook.{name}.username` | Callbook service | string | Per-provider credentials (e.g., `callbook.qrz.username`). |
| `callbook.{name}.password` | Callbook service | string | Per-provider credentials. |

Supported provider names depend on `backend/integrations/callbook/service.py` — check there for the current list.

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
