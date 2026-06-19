"""Tests for backend/auth/dns_pin.py.

The pin's job is to make socket.getaddrinfo return a fixed IP for a
specific hostname while a context is active — and to leave the system
resolver alone for everything else. We don't open a real TCP socket;
verifying that getaddrinfo returns the pinned value is sufficient, since
httpx/anyio call exactly that function during their connect.
"""
import socket

import pytest

from backend.auth.dns_pin import install_resolver_patch, pin_dns


@pytest.fixture(autouse=True)
def _install_patch():
    """The patch is installed implicitly by pin_dns(); calling it here
    just makes the test ordering robust if a future test imports the
    module after manipulating socket.getaddrinfo directly."""
    install_resolver_patch()


def test_pin_overrides_getaddrinfo_for_named_host():
    with pin_dns("evil.example.com", "203.0.113.7"):
        result = socket.getaddrinfo("evil.example.com", 443)
    assert any(addr[4][0] == "203.0.113.7" for addr in result)


def test_pin_does_not_override_other_hosts():
    """A pin for one host must not affect lookups for another."""
    # Use a literal IP for the unrelated lookup so we don't hit DNS in CI.
    with pin_dns("evil.example.com", "203.0.113.7"):
        # 127.0.0.1 resolves to itself via the system stub resolver and
        # is intentionally NOT the pinned IP — proves the override is
        # scoped to the hostname.
        result = socket.getaddrinfo("127.0.0.1", 443)
    assert all(addr[4][0] != "203.0.113.7" for addr in result)


def test_pin_clears_on_context_exit():
    """After the context ends, the pin must NOT continue overriding."""
    with pin_dns("evil.example.com", "203.0.113.7"):
        pass
    # Outside the context, the pin is gone — getaddrinfo for the hostname
    # would fall through to real DNS. We don't make that call (CI may lack
    # DNS); instead, we verify the contextvar map is empty.
    from backend.auth.dns_pin import _PINS

    assert _PINS.get() == {}


def test_pin_returns_ipv6_family_for_ipv6_pin():
    """A v6 pinned IP must come back with AF_INET6 + 4-tuple sockaddr.

    Without this, httpx would receive an AF_INET-tagged v6 string and
    `socket.connect` would refuse it; v6-only OIDC providers would be
    unreachable under the pin.
    """
    with pin_dns("v6.example", "2001:db8::1"):
        result = socket.getaddrinfo("v6.example", 443)
    family, _socktype, _proto, _canon, sockaddr = result[0]
    assert family == socket.AF_INET6
    # IPv6 sockaddr is (addr, port, flowinfo, scopeid).
    assert sockaddr[0] == "2001:db8::1"
    assert len(sockaddr) == 4


def test_pin_idna_round_trip():
    """An IDN hostname goes in as Unicode but httpx will look it up in
    its ace form. The pin must match either spelling."""
    # bücher.example → xn--bcher-kva.example
    unicode_host = "bücher.example"
    ace_host = unicode_host.encode("idna").decode("ascii").lower()

    with pin_dns(unicode_host, "203.0.113.99"):
        # httpx-style lookup: ace form.
        ace_result = socket.getaddrinfo(ace_host, 443)
    assert any(addr[4][0] == "203.0.113.99" for addr in ace_result)


def test_pin_nested_contexts_combine():
    """Nested pin_dns contexts both apply; exit restores the prior frame."""
    with pin_dns("host-a.example", "203.0.113.1"):
        with pin_dns("host-b.example", "203.0.113.2"):
            a = socket.getaddrinfo("host-a.example", 0)
            b = socket.getaddrinfo("host-b.example", 0)
        # After exiting the inner context, the outer pin is still active.
        c = socket.getaddrinfo("host-a.example", 0)
    assert any(addr[4][0] == "203.0.113.1" for addr in a)
    assert any(addr[4][0] == "203.0.113.2" for addr in b)
    assert any(addr[4][0] == "203.0.113.1" for addr in c)
