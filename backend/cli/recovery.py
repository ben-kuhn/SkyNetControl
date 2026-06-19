"""skynetcontrol-recovery — break-glass admin recovery token management.

Subcommands:
  mint-admin-token [--ttl 10m]    — generate a token, print plaintext + claim URL
  list-tokens                     — show outstanding (unused, unexpired) tokens, no plaintext
  revoke <prefix>                 — mark all unused tokens with the given hash prefix as used
  rotate-secrets                  — re-encrypt any plaintext oauth/smtp credentials in app_config
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from datetime import timedelta
from typing import Sequence

from backend.auth.recovery import (
    list_outstanding,
    mint_token,
    revoke_by_prefix,
)
from backend.auth.secret_box import (
    _PREFIX as ENC_PREFIX,
    decrypt_with_key,
    encrypt,
    install_key_material,
)
from backend.config import Settings
import backend.auth.models  # noqa: F401 — ensure AdminRecoveryToken model is registered
from backend.config_mgmt.models import AppConfig
from backend.db.session import create_engine_from_url, create_session_factory


# Keys whose values should be encrypted at rest. Mirrors the typed routes
# (oauth_routes, smtp_routes) and the routes.py bulk-PUT sensitivity check.
def _is_sensitive_key(key: str) -> bool:
    lk = key.lower()
    return any(fragment in lk for fragment in ("api_key", "password", "secret", "token"))


def _rotate_secrets(db, from_key: str | None = None) -> tuple[int, int, int, int]:
    """Walk app_config, re-encrypt any sensitive key under the current key.

    Returns (re_encrypted, already_encrypted, migrated_from_old, unrecoverable):
    - Plaintext sensitive row → encrypted under current key. → re_encrypted.
    - Envelope-encrypted row decryptable under current key → untouched. → already.
    - Envelope NOT decryptable under current key, from_key supplied,
      decryptable under from_key → re-encrypted under current. → migrated.
    - Envelope decryptable under neither key (or no from_key supplied) →
      left untouched. → unrecoverable.

    Idempotent without from_key: a second run does nothing destructive.
    The CLI surfaces a non-zero exit code when `unrecoverable` is non-zero
    so scripted callers don't silently miss the failure.
    """
    rows = (
        db.query(AppConfig)
        .filter(AppConfig.value.isnot(None))
        .all()
    )
    re_encrypted = 0
    already = 0
    migrated = 0
    unrecoverable = 0
    # The current installed key — read once. install_key_material was
    # called in main() before we ever reach here, so this is never None
    # in production, but defensive against test paths that don't init.
    from backend.auth.secret_box import _key_material as _current_material

    for row in rows:
        if not _is_sensitive_key(row.key) or not row.value:
            continue
        if not row.value.startswith(ENC_PREFIX):
            # Plaintext legacy row — encrypt under the current key.
            row.value = encrypt(row.value)
            re_encrypted += 1
            continue
        # Envelope present. Try the current key. decrypt_with_key returns
        # None on InvalidTag, so a None result definitively means "this
        # row is not under our current key" — without conflating it with
        # legitimately-empty plaintext (which would never be enveloped).
        decrypted_current = (
            decrypt_with_key(row.value, _current_material) if _current_material else None
        )
        if decrypted_current is not None:
            already += 1
            continue
        # Not under current key. Try from_key if provided.
        if from_key is not None:
            decrypted_old = decrypt_with_key(row.value, from_key)
            if decrypted_old is not None:
                row.value = encrypt(decrypted_old)
                migrated += 1
                continue
        # Encrypted under neither current nor from_key (or no from_key).
        # Leave the row alone; the CLI exit code + stderr warn the caller.
        unrecoverable += 1
    if re_encrypted or migrated:
        db.commit()
    if unrecoverable:
        print(
            f"WARNING: {unrecoverable} encrypted row(s) could not be decrypted "
            f"under the current key{' or the supplied --from-key' if from_key else ''}. "
            "Re-enter affected credentials via the admin config page.",
            file=sys.stderr,
        )
    return re_encrypted, already, migrated, unrecoverable


def _parse_ttl(spec: str) -> timedelta:
    m = re.fullmatch(r"(\d+)([smhd])", spec)
    if not m:
        raise argparse.ArgumentTypeError(f"bad TTL {spec!r}; use forms like 10m, 1h, 30s")
    n, unit = int(m.group(1)), m.group(2)
    return {"s": timedelta(seconds=n), "m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[
        unit
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skynetcontrol-recovery")
    sub = parser.add_subparsers(dest="cmd", required=True)

    mint = sub.add_parser("mint-admin-token", help="Mint a single-use admin recovery token.")
    mint.add_argument(
        "--ttl",
        default="10m",
        type=_parse_ttl,
        help="How long the token is valid (e.g. 10m, 1h). Default 10m.",
    )

    sub.add_parser("list-tokens", help="Show outstanding (unused, unexpired) tokens. No plaintext.")

    revoke = sub.add_parser("revoke", help="Mark all unused tokens with this hash prefix as used.")
    revoke.add_argument("prefix", help="Hash prefix (8+ hex chars recommended).")

    rotate = sub.add_parser(
        "rotate-secrets",
        help="Re-encrypt any plaintext OAuth/SMTP credentials still in app_config.",
    )
    rotate.add_argument(
        "--from-key",
        default=None,
        help=(
            "Old SKYNET_SECRETS_KEY value. When supplied, rows that don't decrypt "
            "under the current key are tried under this one, then re-encrypted. "
            "Use after rotating SKYNET_SECRETS_KEY to migrate existing credentials."
        ),
    )

    args = parser.parse_args(argv)

    settings = Settings()
    database_url = os.environ.get("SKYNET_DATABASE_URL", settings.database_url)
    # The CLI bypasses create_app(), so bind the secret_box key explicitly
    # — encrypt() and decrypt() refuse to operate otherwise. Same fallback
    # rule as create_app (SKYNET_SECRETS_KEY preferred, JWT secret used
    # when unset).
    install_key_material(settings.secrets_key or settings.jwt_secret_key)
    engine = create_engine_from_url(database_url)
    # No schema setup here — `skynetcontrol-alembic upgrade head` runs as
    # ExecStartPre on the service, and tests apply Base.metadata.create_all
    # before invoking main(). The CLI should operate against the DB as it is.
    session_factory = create_session_factory(engine)

    with session_factory() as db:
        if args.cmd == "mint-admin-token":
            plaintext, expires_at = mint_token(db, ttl=args.ttl)
            hash_prefix = hashlib.sha256(plaintext.encode()).hexdigest()[:8]
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
            try:
                count = revoke_by_prefix(db, args.prefix)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(f"Revoked {count} token(s) matching prefix {args.prefix!r}.")
            return 0

        if args.cmd == "rotate-secrets":
            re_encrypted, already, migrated, unrecoverable = _rotate_secrets(
                db, from_key=args.from_key
            )
            parts = [f"Re-encrypted {re_encrypted} row(s) from plaintext"]
            if args.from_key is not None:
                parts.append(f"migrated {migrated} row(s) from --from-key")
            parts.append(f"{already} were already encrypted under current key")
            print("; ".join(parts) + ".")
            if re_encrypted == 0 and already == 0 and migrated == 0 and unrecoverable == 0:
                print("No sensitive AppConfig rows found.")
            # Non-zero exit when any row could not be migrated, so scripted
            # callers don't silently miss the failure. The stderr WARNING
            # already explains the recovery path.
            return 3 if unrecoverable else 0

    # Argparse with required=True already exits non-zero for unknown subcommands;
    # this is a defensive fallthrough.
    return 1


if __name__ == "__main__":
    sys.exit(main())
