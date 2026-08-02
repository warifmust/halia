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


def _default_resolver(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def _is_internal(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def check_egress(url: str, resolver: Resolver = _default_resolver) -> None:
    """Raise EgressDenied if `url` isn't a public http(s) address.

    Blocks non-http(s) schemes and any host that resolves to an internal/reserved IP
    (loopback, link-local incl. the cloud metadata IP, private ranges, …).
    """
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
        if _is_internal(ip):
            raise EgressDenied(
                f"blocked internal address {ip} for host '{host}' (SSRF floor)"
            )
