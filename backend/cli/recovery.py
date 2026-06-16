"""skynetcontrol-recovery — break-glass admin recovery token management.

Subcommands:
  mint-admin-token [--ttl 10m]    — generate a token, print plaintext + claim URL
  list-tokens                     — show outstanding (unused, unexpired) tokens, no plaintext
  revoke <prefix>                 — mark all unused tokens with the given hash prefix as used
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
from backend.config import Settings
import backend.auth.models  # noqa: F401 — ensure AdminRecoveryToken model is registered
from backend.db.session import create_engine_from_url, create_session_factory


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

    args = parser.parse_args(argv)

    database_url = os.environ.get("SKYNET_DATABASE_URL", Settings().database_url)
    engine = create_engine_from_url(database_url)
    # No schema setup here — `skynetcontrol-alembic upgrade head` runs as
    # ExecStartPre on the service, and tests apply Base.metadata.create_all
    # before invoking main(). The CLI should operate against the DB as it is.
    session_factory = create_session_factory(engine)

    with session_factory() as db:
        if args.cmd == "mint-admin-token":
            plaintext, expires_at = mint_token(db, ttl=args.ttl)
            hash_prefix = hashlib.sha256(plaintext.encode()).hexdigest()[:8]
            settings = Settings()
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

    # Argparse with required=True already exits non-zero for unknown subcommands;
    # this is a defensive fallthrough.
    return 1


if __name__ == "__main__":
    sys.exit(main())
