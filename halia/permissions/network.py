"""Egress floor — the network side of the permission floor.

An always-on guard for outbound HTTP: even "safe" read-only web skills must not be
steered (e.g. by a prompt-injected page) into reaching *internal* addresses — the
cloud metadata endpoint (169.254.169.254 → credentials), localhost services, or
private LAN hosts. This is the SSRF floor. A per-profile domain allow/deny list is
the finer leash that builds on top later.

Note: the host is resolved and its IPs checked here. This blocks the obvious SSRF
cases; a determined DNS-rebinding attack (resolve public here, private at connect
time) is out of scope for this floor — pin-and-connect is a later hardening.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse

# A resolver takes a host and returns its IP strings (injectable for tests).
Resolver = Callable[[str], list[str]]


class EgressDenied(RuntimeError):
    """Raised when an outbound request is blocked by the egress floor."""


# Process-wide opt-in for reaching loopback/private addresses (local dev-server testing).
# Off by default (SSRF-safe); the user turns it on explicitly (`--allow-local` / `/local`),
# so prompt-injected content can never enable it. Cloud-metadata / link-local stays blocked
# even when this is on.
_ALLOW_LOCAL = False


def set_allow_local(value: bool) -> None:
    """Enable/disable reaching loopback + private addresses for this process."""
    global _ALLOW_LOCAL
    _ALLOW_LOCAL = value


def allow_local_enabled() -> bool:
    return _ALLOW_LOCAL


def _default_resolver(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def _blocked(ip: str, allow_local: bool) -> bool:
    """Is `ip` off-limits? Loopback/private are relaxable via allow_local; the rest never are.

    Order matters: loopback (127.0.0.1, ::1) is checked first because Python also flags ::1
    as reserved; link-local (the 169.254 cloud-metadata range) is checked before private so
    it stays blocked even with allow_local (Python counts link-local as private too).
    """
    addr = ipaddress.ip_address(ip)
    # Loopback — a local dev server. Blocked by default, relaxable with allow_local.
    if addr.is_loopback:
        return not allow_local
    # Never reachable, even with allow_local: cloud metadata (link-local), multicast,
    # the unspecified address — the genuinely dangerous SSRF targets.
    if addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        return True
    # Private LAN (10/8, 192.168, 172.16, fc00::) — a dev/staging box. Relaxable.
    if addr.is_private:
        return not allow_local
    # Anything else reserved stays blocked.
    return bool(addr.is_reserved)


def check_egress(
    url: str, resolver: Resolver = _default_resolver, allow_local: bool | None = None
) -> None:
    """Raise EgressDenied if `url` isn't a reachable http(s) address.

    Blocks non-http(s) schemes and internal/reserved IPs. Loopback + private ranges are
    blocked unless local access is enabled (arg overrides the process default set by
    `set_allow_local`); link-local (incl. the cloud metadata IP) is always blocked.
    """
    if allow_local is None:
        allow_local = _ALLOW_LOCAL
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise EgressDenied(f"scheme '{parsed.scheme or '(none)'}' is not allowed (http/https only)")
    host = parsed.hostname
    if not host:
        raise EgressDenied("no host in URL")

    try:
        ips = resolver(host)
    except OSError as exc:
        raise EgressDenied(f"could not resolve host '{host}': {exc}") from exc
    if not ips:
        raise EgressDenied(f"could not resolve host '{host}'")

    for ip in ips:
        if _blocked(ip, allow_local):
            addr = ipaddress.ip_address(ip)
            hint = ""
            if not allow_local and (addr.is_loopback or addr.is_private):
                hint = " — enable local testing with --allow-local (or /local in the TUI)"
            raise EgressDenied(
                f"blocked internal address {ip} for host '{host}' (SSRF floor){hint}"
            )
