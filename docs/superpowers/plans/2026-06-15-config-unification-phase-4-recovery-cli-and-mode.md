# Config Unification — Phase 4: Recovery CLI + Recovery-Mode Wizard

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the operator a break-glass path to fix a misconfigured install (e.g. a fat-fingered OAuth client secret that locks everyone out of sign-in). A new `skynetcontrol-recovery` CLI mints a short-lived token; pasting it at `/recovery` sets a short-lived cookie that lifts the `setup_completed` gate and admits access to the existing admin-only OAuth / SMTP / setup-test endpoints. The wizard SPA re-runs in **edit-existing mode** against the current AppConfig.

**Architecture:**

- **Schema** — new `admin_recovery_tokens` table: `id`, `token_hash` (sha256, indexed), `expires_at`, `used_at`, `created_at`.
- **Module** — `backend/auth/recovery.py` owns: token generation (`secrets.token_urlsafe`), sha256 hashing on store, expiry checks, single-use enforcement, and the recovery JWT cookie helpers (`make_recovery_token`, `decode_recovery_token`).
- **CLI** — `backend/cli/recovery.py` with `mint-admin-token`, `list-tokens`, `revoke <prefix>` subcommands. Wrapped as `skynetcontrol-recovery` in `default.nix`. Reads `SKYNET_DATABASE_URL` like the alembic CLI does.
- **HTTP** — `backend/auth/recovery_routes.py` exposes:
  - `GET /api/recovery/status` → `{outstanding: bool}` (404 vs 200 is too coarse for the SPA; use this instead and the SPA decides)
  - `POST /api/recovery/claim` → `{token: "..."}`, validates + sets cookie + 200 with body (frontend handles redirect)
