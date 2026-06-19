"""Tests for the OIDC id_token verifier.

Generates an RSA keypair per-test, signs a JWT with python-jose under
RS256, and exercises the verifier against the resulting token. Network
is mocked at the JWKS-fetch boundary so no real OIDC provider is hit.
"""
import base64
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from backend.auth import oidc_verify


def _make_rsa_jwk():
    """Generate an RSA keypair and return (private_pem, jwks_entry).

    The JWK is the form the verifier consumes from a JWKS endpoint.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub = private_key.public_key().public_numbers()
    # n and e are base64url-encoded big-endian integers, no padding.
    def b64u_uint(n: int) -> str:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")

    jwk = {
        "kty": "RSA",
        "kid": "test-key-1",
        "alg": "RS256",
        "use": "sig",
        "n": b64u_uint(pub.n),
        "e": b64u_uint(pub.e),
    }
    return private_pem, jwk


def _sign_id_token(private_pem: bytes, claims: dict) -> str:
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": "test-key-1"})


@pytest.fixture(autouse=True)
def _clean_jwks_cache():
    oidc_verify.reset_for_tests()
    yield
    oidc_verify.reset_for_tests()


@pytest.mark.asyncio
async def test_verify_happy_path():
    """A correctly signed token with matching iss/aud/nonce verifies."""
    private_pem, jwk = _make_rsa_jwk()
    token = _sign_id_token(
        private_pem,
        {
            "iss": "https://idp.example.com",
            "aud": "test-client",
            "sub": "user-1",
            "nonce": "the-nonce",
            "exp": 9999999999,
        },
    )

    with patch.object(oidc_verify, "_fetch_jwks", new=AsyncMock(return_value=[jwk])):
        claims = await oidc_verify.verify_id_token(
            token,
            expected_issuer="https://idp.example.com",
            expected_audience="test-client",
            expected_nonce="the-nonce",
            jwks_uri="https://idp.example.com/jwks",
        )
    assert claims is not None
    assert claims["sub"] == "user-1"


@pytest.mark.asyncio
async def test_verify_rejects_alg_none():
    """`alg: none` (the classic signature-bypass) must be refused outright,
    even before signature verification — the JWKS allowlist excludes it."""
    # Manually craft an alg=none token. python-jose won't sign one for us.
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT","kid":"test-key-1"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(b'{"iss":"https://idp.example.com","aud":"test-client","sub":"u"}').decode().rstrip("=")
    token = f"{header}.{body}."

    _, jwk = _make_rsa_jwk()
    with patch.object(oidc_verify, "_fetch_jwks", new=AsyncMock(return_value=[jwk])):
        claims = await oidc_verify.verify_id_token(
            token,
            expected_issuer="https://idp.example.com",
            expected_audience="test-client",
            expected_nonce="",
            jwks_uri="https://idp.example.com/jwks",
        )
    assert claims is None


@pytest.mark.asyncio
async def test_verify_rejects_hs256():
    """HMAC algorithms aren't in the asymmetric whitelist. A token signed
    under HS256 with a guessed/known secret must be refused."""
    token = jwt.encode(
        {"iss": "https://idp.example.com", "aud": "test-client", "sub": "u", "exp": 9999999999},
        "shared-secret",
        algorithm="HS256",
        headers={"kid": "test-key-1"},
    )

    _, jwk = _make_rsa_jwk()
    with patch.object(oidc_verify, "_fetch_jwks", new=AsyncMock(return_value=[jwk])):
        claims = await oidc_verify.verify_id_token(
            token,
            expected_issuer="https://idp.example.com",
            expected_audience="test-client",
            expected_nonce="",
            jwks_uri="https://idp.example.com/jwks",
        )
    assert claims is None


@pytest.mark.asyncio
async def test_verify_rejects_nonce_mismatch():
    private_pem, jwk = _make_rsa_jwk()
    token = _sign_id_token(
        private_pem,
        {
            "iss": "https://idp.example.com",
            "aud": "test-client",
            "sub": "u",
            "nonce": "OLD-nonce",
            "exp": 9999999999,
        },
    )

    with patch.object(oidc_verify, "_fetch_jwks", new=AsyncMock(return_value=[jwk])):
        claims = await oidc_verify.verify_id_token(
            token,
            expected_issuer="https://idp.example.com",
            expected_audience="test-client",
            expected_nonce="NEW-nonce",
            jwks_uri="https://idp.example.com/jwks",
        )
    assert claims is None


@pytest.mark.asyncio
async def test_verify_rejects_wrong_audience():
    private_pem, jwk = _make_rsa_jwk()
    token = _sign_id_token(
        private_pem,
        {
            "iss": "https://idp.example.com",
            "aud": "OTHER-client",
            "sub": "u",
            "exp": 9999999999,
        },
    )

    with patch.object(oidc_verify, "_fetch_jwks", new=AsyncMock(return_value=[jwk])):
        claims = await oidc_verify.verify_id_token(
            token,
            expected_issuer="https://idp.example.com",
            expected_audience="test-client",
            expected_nonce="",
            jwks_uri="https://idp.example.com/jwks",
        )
    assert claims is None


def _compute_at_hash(access_token: str) -> str:
    """Compute the RS256 at_hash per OpenID Connect Core 3.1.3.6:
    base64url-no-padding of the left half of SHA-256(access_token).
    """
    import hashlib

    digest = hashlib.sha256(access_token.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest[: len(digest) // 2]).decode("ascii").rstrip("=")


@pytest.mark.asyncio
async def test_verify_at_hash_requires_access_token():
    """An id_token carrying at_hash (Google does) needs the access_token
    threaded through, or jose refuses it. Regression for the prod log
    "No access_token provided to compare against at_hash claim."
    """
    private_pem, jwk = _make_rsa_jwk()
    access_token = "the-real-access-token"
    token = _sign_id_token(
        private_pem,
        {
            "iss": "https://idp.example.com",
            "aud": "test-client",
            "sub": "u",
            "exp": 9999999999,
            "at_hash": _compute_at_hash(access_token),
        },
    )

    # Without access_token → jose raises, verifier returns None.
    with patch.object(oidc_verify, "_fetch_jwks", new=AsyncMock(return_value=[jwk])):
        claims = await oidc_verify.verify_id_token(
            token,
            expected_issuer="https://idp.example.com",
            expected_audience="test-client",
            expected_nonce="",
            jwks_uri="https://idp.example.com/jwks",
        )
    assert claims is None

    # With matching access_token → passes.
    with patch.object(oidc_verify, "_fetch_jwks", new=AsyncMock(return_value=[jwk])):
        claims = await oidc_verify.verify_id_token(
            token,
            expected_issuer="https://idp.example.com",
            expected_audience="test-client",
            expected_nonce="",
            jwks_uri="https://idp.example.com/jwks",
            access_token=access_token,
        )
    assert claims is not None
    assert claims["sub"] == "u"

    # Wrong access_token → at_hash mismatch, verifier returns None.
    with patch.object(oidc_verify, "_fetch_jwks", new=AsyncMock(return_value=[jwk])):
        claims = await oidc_verify.verify_id_token(
            token,
            expected_issuer="https://idp.example.com",
            expected_audience="test-client",
            expected_nonce="",
            jwks_uri="https://idp.example.com/jwks",
            access_token="some-other-token",
        )
    assert claims is None


@pytest.mark.asyncio
async def test_verify_jwks_unreachable_returns_none():
    """If the JWKS endpoint can't be reached, the verifier refuses the
    token rather than admitting an unverified one."""
    private_pem, _jwk = _make_rsa_jwk()
    token = _sign_id_token(
        private_pem,
        {"iss": "https://idp.example.com", "aud": "test-client", "sub": "u", "exp": 9999999999},
    )

    with patch.object(oidc_verify, "_fetch_jwks", new=AsyncMock(return_value=None)):
        claims = await oidc_verify.verify_id_token(
            token,
            expected_issuer="https://idp.example.com",
            expected_audience="test-client",
            expected_nonce="",
            jwks_uri="https://idp.example.com/jwks",
        )
    assert claims is None
