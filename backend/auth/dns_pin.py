"""Pin a hostname to a pre-resolved IP for outbound httpx fetches.

Closes the residual DNS-rebinding TOCTOU in the SSRF guard: the guard
resolves `host` once and confirms the IP is global. Without this module
the actual TCP connect re-resolves through `socket.getaddrinfo`, and an
attacker controlling DNS could swap in a private IP between the check
and the fetch. With it, the guard hands the resolved IP to a context-
bound override that returns exactly that IP for the duration of the
fetch — httpx/httpcore/anyio's network stack all go through
`socket.getaddrinfo`, so a single patched call covers everything.

TLS / cert validation is untouched: the request URL still carries the
original hostname, so SNI and X.509 subject verification use the
hostname (not the IP). Only the TCP-layer IP lookup is pinned.

Concurrency: the pin lives in a ContextVar, which `asyncio.to_thread`
propagates to the worker thread (CPython 3.9+). Multiple concurrent
requests each see their own pin; the active set is per-task, not
global.

Installation is idempotent — the patch is module-level and applied
exactly once at import time.
"""
import contextlib
import contextvars
import ipaddress
import socket
from typing import Iterator


# {ace_hostname: pinned_ip}. Empty / unset means no pinning active
# for this task — fall through to the system resolver. Keys are stored
# in IDNA-encoded ("ace") form so the pin survives Unicode-vs-punycode
# round-trips between urlparse() (which returns Unicode for IDNs) and
# httpx/anyio (which call getaddrinfo with the ace form).
_PINS: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("_PINS", default={})


def _to_ace(hostname: str) -> str:
    """Return the lower-cased ASCII-Compatible-Encoding form of `hostname`.

    For ASCII hosts this is just `.lower()`. For IDN hosts (anything
    containing non-ASCII), encode via IDNA — which is exactly what
    httpx will do internally before calling getaddrinfo. On encode
    failure, fall back to lowercase so the lookup can still match in
    the common ASCII path.
    """
    try:
        return hostname.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return hostname.lower()


def _normalise_host(host) -> str:
    """Accept str or bytes; getaddrinfo's `host` argument may be either."""
    if isinstance(host, bytes):
        return host.decode("ascii", errors="ignore")
    return host or ""


_INSTALLED = False
_ORIGINAL_GETADDRINFO = socket.getaddrinfo


def _patched_getaddrinfo(host, port, *args, **kwargs):
    pins = _PINS.get()
    if pins:
        # Normalise the caller's hostname into the same shape the pin
        # uses for the dict key — lowercased ace form. httpx passes the
        # already-encoded ace form, so this mostly just lowercases.
        key = _to_ace(_normalise_host(host))
        ip = pins.get(key)
        if ip is not None:
            # Reflect the address family the pinned IP actually belongs to.
            # IPv4 returns the 2-tuple sockaddr; IPv6 needs the 4-tuple
            # (addr, port, flowinfo, scopeid). Without this an IPv6-only
            # OIDC endpoint would silently fail to connect.
            try:
                addr_obj = ipaddress.ip_address(ip)
            except ValueError:
                # Shouldn't happen — the SSRF guard validates this — but
                # if it does, fall through to the real resolver so the
                # request is observable rather than silently broken.
                return _ORIGINAL_GETADDRINFO(host, port, *args, **kwargs)
            if isinstance(addr_obj, ipaddress.IPv6Address):
                return [
                    (
                        socket.AF_INET6,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        (ip, port or 0, 0, 0),
                    )
                ]
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (ip, port or 0),
                )
            ]
    return _ORIGINAL_GETADDRINFO(host, port, *args, **kwargs)


def install_resolver_patch() -> None:
    """Monkey-patch socket.getaddrinfo to consult the per-task pin map.

    Idempotent: re-import or repeated calls are safe; the patch is only
    applied once. Removing the patch isn't supported — it lives for the
    process lifetime.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    socket.getaddrinfo = _patched_getaddrinfo
    _INSTALLED = True


@contextlib.contextmanager
def pin_dns(hostname: str, ip: str) -> Iterator[None]:
    """Context manager: while active, getaddrinfo for `hostname` returns `ip`.

    Use after the SSRF guard has verified `ip` is global. Wrap the httpx
    fetch inside the `with` block — the override applies to the calling
    task and to any thread it dispatches via asyncio.to_thread.

    Hostname is stored in IDNA ace form so the pin matches the
    httpx-supplied lookup key regardless of which side did the encoding.
    """
    install_resolver_patch()
    current = _PINS.get()
    new = {**current, _to_ace(hostname): ip}
    token = _PINS.set(new)
    try:
        yield
    finally:
        _PINS.reset(token)
