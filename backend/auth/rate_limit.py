"""In-memory per-IP token-bucket rate limiter.

Targets the handful of routes reachable without auth (login/callback,
setup/claim/start, recovery/claim+status) plus logout. The single-worker
NixOS deployment makes in-memory state correct: every request lands in
the same process, so a single bucket dict is consistent. A multi-worker
or replicated deployment would need to swap this for Redis-or-similar.

Design notes:
- Token bucket per (route_key, client_ip). Capacity is the burst size;
  refill_per_sec is the steady-state rate. A capacity of 10 with refill
  0.17/s means "10-request burst, then 10 per minute steady-state."
- `request.client.host` is the key, NOT X-Forwarded-For. With a trusted
  upstream proxy this collapses all requests to the proxy IP — that is
  fine for a single-admin app (it becomes a global limit). Don't trust
  X-F-F without a "trust this proxy IP" allowlist; that's a bigger
  feature than this audit pass.
- Dict cleanup: capped at _MAX_BUCKETS entries with TTL-based eviction
  when full. The cap is far above any legitimate workload; eviction
  protects against memory exhaustion from attacker IP rotation.
"""
from collections.abc import Callable
from time import monotonic

from fastapi import HTTPException, Request

_BUCKETS: dict[tuple[str, str], tuple[float, float]] = {}
_MAX_BUCKETS = 10_000
_BUCKET_TTL_SEC = 600.0


def _trusted_proxies(request: Request) -> set[str]:
    """Read the trusted-proxy allowlist from app.state.settings.

    Cached per-request via attribute lookup so we don't re-split on every
    bucket check. Empty allowlist disables proxy-header trust entirely
    (the safe default — without an allowlist, anyone can spoof
    X-Forwarded-For)."""
    try:
        raw = request.app.state.settings.trusted_proxies
    except AttributeError:
        return set()
    if not raw:
        return set()
    return {ip.strip() for ip in raw.split(",") if ip.strip()}


def _client_ip(request: Request) -> str:
    """Return the IP we'll bucket on.

    When the connecting peer is in the trusted-proxy allowlist, consult
    proxy-set headers in this order: CF-Connecting-IP (Cloudflare's
    canonical), X-Real-IP (nginx convention), X-Forwarded-For (rightmost
    public entry). Otherwise, ignore those headers — they're trivially
    spoofable when the peer isn't actually a proxy. Behind Cloudflare,
    this is the difference between a single shared bucket for every
    visitor (useless) and a per-user bucket (the intended defence).
    """
    if request.client is None:
        return "unknown"
    peer = request.client.host
    if peer in _trusted_proxies(request):
        for header in ("cf-connecting-ip", "x-real-ip"):
            v = request.headers.get(header, "").strip()
            if v:
                return v
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            # Right-most non-trusted entry. XFF is "client, proxy1, proxy2";
            # walking from the right skips any chained proxies that are
            # also in our allowlist.
            allowed = _trusted_proxies(request)
            for entry in reversed([e.strip() for e in xff.split(",") if e.strip()]):
                if entry not in allowed:
                    return entry
    return peer


def _maybe_sweep(now: float) -> None:
    if len(_BUCKETS) < _MAX_BUCKETS:
        return
    cutoff = now - _BUCKET_TTL_SEC
    expired = [k for k, (_, last) in _BUCKETS.items() if last < cutoff]
    for k in expired:
        del _BUCKETS[k]


def _consume(key: str, ip: str, capacity: float, refill_per_sec: float) -> bool:
    """Return True if a token was consumed; False if the bucket was empty."""
    now = monotonic()
    _maybe_sweep(now)
    tokens, last = _BUCKETS.get((key, ip), (capacity, now))
    tokens = min(capacity, tokens + (now - last) * refill_per_sec)
    if tokens < 1:
        _BUCKETS[(key, ip)] = (tokens, now)
        return False
    _BUCKETS[(key, ip)] = (tokens - 1, now)
    return True


def rate_limit(key: str, *, capacity: int, refill_per_sec: float) -> Callable:
    """FastAPI dependency: raise 429 if the per-IP token bucket is empty.

    `key` namespaces the bucket so different routes don't share quota.
    """

    def dep(request: Request) -> None:
        if not _consume(key, _client_ip(request), float(capacity), refill_per_sec):
            raise HTTPException(status_code=429, detail="Too many requests")

    return dep


def reset_for_tests() -> None:
    """Clear all buckets. Tests call this between scenarios so per-IP
    state from earlier tests doesn't leak into later ones."""
    _BUCKETS.clear()
