"""Tests for the per-IP token-bucket rate limiter."""
from time import monotonic

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.auth import rate_limit as rl


def _app(key: str, capacity: int, refill_per_sec: float) -> FastAPI:
    app = FastAPI()

    @app.get("/", dependencies=[Depends(rl.rate_limit(key, capacity=capacity, refill_per_sec=refill_per_sec))])
    def root():
        return {"ok": True}

    return app


def test_token_bucket_allows_burst_then_blocks():
    rl.reset_for_tests()
    client = TestClient(_app("burst", capacity=3, refill_per_sec=0.01))
    # First 3 succeed (full bucket).
    for _ in range(3):
        assert client.get("/").status_code == 200
    # Fourth in the burst window 429s — refill rate is too slow to mint
    # a fresh token in the test's wall-clock duration.
    assert client.get("/").status_code == 429


def test_separate_keys_have_separate_buckets():
    """A bucket starved on route X must NOT 429 a request to route Y.

    This is the whole point of the namespace key — high-traffic endpoints
    shouldn't starve infrequent ones (or vice versa)."""
    rl.reset_for_tests()
    a = TestClient(_app("ka", capacity=1, refill_per_sec=0.01))
    b = TestClient(_app("kb", capacity=1, refill_per_sec=0.01))
    assert a.get("/").status_code == 200
    assert a.get("/").status_code == 429
    # b's bucket is untouched.
    assert b.get("/").status_code == 200


def test_refill_restores_quota():
    """After refill, the bucket admits a new request."""
    rl.reset_for_tests()
    # 100 tokens per second refill — bucket recovers fast enough for the
    # test to observe without sleeping a full second.
    client = TestClient(_app("refill", capacity=1, refill_per_sec=100.0))
    assert client.get("/").status_code == 200
    assert client.get("/").status_code == 429
    # Sleep just enough for refill to mint a fresh token.
    start = monotonic()
    while monotonic() - start < 0.05:
        pass
    assert client.get("/").status_code == 200


def test_unknown_client_doesnt_crash():
    """request.client can be None in some ASGI scenarios; the limiter
    must not blow up — it just attributes to a synthetic "unknown" IP."""
    rl.reset_for_tests()
    # _client_ip handles request.client is None; we hit the function
    # directly to cover the branch without needing a non-standard ASGI env.
    class _Stub:
        client = None
    assert rl._client_ip(_Stub()) == "unknown"
