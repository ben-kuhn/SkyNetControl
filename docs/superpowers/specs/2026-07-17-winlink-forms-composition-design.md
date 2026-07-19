# Winlink Forms Composition Design

**Date:** 2026-07-17
**Status:** Approved for planning

## Context

This is **SP4b**, the user-facing half of the forms work (SP4 of live events). SP4a
(Winlink attachments + inbound form capture — the B2F attachment codec, inbound
attachment extraction, `find_form_xml`, and received-form metadata surfaced on event
messages) is merged and is the foundation SP4b builds on.

SP4b lets NCS **author and send arbitrary Winlink standard forms** (ICS-213, damage
reports, hospital bed counts, regional agency forms) during an event, and **reply to a
received form** in kind. The engine is PAT-faithful: the form's own HTML+JS runs in a
locked-down sandboxed iframe in the operator's browser and hands back the computed
variables; the server does no-JS template→message composition and generates the
`RMS_Express_Form` XML attachment.

Binding decisions from the SP4 and SP4b Q&A:

- **Sandbox model A:** the form's real JS runs in `<iframe sandbox="allow-scripts">` (NO
  `allow-same-origin`) → opaque origin, no cookies/API/localStorage/network access;
  variables leave via `postMessage`. The user signed off on running internet-sourced
  form JS in this contained posture.
- **Catalog:** browsable folder tree + search, mirroring the on-disk library hierarchy —
  as every Winlink client presents it.
- **Event-scoped:** composition plugs into SP3's Messages panel and produces an outbound
  `EventMessage` via SP3's delivery path (with SP4a's attachment codec).
- **True B2F attachment:** the `RMS_Express_Form_<name>.xml` rides as a real attachment
  (SP4a codec), so receiving Winlink clients render it as the form.
- **Preview step KEPT:** fill → (prompts) → server-built preview → send. One
  confirmation beyond a bare Winlink client, because the message goes out under the net
  callsign to served agencies.
- **Persist template + variables + datetime (rebuild XML deterministically):** a
  companion `event_message_forms` row stores `template_path` + `variables` JSON +
  `datetime_stamp`; the message + XML are rebuilt deterministically for send/retry/
  display.
- **Reply-with-form (A):** open the inbound form's `reply_template` input form prefilled
  from the sender's variables (best-effort name match); fall back to a prefilled
  plain-text reply when the reply template has no fillable input form.
- **Offline-only sandbox:** `connect-src 'none'` — no network in the sandbox. Live-data
  "Web Services" forms are a future enhancement.
- **Fetch keeps `.js`:** the forms-fetch allowlist is widened to retain `.js`; the
  receive-side viewer stays script-stripped (only the sandboxed compose iframe serves
  scripts).
- **Builder = PAT `scanAndBuild` + `buildXML` port**, deterministic, pinned against a
  real captured `RMS_Express_Form` message.
- The newer JSON form format is a future enhancement (not built).

## Requirements

- Browse/search the fetched Standard Forms library and pick a composable form.
- Fill the form's real HTML+JS in a locked sandboxed iframe; collect the computed
  variables via a postMessage handshake the parent validates.
- Surface any `{Ask}`/`{Select}` template prompts as native fields.
- Server-side deterministic build: template→message (control lines, `<Var>`, insertion
  tags auto-filled from net+event context, `Def:`) + `RMS_Express_Form` XML attachment.
- Preview the composed message before send; send produces an outbound `EventMessage`
  threaded in the panel with the XML attached, delivered under the net callsign.
- Reply-with-form: open the reply template prefilled from the inbound form's variables;
  fall back to a prefilled plain-text reply when no input form exists.
- Retry re-runs the deterministic rebuild (byte-stable).
- NCS + active event only; viewers never see compose controls.
- Winlink/form failures never affect event operation; untrusted JS is contained.

## Architecture

### Fetch change + catalog

- **`fetch.py` allowlist gains `.js`** (alongside `.html/.htm/.txt/.xml/.css`).
  Composition needs the forms' bundled scripts. All other fetch guards (zip-slip, SSRF,
  size/entry caps, atomic promotion) unchanged. The receive-side viewer (`render.py`)
  stays script-stripped. A re-fetch after deploy re-populates `.js`.
