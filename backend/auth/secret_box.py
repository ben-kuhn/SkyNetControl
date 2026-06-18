"""AEAD envelope for secrets stored in the AppConfig table.

Threat model: an attacker reads `skynetcontrol.db` at rest (backup theft,
hostile maintainer of the container host). Without this layer, OAuth
provider client secrets and SMTP passwords are recoverable byte-for-byte
from the dump — a fresh deploy with the same DB resumes authenticated
outbound calls under the operator's identity.

Design:
- AES-256-GCM via cryptography.hazmat (pulled in transitively by
  python-jose[cryptography], already a runtime dep).
- 32-byte key derived from the JWT signing secret via HKDF-SHA256 with a
  fixed salt and `info="skynetcontrol-app-config-encryption-v1"` for
  domain separation from any other key the JWT secret might later seed.
  Reusing the JWT secret avoids a second LoadCredential rotation; the
  cost is that rotating the JWT secret also forces operators to re-enter
  saved IdP / SMTP credentials (or rotate them via the wizard's
  preserve-on-empty sentinel).
- Stored shape: `enc:v1:<urlsafe-b64(nonce || ciphertext+tag)>`.
- decrypt() returns input unchanged when the prefix is absent so existing
  plaintext rows keep working until the next admin save re-encrypts them.

Lifecycle:
- create_app() installs the key material via `install_key_material`.
- Tests do the same in their conftest, since many bypass create_app.
- All other code (oauth.py, smtp.py) just calls encrypt/decrypt.
"""
import base64
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_PREFIX = "enc:v1:"
_HKDF_SALT = b"skynetcontrol-secretbox-salt-v1"
_HKDF_INFO = b"skynetcontrol-app-config-encryption-v1"

_key_material: str | None = None


def install_key_material(material: str) -> None:
    """Bind the process-wide secret-box key material.

    Called from create_app() with settings.jwt_secret_key. Idempotent;
    repeated calls with the same value are a no-op, with a different
    value they rebind (tests run multiple app instances in one process).
    """
    global _key_material
    _key_material = material


def _derive_key() -> bytes:
    if _key_material is None:
        raise RuntimeError(
            "secret_box not initialized — call install_key_material() from create_app() or conftest"
        )
    kdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=_HKDF_SALT, info=_HKDF_INFO)
    return kdf.derive(_key_material.encode("utf-8"))


def encrypt(plaintext: str) -> str:
    """Encrypt `plaintext` and return the `enc:v1:` envelope.

    Empty input round-trips as empty — there's nothing to protect, and
    matching get/upsert paths use "" as the "no value" sentinel.
    """
    if not plaintext:
        return ""
    nonce = os.urandom(12)
    ct = AESGCM(_derive_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return _PREFIX + base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def decrypt(stored: str) -> str:
    """Decrypt an `enc:v1:` envelope, or return the input unchanged.

    The passthrough branch supports installs whose existing rows are
    plaintext from before this module landed; those rows re-encrypt on
    the next admin save (oauth/smtp upsert paths always encrypt).
    """
    if not stored or not stored.startswith(_PREFIX):
        return stored
    blob = base64.urlsafe_b64decode(stored[len(_PREFIX):].encode("ascii"))
    nonce, ct = blob[:12], blob[12:]
    return AESGCM(_derive_key()).decrypt(nonce, ct, None).decode("utf-8")
