# Settings reorganization

Date: 2026-06-29

## Problem

Three things are wrong with the current settings UX:

1. **Net-scoped fields live on the global ConfigPage.** Net Callsign, Net Winlink Address, PAT Mailbox Path, Auto-Scanner, and delivery routes (channel selection + per-channel destinations) are shown as global settings, but the backend already reads them from `net_config`. The global rows are dead data and the global UI is misleading on a multi-net install.
2. **Save-per-field UX.** Every field has its own Save button. Saving a callbook provider requires three clicks (Save providers, Save username, Save password). Same for groups.io, email, etc.
3. **Winlink-only assumption.** All "Net Operations" UI assumes a Winlink net. Voice nets, JS8Call mailbox nets, packet BBS nets, and multi-mode nets like PKTNET have no way to hide the irrelevant fields.

## Design

### Field placement

**Per-net (NetSettingsPage), always shown:**
- General: `name`, `slug`, `is_public`, `winlink_enabled` (new capability flag)
- Net Operations: `default_net_control` (Net Callsign)
- Delivery: `delivery.backends`, `delivery.email.to_address`, `delivery.groupsio.group_name`

**Per-net (NetSettingsPage), shown only when `winlink_enabled=true`:**
- Net Operations: `net_address` (Net Winlink Address)
- PAT: `pat_mailbox_path`, `scanner.enabled`, `scanner.interval_minutes`
- Delivery: `delivery.winlink.target_address` (also gated by `winlink` being in `delivery.backends`)
- The `winlink` option in the `delivery.backends` multiselect is itself hidden when `winlink_enabled=false`.

**Global (ConfigPage):**
- Auth: `registration_open` (new section)
- OAuth providers: unchanged (per-provider Save+Test)
- SMTP: existing SmtpForm (already single-save)
- Winlink Forms: unchanged
- Integrations: `claude_api_key`
- Delivery (global creds): `delivery.groupsio.api_key`
- Callbook: `callbook.providers`, `callbook.hamqth.{username,password}`, `callbook.qrz.{username,password}`

**Removed from ConfigPage entirely** (now per-net): `default_net_control`, `net_address`, `pat_mailbox_path`, `scanner.enabled`, `scanner.interval_minutes`, `delivery.backends`, `delivery.email.to_address`, `delivery.groupsio.group_name`, `delivery.winlink.target_address`.

### Capability flags

Net capability is modelled as boolean flags in the existing `net_config` kv table. No schema change. The flag for this iteration:

- `winlink_enabled` — when `false`, hides Winlink-specific fields listed above.

Future capability flags (`js8call_enabled`, `packet_bbs_enabled`, `voice_enabled`, …) follow the same pattern: each gates its own field set. A multi-mode net like PKTNET turns on whichever flags apply. Not implementing those flags now — designing only so adding them is a one-field-set-per-flag change.

### Save UX

One Save button per section, with section-level dirty tracking:

- Section is "dirty" if any field in it differs from its last saved value.
- Save button is `disabled` when clean, `primary` styled when dirty.
- On click: bulk-save all section fields in one transactional request.
- On success: snap saved-values to current values, green toast.
- On failure: leave current values dirty, red toast with error detail.

Per-section Test buttons remain where they exist today (groups.io test in the per-net Delivery section, SMTP test in SmtpForm). They use last-saved values; the section refuses to test when there are unsaved changes and prompts the user to save first.

OAuth providers keep their per-provider Save+Test layout — each provider is an independent entity, not a section.

### Backend

Add bulk-set endpoints so a section Save is one atomic request:

- `PUT /admin/config/bulk` — body `{"values": {"key": "value", ...}}` — transactional upsert into `app_config`.
- `PUT /admin/nets/{slug}/config/bulk` — same shape, scoped to a net's `net_config`.

Existing single-key endpoints (`PUT /admin/config/{key}`, `PUT /admin/nets/{slug}/config/{key}`) stay — OAuth provider rows and any backend callers still use them.

### Migration (Alembic)

One revision that:

1. For each net, for each key in the **moved-to-per-net** set listed above: if `net_config` has no row for `(net_id, key)`, insert one with the value from `app_config` (if `app_config` has a value).
2. For each net: insert `winlink_enabled=true` if not already present (preserves today's behavior; operators of non-Winlink nets opt out via the UI).
3. Delete those same keys from `app_config`.

Single-net deployments: no functional change — the values that were "global" are now seeded into the only net. Multi-net deployments: each net inherits the previously-global value, matching what the backend was already doing via fallback.

## Implementation order

1. Bulk-set backend endpoints + tests.
2. Alembic migration + tests.
3. NetSettingsPage: add `winlink_enabled` toggle, add Delivery section, gate Winlink-only fields. Switch to section-level Save.
4. ConfigPage: remove now-per-net field definitions, add Auth section for `registration_open`, switch remaining sections to section-level Save.
5. Smoke test in browser: single-net flow, multi-net flow with one net winlink-disabled.

## Out of scope

- Other capability flags (`js8call_enabled`, etc.) — designed-for but not implemented.
- Reorganizing OAuth providers UI (keeps per-provider pattern by user request).
- Changing how SMTP and Winlink Forms cards work internally.
- Any redesign of the global Delivery semantics beyond moving routes per-net and keeping `groupsio.api_key` global.