- **`backend/modules/forms/catalog.py`:** `build_catalog() -> FormFolder` walks
  `${STATEDIR}/forms/` into a nested tree (folders + forms) mirroring the on-disk
  hierarchy. A "composable" form is a `.txt` template referencing a fillable input HTML;
  display-only/reply-only templates are excluded from the composable catalog (still
  resolvable for reply-with-form). Each entry: display name, template path, input-form
  path. Computed on request, cached in-process keyed by `forms.library_version`,
  invalidated on re-fetch.
- **Route:** `GET /api/nets/{slug}/forms/catalog?q=` (net member) → the tree, optionally
  name-filtered.

### Sandboxed form-serve + collector handshake

- **Serve route:** `GET /api/nets/{slug}/forms/render?path=<input-html>` (net_control).
  Realpath-guarded under the forms dir (traversal → 404). Reads the input HTML, injects
  a small **collector shim** `<script>` before `</body>`, and returns `text/html` with a
  restrictive response CSP: `sandbox; default-src 'none'; script-src 'unsafe-inline';
  style-src 'unsafe-inline'; img-src data:; connect-src 'none'; form-action 'none'`.
- **The iframe** (frontend): `<iframe sandbox="allow-scripts" src=".../forms/render?...">`
  — `allow-scripts` WITHOUT `allow-same-origin` → opaque origin. The form's JS runs but
  reaches nothing of ours (cookies, API, localStorage, network).
- **The collector shim** (~30 static lines): on the form's submit (and a parent-driven
  "Done"), it serializes the form's named fields (the values the form's JS populated) and
  `postMessage`s `{type: "skynet-form-vars", variables: {...}}` to `window.parent`
  (targetOrigin = app origin). For reply-with-form, the parent seeds prefill values the
  shim applies to matching fields on load.
- **Trust boundary:** the parent's `message` listener validates
  `event.source === iframe.contentWindow` and the message shape; the only thing crossing
  is the flat variable dict, re-validated server-side by the builder. The shim cannot
  call our API (opaque origin).

### The message builder

`backend/modules/forms/builder.py` — a deterministic port of PAT's `scanAndBuild` +
`buildXML`. Pure functions (no I/O), exhaustively unit-testable.

- `build_form_message(template_path, variables, context) -> ComposedForm` where
  `context` = `{callsign, datetime_stamp, grid/position}` and `ComposedForm` =
  `{to, cc, subject, body, attachment: B2FAttachment}`.
- Walks the `.txt` template control lines: `To:`, `Cc:`, `Subject:`/`Subj:`, `Msg:`
  (body begins after), `Def:` (template defaults into the variable set), `Form:`,
  `ReplyTemplate:`, `Seqinc`/`Seqset`/`Readonly` (recognized; sequence handling minimal
  for v1).
- Substitutes `<Var Name>` (case-insensitive, `Def:` fills gaps) and the fixed
  **insertion tags** from `context`: `<MsgSender>`/`<Callsign>` → net callsign,
  `<DateTime>`/`<UDTG>` → `datetime_stamp`, `<GridSquare>`/`<GPS>`/`<Position>` → event
  location if set, else blank (PAT leaves unconfigured tags blank). Insertion-tag list
  ported from PAT's `insertion_tags` reference.
- `{Ask}`/`{Select}` prompts merge from the operator's answers (collected in the preview
  step) before the final substitution — nothing left unsubstituted.
- `build_form_xml(display_form, variables, context) -> str` emits the exact
  `<RMS_Express_Form>` structure PAT/RMS write: `<form_parameters>` (`xml_file_version`,
  `rms_express_version`, `submission_datetime`, `senders_callsign`, `grid_square`,
  `display_form`, optional `reply_template`) + `<variables>` (each `<key>value</key>`,
  sorted). Attachment filename `RMS_Express_Form_<displayform-basename>.xml`.
- **Determinism:** pure over (template, variables, context). The only non-deterministic
  input (`<DateTime>`) is passed in via `context` (stamped once at compose, stored,
  reused on retry) — never read from the clock inside the builder. So preview / send /
  retry rebuild identically.
