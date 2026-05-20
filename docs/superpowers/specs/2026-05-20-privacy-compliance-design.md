# Privacy Compliance Design Spec

**Goal:** Add GDPR/CCPA compliance features: user data export, account anonymization ("right to be forgotten"), and a privacy policy page. Designed for self-hosted deployments where the net operator is the data controller.

**Architecture:** A new `backend/privacy/` sub-package with two services (export and anonymization) and API routes, plus frontend pages for the privacy policy and user-facing data actions. Admins get equivalent controls on the Users page.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, React/TypeScript

---

## Scope

This spec covers a self-hosted deployment used by a single net. The app uses only strictly-necessary HTTPOnly cookies for authentication — no tracking, analytics, or third-party cookies — so no cookie consent banner is needed. Cookie usage is documented in the privacy policy instead.

---

## Anonymization Service

When a user is anonymized, all PII is replaced with opaque placeholders. A unique anonymous identifier (e.g., `ANON-A3F8`) is generated per anonymization to maintain referential integrity — the same placeholder replaces the callsign everywhere so check-in history still links together.

### Tables and Fields Affected

| Table | Fields Anonymized | Action |
|-------|-------------------|--------|
| `users` | callsign -> `ANON-XXXX`, name -> `"Deleted User"`, email -> `null`, oidc_subject -> `"deleted"`, pending_callsign -> `null`, role -> `DELETED` | Update in place |
| `check_ins` | callsign -> `ANON-XXXX`, name -> `"Deleted User"`, city/county/state -> `null`, latitude/longitude -> `null`, comments -> `null` | Update in place |
| `raw_messages` | from_address -> `"anonymized"`, subject -> `"[redacted]"`, body -> `"[redacted]"` | Update in place |
| `members` | callsign -> `ANON-XXXX`, name -> `"Deleted User"` | Update in place |
| `audit_log` | actor_callsign/target_callsign -> `ANON-XXXX` where they match the original callsign | Update in place |
| `personal_access_tokens` | All tokens for this user | Hard delete |
| `delivery_logs` | No PII in this table | No action |
| `reminder_logs` | No user PII (content is net-wide) | No action |
| `roster_logs` | No user PII (content is net-wide) | No action |

### Anonymization Behavior

- The `ANON-XXXX` identifier is generated as `ANON-` plus 4 random hex characters (uppercase). If a collision occurs, regenerate.
- The anonymized user record stays in the `users` table with role set to `DELETED` so the account can't log in.
- The original callsign is freed — the `ANON-XXXX` primary key won't conflict with a future registration using the same callsign.
- Raw messages linked to anonymized check-ins have their content redacted because message bodies may contain personal information.
- An audit log entry is created after anonymization: `action: "user.anonymized"`, `actor_callsign: <who triggered it>`, `target_callsign: "ANON-XXXX"` (the new anonymous ID, not the original).
- After self-service anonymization, the user's access token cookie is cleared and the session is invalidated.
- Admins cannot anonymize other admins (safety check).
- A user cannot anonymize themselves if they are the sole admin.

### UserRole Change

Add `DELETED = "deleted"` to the `UserRole` enum. Users with this role are excluded from all user listings and cannot authenticate.

---

## Data Export Service

Collects all data associated with a user's callsign and returns it as a JSON download.

### Export Format

```json
{
  "exported_at": "2026-05-20T12:00:00Z",
  "user": {
    "callsign": "W0NE",
    "name": "John Smith",
    "email": "john@example.com",
    "role": "admin",
    "created_at": "2025-01-15T08:00:00Z"
  },
  "check_ins": [
    {
      "session_id": 1,
      "callsign": "W0NE",
      "name": "John Smith",
      "city": "Denver",
      "county": "Denver",
      "state": "CO",
      "latitude": 39.7392,
      "longitude": -104.9903,
      "comments": "Good signal today",
      "timing_status": "on_time",
      "created_at": "2026-05-20T12:00:00Z"
    }
  ],
  "raw_messages": [
    {
      "message_id": "abc123",
      "from_address": "w0ne@winlink.org",
      "subject": "Check-in",
      "body": "Name: John Smith\nCallsign: W0NE\n...",
      "received_at": "2026-05-20T12:00:00Z"
    }
  ],
  "member_record": {
    "callsign": "W0NE",
    "name": "John Smith",
    "first_check_in_date": "2025-02-01",
    "last_check_in_date": "2026-05-20",
    "total_check_ins": 45
  },
  "audit_log": [
    {
      "action": "user.role_changed",
      "actor_callsign": "ADMIN",
      "target_callsign": "W0NE",
      "details": "{\"from\": \"viewer\", \"to\": \"net_control\"}",
      "created_at": "2025-03-01T10:00:00Z"
    }
  ],
  "tokens": [
    {
      "name": "API Script",
      "scopes": "schedule:read,checkins:read",
      "created_at": "2026-01-01T00:00:00Z",
      "last_used_at": "2026-05-19T15:30:00Z",
      "expires_at": null
    }
  ]
}
```

