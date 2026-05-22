# TODO: Configurable Check-in Modes

## Problem

The message parser has a hardcoded list of known modes (`winlink`, `vara`, `ardop`, `packet`, `pactor`, `telnet`, `ax.25`). The Add Check-in modal has a different hardcoded list (`Voice`, `Winlink`, `CW`, `Digital`). Neither matches real-world usage well, and neither is configurable.

## Goal

Make the list of recognized modes configurable via `AppConfig`, with sensible defaults. Use that same list to populate the Add Check-in modal's mode dropdown.

## Scope

1. **Backend — AppConfig key:** Add `checkins.modes` config key storing a JSON list of mode strings. Default: `["Voice", "Winlink", "VARA FM", "VARA HF", "ARDOP", "1200-baud Packet", "9k6 Packet", "Pactor", "Telnet", "AX.25", "CW", "Digital"]`

2. **Backend — Message parser:** Replace the hardcoded `known_modes` set in `backend/modules/checkins/message_parser.py` with a lookup from `AppConfig`. Fall back to the default list if not configured.

3. **Backend — API endpoint:** Add `GET /api/checkins/modes` (or include in an existing config endpoint) to expose the configured modes list to the frontend.

4. **Frontend — Add Check-in modal:** Replace the hardcoded `<select>` options with a dynamic list fetched from the modes endpoint. Keep the text input on the Edit modal for flexibility (edited check-ins may have non-standard modes).

5. **Frontend — Admin config:** Consider exposing the modes list in the Config page so admins can add/remove modes without touching the database directly.

## Files

- `backend/modules/checkins/message_parser.py` — replace hardcoded `known_modes`
- `backend/modules/checkins/routes.py` — add modes endpoint
- `frontend/src/pages/CheckInsPage.tsx` — dynamic mode dropdown in AddCheckinModal
- `frontend/src/api/checkins.ts` — add `fetchModes()` API client function