- **Pinning task (plan):** the insertion-tag list and control-line grammar MUST be
  validated against PAT's source + a real captured `RMS_Express_Form` message — an
  explicit plan task, not hand-waved. (Ties to SP4a's open real-`.b2f` interop caveat.)

### Data model, outbound compose, reply-with-form

- **`event_message_forms`** companion table: `id`, `event_message_id` FK →
  event_messages.id (cascade), `template_path`, `display_form`, `reply_template`
  (nullable), `variables` (JSON), `datetime_stamp`. One row per outbound form message.
- **`send_event_form_message(db, event_id, *, actor, template_path, variables,
  reply_to_id=None)`** (events message service): runs `build_form_message`, creates the
  outbound `EventMessage` (subject/body from the build) + the companion row, and sends
  via SP3's `dispatch_delivery("event_message", …, backends=["winlink"],
  config_overrides={"target_address": …, "attachments": [xml_attachment]})` — SP4a's
  `WinlinkBackend` carries the attachment. `to_address` from the template's `To:` or an
  operator override.
- **Retry** re-runs the deterministic build from the stored companion row (reusing
  `datetime_stamp`) — byte-stable.
- **Reply-with-form:** `POST …/messages/{id}/reply-form` reads the inbound message's
  captured `reply_template` + variables (SP4a), resolves the reply template → its input
  form, and drives the compose flow prefilled from the inbound variables (best-effort
  name match). No input form → prefilled plain-text reply (SP3 path). The reply links via
  `reply_to_id` (SP3 threading).
- **API (all NCS):** `POST …/forms/compose` (build-only preview, no send), `POST
  …/form-messages` (send), `POST …/messages/{id}/reply-form`. Preview (build-only) is
  separate from send.

### Frontend compose flow

Entered from the event Messages panel (`New form`, or `Reply with form` on an inbound
form message):

1. **`FormCatalog.tsx`** — the folder tree + search; pick a form. (Reply-with-form skips
   this; the reply template is pre-resolved.)
2. **`FormFillFrame.tsx`** — the sandboxed iframe (Section: serve). NCS fills natively; a
   parent "Done" (and the form's own submit) triggers the shim → `postMessage` → the
   parent captures the variable dict. Reply-with-form seeds prefill values.
3. **Prompts step** — unfilled `{Ask}`/`{Select}` rendered as native inputs; answers
   merge into variables.
4. **`FormPreview.tsx`** — `POST …/forms/compose` (build-only) → show To/Subject/Body +
   "will attach `RMS_Express_Form_<name>.xml`"; editable `to_address`. The kept confirm
   step.
5. **Send** → `POST …/form-messages` (or reply-form) → outbound `EventMessage` threaded
   in the panel with its 📎.

A `useFormCompose` hook holds `{template, variables, prompts}` across steps. The
sandboxed iframe is the only place untrusted JS runs; the only thing leaving it is the
validated variable dict. NCS + active event only.

## Error handling

- Malformed template / build failure → typed `FormBuildError` → 422 with reason (never
  500); operator stays on fill/preview with variables intact.
- Sandbox never yields variables (form errors / no submit) → compose can't advance; no
  partial send; the stuck iframe is contained.
- Path traversal on serve/render → 404 (realpath-guarded).
- `.js`-less library (operator hasn't re-fetched) → form loads degraded; catalog/preview
  surface a "re-fetch the forms library" hint.
- Send failure → SP3 non-fatal path: outbound `EventMessage` + companion row persist,
  delivery marked failed, retry re-runs the deterministic rebuild.
- Reply template has no input form → prefilled plain-text reply fallback (never
  dead-ends).

## Testing

- **Builder unit tests (the heart):** `<Var>` substitution, insertion-tag autofill from
  context, `Def:` defaults, `{Ask}`/`{Select}` merge, reply-template quoting, and the
  exact `RMS_Express_Form` XML — asserted against a **real captured RMS_Express_Form
  message** (the pinning task). Determinism: same (template, variables, datetime) →
  identical bytes.
- **Serve route:** shim injected, restrictive CSP header present, traversal blocked,
  net_control-gated.
- **Compose/send/reply-form routes:** permission matrix, closed-event 409, companion row
  persisted, retry rebuild byte-stable, reply-with-form prefill + plain-text fallback.
- **Catalog:** tree build, composable-only filtering, search, version-keyed cache
  invalidation.
- **Frontend:** the postMessage trust boundary (source check, shape validation), build
  gate, manual smoke of a real fill → preview → send.

## Non-goals (SP4b)

- Live-data / "Web Services" forms needing network (offline-only sandbox; future).
- The newer JSON form format (future).
- Form authoring/editing (we fill existing library forms, not create templates).
- Inbound form rendering changes (the SP4a/existing receive-side viewer is unchanged).
- Full sequence-number (`Seqinc`/`Seqset`) semantics beyond recognition (minimal v1).