Secrets (token hashes, oidc_subject) are excluded from the export. Only data the user would recognize as "theirs" is included. Audit log entries include those where the user is either the actor or the target.

---

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/privacy/export` | Any authenticated user | Export your own data as JSON |
| `GET` | `/api/privacy/export/{callsign}` | Admin only | Export a specific user's data |
| `POST` | `/api/privacy/anonymize` | Any authenticated user | Anonymize your own account |
| `POST` | `/api/privacy/anonymize/{callsign}` | Admin only | Anonymize a specific user's account |

### Anonymize Request Body

```json
{"confirm": true}
```

The `confirm: true` field is required to prevent accidental deletions. Requests without it return 400.

### Anonymize Response

```json
{
  "anonymized": true,
  "anonymous_id": "ANON-A3F8",
  "message": "Account anonymized. All personal data has been replaced."
}
```

### Export Response

Returns the JSON export as a file download with `Content-Disposition: attachment; filename="skynetcontrol-export-{callsign}.json"` and `Content-Type: application/json`.

---

## Frontend

### Privacy Policy Page

A new route at `/privacy` rendering a static privacy policy as a React component. Content covers:

- What data is collected (callsign, name, email, location, check-in messages)
- Why it's collected (net operations, roster generation, check-in tracking)
- How it's stored (local database on the operator's server)
- External sharing (groups.io, email, Winlink — only when delivery backends are configured)
- Cookies used (two strictly-necessary HTTPOnly auth cookies, no tracking)
- User rights (export data, delete account)
- How to exercise rights (links to in-app actions)

A link to the privacy policy appears in the app navigation or footer.

### User Privacy & Data Section

Accessible to any logged-in user (e.g., via a profile dropdown or settings page). Contains:

- **"Download My Data"** button — calls `GET /api/privacy/export`, triggers a JSON file download in the browser
- **"Delete My Account"** button — shows a confirmation dialog:
  - Explains what happens: account anonymized, personal data replaced with placeholders, action is irreversible
  - Requires typing "DELETE" to confirm
  - Calls `POST /api/privacy/anonymize` with `{"confirm": true}`
  - On success, clears the auth cookie and redirects to the login page

### Admin Controls on Users Page

The existing Users page gets two new actions per user row:

- **"Export Data"** icon/button — calls `GET /api/privacy/export/{callsign}`, triggers file download
- **"Anonymize"** icon/button — same confirmation dialog flow as self-service, calls `POST /api/privacy/anonymize/{callsign}`

Not shown for users with `DELETED` role.

---

## File Structure

```
backend/privacy/
├── __init__.py
├── service.py          # export_user_data(), anonymize_user()
└── routes.py           # Privacy API endpoints

frontend/src/
├── pages/PrivacyPolicyPage.tsx    # Static privacy policy content
├── api/privacy.ts                  # API client functions
```

### Modifications to Existing Files

| File | Change |
|------|--------|
| `backend/auth/models.py` | Add `DELETED = "deleted"` to `UserRole` enum |
| `backend/app.py` | Register privacy routes at `/api/privacy` |
| `alembic/env.py` | Import `backend.privacy` (no new models, but for enum migration) |
| `frontend/src/App.tsx` | Add `/privacy` route |
| `frontend/src/pages/UsersPage.tsx` | Add export/anonymize actions per user row |
| `frontend/src/api/users.ts` | Add `exportUserData()` and `anonymizeUser()` API functions (or in new `privacy.ts`) |

---

## Testing Strategy

- **Anonymization service tests**: Verify all PII fields are replaced, referential integrity maintained, tokens deleted, audit log created, sole-admin protection
- **Export service tests**: Verify all user data included, secrets excluded, correct JSON structure
- **Route tests**: Auth checks (self-service vs admin), confirm body required, response format, cookie clearing on self-anonymize
- **Integration tests**: Full flow — export data, anonymize, verify exported data no longer retrievable, verify anonymized records exist with placeholders