- **Recovery cookie** — JWT signed with the same `SKYNET_JWT_SECRET_KEY` as user sessions but with claim `{"type": "recovery", "hash_prefix": "abc12345", "exp": <now+30m>}`. Distinct from user-session JWTs, never confused with one.
- **Auth gating** — new dependency `require_admin_or_recovery` accepts EITHER a normal admin JWT OR a valid recovery JWT. Applied to the specific endpoints recovery mode needs to reach (OAuth CRUD, SMTP CRUD, test endpoints). The existing `require_role` stays unchanged everywhere else, so recovery cookies cannot reach members / check-ins / etc.
- **Setup status augmented** — `GET /api/setup/status` learns to report `{setup_completed, recovery_mode}`. `recovery_mode=true` iff the request carries a valid recovery cookie. Frontend uses this to flip into edit-existing mode.
- **Frontend** — new `RecoveryPage.tsx` for the token-entry form (routed at `/recovery`). `SetupGate.tsx` learns to render `<SetupPage>` when `setup_completed=false` OR `recovery_mode=true`. `SetupPage.tsx` pre-fills steps 1-3 from the current AppConfig when `recovery_mode=true`, switches save semantics to per-step (each step's Save calls the existing CRUD endpoints), and shows "Done" instead of "Sign in to claim" at step 4 — clicking Done expires the recovery cookie and reloads.
- **Audit log** — `log_action(db, actor="recovery:<hash-prefix>", ...)` for every save during a recovery session. The `actor` field on `require_admin_or_recovery`'s return value lets existing call sites carry on with no other changes.

**Tech Stack:** Same as prior phases.

**Spec:** `docs/superpowers/specs/2026-06-12-config-unification-design.md` (sections "Recovery CLI" and "Recovery-mode wizard")

---

## Schema

```python
class AdminRecoveryToken(Base):
    __tablename__ = "admin_recovery_tokens"

    id          : int (pk)
    token_hash  : str (64-char sha256 hex, unique, indexed)
    expires_at  : datetime (timezone-aware UTC)
    used_at     : datetime | None
    created_at  : datetime (timezone-aware UTC, default=now)
```

## URL surface

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET    | `/api/recovery/status` | none | `{outstanding: bool}` — true iff at least one unused, unexpired token exists. The frontend uses this to decide whether to even show the token-entry form. |
| POST   | `/api/recovery/claim`  | none | Body: `{token: "..."}`. Validates, marks used, sets recovery cookie, returns 200. Frontend redirects after on success. |
| GET    | `/api/setup/status`    | none | Augmented to return `{setup_completed, recovery_mode}`. |
| Mutating endpoints (`/api/admin/oauth/providers/*`, `/api/admin/smtp`, `/api/admin/test/*`, `/api/config/{key}`) | (existing) admin | Auth dependency switches from `require_role(UserRole.ADMIN)` to `require_admin_or_recovery` — same shape (returns a "principal" with `.callsign`), just admits recovery cookies. Audit log carries `actor="recovery:<prefix>"` for recovery-cookie saves. |

---

## File structure

**New backend files:**

| Path | Responsibility |
|------|----------------|
| `backend/auth/recovery.py` | `mint_token(db) -> (plaintext, expires_at)`, `verify_token(db, plaintext) -> AdminRecoveryToken | None`, `list_outstanding(db)`, `revoke_by_prefix(db, prefix) -> int`, recovery JWT encode/decode, `RecoveryPrincipal` dataclass. |
| `backend/cli/recovery.py` | `main(argv=None)` argparse entry — three subcommands. Reads `SKYNET_DATABASE_URL` to build a session like `db_copy.py` does. |
| `backend/auth/recovery_routes.py` | `recovery_router` with `status` and `claim` endpoints. Cookie set on the claim response. |
| `backend/auth/models_recovery.py` (or extend `backend/audit/models.py` or `backend/auth/models.py`) — pick the cleanest home for `AdminRecoveryToken`. Probably `backend/auth/models.py` so it co-lives with `User`. |
| `alembic/versions/<sha>_add_admin_recovery_tokens.py` | Schema migration. |
| `tests/test_recovery_module.py` | Unit tests for `backend/auth/recovery.py`. |
| `tests/test_recovery_cli.py` | Subprocess-style tests for the CLI (or in-process via `main(argv=[...])`). |
| `tests/test_recovery_routes.py` | HTTP tests for `/api/recovery/*` and the augmented `/api/setup/status`. |
| `tests/test_admin_or_recovery_gating.py` | Tests that the new dependency admits both kinds of principal and gates correctly. |

**Modified backend files:**

| Path | Change |
|------|--------|
| `backend/auth/dependencies.py` | Add `require_admin_or_recovery` dependency. Add helper to decode recovery cookie. |
| `backend/config_mgmt/{oauth_routes,smtp_routes,test_routes}.py` | Switch the `Depends(require_role(UserRole.ADMIN))` on the mutating endpoints to `Depends(require_admin_or_recovery)`. **No other changes**; the existing `User` parameter is replaced with a `Principal` (sum type) whose `.callsign` returns either the user's callsign or `recovery:<prefix>`. |
| `backend/config_mgmt/setup_routes.py` | `GET /api/setup/status` returns `{setup_completed, recovery_mode}`. The redirect-gate logic in `claim/start` and `claim/callback` still 410s after completion REGARDLESS of recovery cookie (the wizard's claim flow only fires for first-boot; recovery mode uses per-step save). |
| `backend/app.py` | Mount `recovery_router` at `/api/recovery`. |
| `default.nix` | Add `skynetcontrol-recovery` entry-point wrapper next to the existing ones. |

**New frontend files:**

| Path | Responsibility |
|------|----------------|
| `frontend/src/api/recovery.ts` | `getRecoveryStatus`, `claimRecoveryToken(token)`, `clearRecoveryCookie()`. |
| `frontend/src/pages/RecoveryPage.tsx` | Token-entry form. POSTs to `/api/recovery/claim`. On success, navigates to `/setup`. Shows a clear message when no token is outstanding ("Ask the operator to run `skynetcontrol-recovery mint-admin-token` first"). |

**Modified frontend files:**

| Path | Change |
|------|--------|
| `frontend/src/components/SetupGate.tsx` | Treat `setup_completed=false` OR `recovery_mode=true` as "render SetupPage". |
| `frontend/src/pages/SetupPage.tsx` | On mount, if `recovery_mode=true`, pre-fill steps 1-3 from current AppConfig (uses existing OAuth + SMTP API clients + the flat `/api/config/` endpoint for net basics). Per-step Save buttons use the existing CRUD endpoints. Step 4 says "Done" (just clears the recovery cookie and reloads) instead of "Sign in with [provider]" — *unless* the OAuth provider was edited, in which case it shows "Sign in to verify" which triggers a regular `/api/auth/login/<slug>` redirect (the recovery cookie's job is done at that point and the real OAuth flow takes over). |
| `frontend/src/App.tsx` | Add `<Route path="/recovery" element={<RecoveryPage />} />` *outside* the `<SetupGate>` wrap, so the recovery page is reachable even when the gate would otherwise hide the app. |

---

## Task 1: Schema + recovery module + CLI

**Files:**
- Create: `alembic/versions/<sha>_add_admin_recovery_tokens.py`
- Create: `backend/auth/recovery.py`
- Create: `backend/cli/recovery.py`
- Create: `tests/test_recovery_module.py`
- Create: `tests/test_recovery_cli.py`
- Modify: `backend/auth/models.py` (add `AdminRecoveryToken`)
- Modify: `default.nix` (add `skynetcontrol-recovery` wrapper)

### Step 1.1: Schema

- [ ] **Step 1: Add the model**

In `backend/auth/models.py`, add:

```python
class AdminRecoveryToken(Base):
    __tablename__ = "admin_recovery_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
```

- [ ] **Step 2: Generate an Alembic migration**

```bash
nix-shell --run "alembic -c alembic.ini revision -m 'add admin recovery tokens'"
```

Replace the autogenerated body with the explicit `op.create_table(...)` matching the model. `downgrade()` drops the table.

### Step 1.2: Recovery module

- [ ] **Step 3: Write failing tests for `backend/auth/recovery.py`**

Create `tests/test_recovery_module.py` with these tests (use the same in-memory SQLite fixture pattern as `tests/test_config_mgmt_smtp.py`):

```python
def test_mint_token_returns_plaintext_and_persists_hash(db, fixed_now): ...
def test_mint_token_is_unique_across_calls(db): ...
def test_verify_token_matches_unused_unexpired(db): ...
def test_verify_token_rejects_unknown(db): ...
def test_verify_token_rejects_used(db): ...
def test_verify_token_rejects_expired(db, fixed_now): ...
def test_verify_token_does_not_mutate(db): ...  # verify() is pure; marking-used is a separate step
def test_mark_used_is_idempotent(db): ...
def test_list_outstanding_filters_used_and_expired(db, fixed_now): ...
def test_revoke_by_prefix_marks_matching_tokens_used(db): ...
def test_revoke_by_prefix_returns_count(db): ...
def test_recovery_jwt_round_trip(): ...
def test_recovery_jwt_expired_rejected(): ...
def test_recovery_jwt_wrong_type_rejected(): ...   # user-session JWTs must NOT decode as recovery
```

For the `fixed_now` fixture, monkeypatch `datetime.now(timezone.utc)` inside the recovery module. Use `freezegun` if it's already a dep; if not, plain monkeypatch via a `_clock()` helper inside the module.

- [ ] **Step 4: Confirm failure**

- [ ] **Step 5: Implement `backend/auth/recovery.py`**

```python
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy.orm import Session

from backend.auth.models import AdminRecoveryToken
from backend.config import Settings

_TOKEN_TTL = timedelta(minutes=10)
_COOKIE_TTL_MINUTES = 30


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def mint_token(db: Session, ttl: timedelta = _TOKEN_TTL) -> tuple[str, datetime]:
    """Generate a fresh single-use admin-recovery token."""
    plaintext = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + ttl
    db.add(AdminRecoveryToken(token_hash=_hash(plaintext), expires_at=expires_at))
    db.commit()
    return plaintext, expires_at


def verify_token(db: Session, plaintext: str) -> AdminRecoveryToken | None:
    """Return the matching token row iff present, unused, and unexpired. Does NOT mark used."""
    row = (
        db.query(AdminRecoveryToken)
        .filter(AdminRecoveryToken.token_hash == _hash(plaintext))
        .one_or_none()
    )
    if row is None:
        return None
    if row.used_at is not None:
        return None
    if datetime.now(timezone.utc) >= row.expires_at:
        return None
    return row


def mark_used(db: Session, row: AdminRecoveryToken) -> None:
    """Mark the token row as used. Idempotent (no-op if already marked)."""
    if row.used_at is None:
        row.used_at = datetime.now(timezone.utc)
        db.commit()


def list_outstanding(db: Session) -> list[AdminRecoveryToken]:
    """Return all unused, unexpired tokens, ordered by expiry ascending."""
    now = datetime.now(timezone.utc)
    return (
        db.query(AdminRecoveryToken)
        .filter(AdminRecoveryToken.used_at.is_(None))
        .filter(AdminRecoveryToken.expires_at > now)
        .order_by(AdminRecoveryToken.expires_at)
        .all()
    )


def revoke_by_prefix(db: Session, prefix: str) -> int:
    """Mark all unused tokens whose token_hash starts with `prefix` as used. Returns count revoked."""
    rows = (
        db.query(AdminRecoveryToken)
        .filter(AdminRecoveryToken.used_at.is_(None))
        .filter(AdminRecoveryToken.token_hash.like(f"{prefix}%"))
        .all()
    )
    now = datetime.now(timezone.utc)
    for row in rows:
        row.used_at = now
    db.commit()
    return len(rows)


# ─── recovery cookie JWT ────────────────────────────────────────────────────


@dataclass(frozen=True)
class RecoveryPrincipal:
    """Returned by `require_admin_or_recovery` when the recovery cookie is valid."""
    hash_prefix: str

    @property
    def callsign(self) -> str:
        # Mirrors User.callsign so audit-log call sites don't need to branch.
        return f"recovery:{self.hash_prefix}"


def make_recovery_token(hash_prefix: str, settings: Settings) -> str:
    payload = {
        "type": "recovery",
        "hash_prefix": hash_prefix,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=_COOKIE_TTL_MINUTES),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_recovery_token(token: str, settings: Settings) -> RecoveryPrincipal | None:
    """Decode and validate a recovery cookie. Returns None for any failure (expired, wrong type, malformed)."""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "recovery":
        return None
    prefix = payload.get("hash_prefix")
    if not isinstance(prefix, str) or not prefix:
        return None
    return RecoveryPrincipal(hash_prefix=prefix)


def cookie_ttl_seconds() -> int:
    return _COOKIE_TTL_MINUTES * 60
```

- [ ] **Step 6: Confirm tests pass.** Expect 14.

### Step 1.3: CLI

- [ ] **Step 7: Write failing tests for the CLI**

`tests/test_recovery_cli.py` calls `backend.cli.recovery:main(argv=[...])` directly with a temp SQLite DB (set via env). Tests:

```python
def test_cli_mint_prints_token_and_url(tmp_path, capsys, monkeypatch): ...
def test_cli_mint_persists_token_in_db(tmp_path, monkeypatch): ...
def test_cli_list_tokens_shows_outstanding_only(tmp_path, capsys, monkeypatch): ...
def test_cli_list_tokens_does_not_print_plaintext(tmp_path, capsys, monkeypatch): ...
def test_cli_revoke_marks_token_used(tmp_path, capsys, monkeypatch): ...
def test_cli_revoke_unknown_prefix_exits_0(tmp_path, capsys, monkeypatch): ...
def test_cli_unknown_subcommand_exits_nonzero(monkeypatch, capsys): ...
```

For each test that needs a DB: `monkeypatch.setenv("SKYNET_DATABASE_URL", f"sqlite:///{tmp_path}/r.db")`, then create the engine + run alembic upgrade head (or just `Base.metadata.create_all`) before calling `main()`.

- [ ] **Step 8: Implement `backend/cli/recovery.py`**

```python
"""skynetcontrol-recovery — break-glass admin recovery token management.

Subcommands:
  mint-admin-token [--ttl 10m]    — generate a token, print plaintext + claim URL
  list-tokens                     — show outstanding (unused, unexpired) tokens, no plaintext
  revoke <prefix>                 — mark all unused tokens with the given hash prefix as used
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import timedelta
from typing import Sequence

from backend.auth.recovery import (
    list_outstanding,
    mint_token,
    revoke_by_prefix,
)
from backend.config import settings
from backend.db.engine import create_engine_from_url
from backend.db.session import create_session_factory


def _parse_ttl(spec: str) -> timedelta:
    m = re.fullmatch(r"(\d+)([smhd])", spec)
    if not m:
        raise argparse.ArgumentTypeError(f"bad TTL {spec!r}; use forms like 10m, 1h, 30s")
    n, unit = int(m.group(1)), m.group(2)
    return {"s": timedelta(seconds=n), "m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skynetcontrol-recovery")
    sub = parser.add_subparsers(dest="cmd", required=True)

    mint = sub.add_parser("mint-admin-token", help="Mint a single-use admin recovery token.")
    mint.add_argument("--ttl", default="10m", type=_parse_ttl,
                      help="How long the token is valid (e.g. 10m, 1h). Default 10m.")

    sub.add_parser("list-tokens", help="Show outstanding (unused, unexpired) tokens. No plaintext.")

    revoke = sub.add_parser("revoke", help="Mark all unused tokens with this hash prefix as used.")
    revoke.add_argument("prefix", help="Hash prefix (8+ hex chars recommended).")

    args = parser.parse_args(argv)

    engine = create_engine_from_url(settings.database_url)
    session_factory = create_session_factory(engine)

    with session_factory() as db:
        if args.cmd == "mint-admin-token":
            plaintext, expires_at = mint_token(db, ttl=args.ttl)
            hash_prefix = __import__("hashlib").sha256(plaintext.encode()).hexdigest()[:8]
            print(f"Token (use it once): {plaintext}")
            print(f"Claim URL:           {settings.app_base_url}/recovery?token={plaintext}")
            print(f"Hash prefix:         {hash_prefix}")
            print(f"Expires:             {expires_at.isoformat()}")
            print()
            print("This token is shown ONCE. Paste the URL into a browser before it expires.")
            return 0

        if args.cmd == "list-tokens":
            rows = list_outstanding(db)
            if not rows:
                print("No outstanding tokens.")
                return 0
            print(f"{'Hash prefix':<12} {'Expires at'}")
            for row in rows:
                print(f"{row.token_hash[:8]:<12} {row.expires_at.isoformat()}")
            return 0

        if args.cmd == "revoke":
            count = revoke_by_prefix(db, args.prefix)
            print(f"Revoked {count} token(s) matching prefix {args.prefix!r}.")
            return 0

    # Argparse with required=True already exits non-zero for unknown subcommands;
    # this is a defensive fallthrough.
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 9: Wire the CLI entry point in `default.nix`**

In `postInstall`, add:

```nix
# Recovery CLI for breaking back in after a misconfigured save.
printf '%s\n' '#!${python}/bin/python' 'import sys' 'from backend.cli.recovery import main' 'sys.exit(main())' > $out/bin/skynetcontrol-recovery
chmod +x $out/bin/skynetcontrol-recovery
```

(Place it next to the `skynetcontrol-db-copy` wrapper.)

- [ ] **Step 10: Run CLI tests.** Expect 7 passed.

- [ ] **Step 11: Full suite + ruff.**

- [ ] **Step 12: Commit**

```bash
git add backend/auth/recovery.py backend/auth/models.py backend/cli/recovery.py \
        alembic/versions/*_add_admin_recovery_tokens.py \
        tests/test_recovery_module.py tests/test_recovery_cli.py \
        default.nix
git commit -m "feat(recovery): admin_recovery_tokens table + recovery module + CLI

New schema: admin_recovery_tokens (token_hash sha256-hex, expires_at,
used_at, created_at). Backed by backend/auth/recovery.py — mint /
verify (single-use enforcement) / list-outstanding / revoke-by-prefix
helpers plus a recovery JWT cookie encoder/decoder using the same
SKYNET_JWT_SECRET_KEY as user sessions but with a distinct \"type\":
\"recovery\" claim so the two cannot be confused.

New CLI skynetcontrol-recovery wraps it: mint-admin-token prints the
plaintext + a claim URL once, list-tokens shows outstanding rows
(hash prefix + expiry, no plaintext), revoke <prefix> marks matching
unused tokens used."
```

---

## Task 2: HTTP routes + recovery cookie + auth-gate extension

**Files:**
- Create: `backend/auth/recovery_routes.py`
- Create: `tests/test_recovery_routes.py`
- Create: `tests/test_admin_or_recovery_gating.py`
- Modify: `backend/auth/dependencies.py` (`require_admin_or_recovery`)
- Modify: `backend/config_mgmt/oauth_routes.py`, `smtp_routes.py`, `test_routes.py` (swap admin dep)
- Modify: `backend/config_mgmt/setup_routes.py` (augment `/status` response with `recovery_mode`)
- Modify: `backend/app.py` (mount `recovery_router`)

### Step 2.1: `require_admin_or_recovery` dependency

- [ ] **Step 1: Add to `backend/auth/dependencies.py`**

```python
from backend.auth.recovery import RecoveryPrincipal, decode_recovery_token


# Marker so call sites can pattern-match if needed. Most don't — they just
# read `.callsign` for the audit log, which both branches satisfy.
Principal = User | RecoveryPrincipal


def require_admin_or_recovery(request: Request, db: Session = Depends(get_db_session)) -> Principal:
    """Admit either a normal admin user OR a request carrying a valid recovery cookie.

    Audit-log calls in the wrapped handlers use `principal.callsign` — for an
    admin that's their callsign; for a recovery session it's
    `recovery:<hash-prefix>`. No other call site changes.
    """
    # 1) Try recovery cookie first — if it's present and valid, accept it
    # before we even try to decode a user JWT. This makes recovery sessions
    # work even if there's a stale or invalid user cookie attached.
    settings = request.app.state.settings
    recovery_cookie = request.cookies.get("recovery_token")
    if recovery_cookie:
        principal = decode_recovery_token(recovery_cookie, settings)
        if principal is not None:
            return principal

    # 2) Fall back to the normal admin check. Reuse require_role's logic.
    user_dep = require_role(UserRole.ADMIN)
    return user_dep(request=request, db=db)
```

(Read the existing `require_role` to confirm its parameters — adjust the recursive call accordingly. The above sketch assumes `require_role` returns a `Callable[Request, Session] -> User`; if it's a different shape, adapt.)

### Step 2.2: Recovery routes

- [ ] **Step 2: Tests for `/api/recovery/*`**

```python
def test_status_returns_outstanding_false_when_no_tokens(test_client): ...
def test_status_returns_outstanding_true_after_mint(test_client, db_setup): ...
def test_status_only_counts_unused_unexpired(test_client, db_setup): ...
def test_claim_returns_400_on_unknown_token(test_client): ...
def test_claim_returns_400_on_used_token(test_client, db_setup): ...
def test_claim_returns_400_on_expired_token(test_client, db_setup): ...
def test_claim_sets_recovery_cookie_and_marks_used(test_client, db_setup): ...
def test_claim_cookie_has_expected_attributes(test_client, db_setup): ...
def test_claim_is_single_use(test_client, db_setup): ...   # second claim with same token → 400
```

- [ ] **Step 3: Implement `backend/auth/recovery_routes.py`**

```python
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session, get_settings
from backend.auth.recovery import (
    cookie_ttl_seconds,
    list_outstanding,
    make_recovery_token,
    mark_used,
    verify_token,
)
from backend.config import Settings

recovery_router = APIRouter(prefix="/recovery", tags=["recovery"])


@recovery_router.get("/status")
def recovery_status(db: Session = Depends(get_db_session)) -> dict:
    return {"outstanding": len(list_outstanding(db)) > 0}


class RecoveryClaimRequest(BaseModel):
    token: str


@recovery_router.post("/claim")
def recovery_claim(
    body: RecoveryClaimRequest,
    response: Response,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    row = verify_token(db, body.token)
    if row is None:
        raise HTTPException(status_code=400, detail="invalid, used, or expired token")
    mark_used(db, row)
    cookie_value = make_recovery_token(hash_prefix=row.token_hash[:8], settings=settings)
    is_secure = settings.app_base_url.startswith("https://")
    response.set_cookie(
        key="recovery_token",
        value=cookie_value,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=cookie_ttl_seconds(),
    )
    return {"ok": True}
```

- [ ] **Step 4: Mount in `backend/app.py`**

```python
app.include_router(recovery_router, prefix="/api")
```

### Step 2.3: Swap the admin dep in mutating endpoints

- [ ] **Step 5: In `oauth_routes.py`, `smtp_routes.py`, `test_routes.py`, AND `config_mgmt/routes.py`**

Find every `User = Depends(require_role(UserRole.ADMIN))` parameter on the mutating endpoints and replace with `principal: Principal = Depends(require_admin_or_recovery)`. Endpoints to swap:

- `backend/config_mgmt/oauth_routes.py` — PUT, DELETE, POST `/slug/derive`. (GET `list` and GET `{slug}` stay admin-only — read access during recovery is fine to admit, but to keep blast radius tight, only the writes are exposed. Adjust this if your judgment differs; the plan's choice is: admit recovery on writes only.)

  Actually on reflection: the wizard in recovery mode NEEDS to read the existing providers to pre-fill Step 2. So GET `list` and GET `{slug}` must also be admitted. Swap those too.

- `backend/config_mgmt/smtp_routes.py` — GET (needed for pre-fill), PUT, DELETE.

- `backend/config_mgmt/test_routes.py` — POST `/oauth/{slug}/start`, GET `/oauth/{test_session_id}/result`, POST `/smtp`. The callback stays no-auth.

- `backend/config_mgmt/routes.py` — PUT `/{key}` (the flat-key endpoint Step 1 in recovery mode uses for net basics). GET `/` (the list endpoint, also needed for pre-fill) — admit recovery here too.

In every changed handler, where `log_action(db, actor=user.callsign, ...)` appears, change `user.callsign` to `principal.callsign`.

### Step 2.4: Augment `/api/setup/status`

- [ ] **Step 6: In `backend/config_mgmt/setup_routes.py`**

```python
@setup_router.get("/status")
def setup_status(
    request: Request,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Always reachable. recovery_mode is true iff a valid recovery cookie is attached."""
    cookie = request.cookies.get("recovery_token")
    in_recovery = False
    if cookie:
        in_recovery = decode_recovery_token(cookie, settings) is not None
    return {"setup_completed": is_setup_completed(db), "recovery_mode": in_recovery}
```

### Step 2.5: Gating tests

- [ ] **Step 7: `tests/test_admin_or_recovery_gating.py`**

```python
def test_oauth_put_admits_admin_user(test_client, admin_token): ...
def test_oauth_put_admits_recovery_cookie(test_client, valid_recovery_cookie): ...
def test_oauth_put_rejects_unauthenticated(test_client): ...
def test_oauth_put_rejects_expired_recovery_cookie(test_client, expired_recovery_cookie): ...
def test_audit_actor_is_user_callsign_when_admin(test_client, admin_token): ...
def test_audit_actor_is_recovery_prefix_when_recovery(test_client, valid_recovery_cookie): ...
def test_setup_status_recovery_mode_true_with_valid_cookie(test_client, valid_recovery_cookie): ...
def test_setup_status_recovery_mode_false_without_cookie(test_client): ...
```

The `valid_recovery_cookie` fixture mints a token via the recovery module, claims it via POST `/api/recovery/claim`, and yields the cookie value.

### Step 2.6: Run + commit

- [ ] **Step 8: Tests + ruff.**

- [ ] **Step 9: Commit**

```bash
git add backend/auth/recovery_routes.py backend/auth/dependencies.py \
        backend/config_mgmt/{oauth_routes,smtp_routes,test_routes,setup_routes}.py \
        backend/app.py \
        tests/test_recovery_routes.py tests/test_admin_or_recovery_gating.py
git commit -m "feat(recovery): HTTP routes + auth gate that accepts admin or recovery cookie

POST /api/recovery/claim validates the token, marks it used, issues a
recovery_token cookie (JWT signed with the same JWT secret, type
\"recovery\", 30-min TTL), returns 200. GET /api/recovery/status
reports whether any tokens are outstanding so the frontend knows
whether to render the entry form.

A new require_admin_or_recovery dependency accepts either an admin
JWT or a valid recovery JWT and is wired into the mutating OAuth /
SMTP / test endpoints. Audit-log calls now record actor=
\"recovery:<hash-prefix>\" for recovery sessions, so saves made during
a recovery window are tagged distinctly.

GET /api/setup/status now also returns recovery_mode: bool so the
frontend can flip into edit-existing mode."
```

---

## Task 3: Frontend — RecoveryPage + recovery-mode wizard

**Files:**
- Create: `frontend/src/api/recovery.ts`
- Create: `frontend/src/pages/RecoveryPage.tsx`
- Modify: `frontend/src/api/setup.ts` (`SetupStatus` gains `recovery_mode: boolean`)
- Modify: `frontend/src/components/SetupGate.tsx` (render SetupPage on either `!setup_completed` or `recovery_mode`)
- Modify: `frontend/src/pages/SetupPage.tsx` (pre-fill + per-step save + step-4 variant)
- Modify: `frontend/src/App.tsx` (add `/recovery` route outside the gate)

No automated tests; manual browser verification.

### Step 3.1: API client + RecoveryPage

- [ ] **Step 1: `frontend/src/api/recovery.ts`**

```typescript
import { apiFetch } from "./client";

export interface RecoveryStatus {
  outstanding: boolean;
}

export function getRecoveryStatus(): Promise<RecoveryStatus> {
  return apiFetch<RecoveryStatus>("/recovery/status");
}

export function claimRecoveryToken(token: string): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>("/recovery/claim", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

// Clears the recovery cookie by setting it to expire immediately. The browser
// won't send it on the next request.
export function clearRecoveryCookie(): void {
  document.cookie = "recovery_token=; path=/; max-age=0; samesite=lax";
}
```

- [ ] **Step 2: `frontend/src/pages/RecoveryPage.tsx`**

Token entry form. On mount, calls `getRecoveryStatus`. If `outstanding=false`, shows "No recovery tokens have been issued. Ask the operator to run `skynetcontrol-recovery mint-admin-token` first." Otherwise renders a single input + Submit. Submit calls `claimRecoveryToken`; on success navigates to `/setup` (which now flips to recovery-mode because the cookie is set). On 400, shows "Invalid or expired token. Ask the operator for a fresh one."

URL query support: if `?token=...` is present in `location.search`, pre-fill the input. (The CLI prints a claim URL that includes the token; users can just click it.)

### Step 3.2: Routes + SetupGate

- [ ] **Step 3: `frontend/src/App.tsx`**

Add the `/recovery` route OUTSIDE the `<SetupGate>` wrap so it's always reachable.

- [ ] **Step 4: Update `SetupStatus` type + `SetupGate`**

In `frontend/src/api/setup.ts`:

```typescript
export interface SetupStatus {
  setup_completed: boolean;
  recovery_mode: boolean;
}
```

In `frontend/src/components/SetupGate.tsx`, change the rendering condition:

```typescript
if (!status.setup_completed || status.recovery_mode) {
  return <SetupPage recoveryMode={status.recovery_mode} />;
}
return <>{children}</>;
```

### Step 3.3: SetupPage recovery mode

- [ ] **Step 5: Modify `SetupPage.tsx`**

Add a `recoveryMode: boolean` prop. When `true`:

- On mount, fetch current state from existing endpoints and pre-fill `form`:
  - `default_net_control`, `net_address`, `app_base_url` from `GET /api/config/`
  - Existing OAuth providers via `listOAuthProviders()` — populate Step 2 from the FIRST enabled provider (recovery mode targets one provider at a time; if more than one is configured, the wizard's slug field stays editable so the operator can pick which to fix)
  - Existing SMTP via `getSmtp()` — populate Step 3
- Per-step Save semantics:
  - Step 1's Next button calls `setConfigValue("default_net_control", ...)` + `setConfigValue("net_address", ...)` + `setConfigValue("app_base_url", ...)`, then advances. (Uses the existing `/api/config/{key}` PUT endpoint, which already exists and is admin-gated → now also admits the recovery cookie via the gating swap in Task 2's Step 5 — but `/api/config/` is in `config_mgmt/routes.py` which Task 2 doesn't touch. We need to add it to the swap list. Adjust Task 2 Step 5 accordingly.)
  - Step 2's Next button calls `upsertOAuthProvider(form.oauth_slug, {...})`, then advances.
  - Step 3's Next/Skip button: Skip clears SMTP via `clearSmtp()`; Save calls `upsertSmtp({...})`. Then advances.
- Step 4 in recovery mode:
  - If the OAuth provider was edited (track an `oauthEdited` flag — flip to true on any change in Step 2's inputs after pre-fill), show "Sign in to verify [provider]". Clicking redirects to `/api/auth/login/<slug>` — the regular login route. After successful login, the user has a real admin session.
  - Otherwise show "Done" — clicking calls `clearRecoveryCookie()`, then `window.location.href = "/"` (the gate re-checks; recovery_mode is now false because the cookie is gone; falls through to the normal app).

### Step 3.4: Manual verification

- [ ] **Step 6: Dev-server end-to-end smoke**

```bash
./run-dev.sh
# In another terminal:
.venv/bin/python -c "from backend.cli.recovery import main; main(['mint-admin-token'])"
# Copy the printed URL, paste into browser.
# Confirm: /recovery accepts the token → redirects to /setup → wizard
# pre-fills with current config. Edit something (e.g. net address). Save
# step → confirm DB row updated. Click Done → cookie cleared → land on /.
```

- [ ] **Step 7: Build**

```bash
cd frontend && nix-shell -p nodejs_22 --run "npm run build"
```

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/recovery.ts frontend/src/api/setup.ts \
        frontend/src/pages/RecoveryPage.tsx frontend/src/pages/SetupPage.tsx \
        frontend/src/components/SetupGate.tsx frontend/src/App.tsx
git commit -m "feat(recovery): /recovery token entry + setup wizard recovery mode

New /recovery route shows a token-entry form (with ?token=... URL
pre-fill from the CLI's printed claim URL). On submit, POST to
/api/recovery/claim sets the recovery cookie and the SPA navigates
to /setup, where the wizard pre-fills steps 1-3 from current
AppConfig.

Each step's Save in recovery mode hits the existing CRUD endpoint
directly (no atomic-commit-at-step-4 model — admin can fix one
thing and leave). Step 4 shows Done when nothing OAuth-related was
edited; if the OAuth provider was touched, it shows Sign in to
verify and bounces through the real /api/auth/login flow, which
also clears the recovery cookie on success."
```

---

## Out of scope (handled in later phases)

- Removing OAuth/SMTP/related fields from Pydantic `Settings`, dropping the env scanner, removing `module.nix.settings` — **Phase 5**.
- Periodic cleanup of expired `admin_recovery_tokens` rows. Manual purge via SQL is fine for now; a cron sweep can land later.

**Phase 4 success criterion:** With a freshly broken OAuth provider on a working deployment (e.g. an admin manually corrupts a `client_secret` via the Config page and signs out), running `skynetcontrol-recovery mint-admin-token` on the host, opening the printed URL, fixing the credentials in the recovery wizard, and clicking "Sign in to verify" lands the operator back into a normal admin session — without ever needing to wipe `setup_completed` or reset the DB.
