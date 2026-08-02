"""Tests for the network egress floor (SSRF protection)."""

import socket

import pytest

from halia.permissions.network import EgressDenied, check_egress
from halia.skills.web import FetchUrl


def _resolves_to(ip: str):
    return lambda host: [ip]


def test_public_address_allowed() -> None:
    check_egress("https://example.com/page", resolver=_resolves_to("93.184.216.34"))  # no raise


@pytest.mark.parametrize(
    "ip",
    ["169.254.169.254", "127.0.0.1", "10.0.0.1", "192.168.1.5", "172.16.0.1", "::1", "0.0.0.0"],
)
def test_internal_addresses_blocked(ip: str) -> None:
    with pytest.raises(EgressDenied):
        check_egress("http://whatever/", resolver=_resolves_to(ip))


def test_cloud_metadata_endpoint_blocked() -> None:
    # the classic SSRF target — must never be reachable
    with pytest.raises(EgressDenied, match="169.254.169.254"):
        check_egress(
            "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
            resolver=_resolves_to("169.254.169.254"),
        )


def test_dns_that_points_inside_is_blocked() -> None:
    # a public-looking host that resolves to a private IP (rebinding-style) is still caught
    with pytest.raises(EgressDenied):
        check_egress("http://sneaky.example.com/", resolver=_resolves_to("10.1.2.3"))


@pytest.mark.parametrize("url", ["ftp://host/x", "file:///etc/passwd", "gopher://x"])
def test_non_http_schemes_blocked(url: str) -> None:
    with pytest.raises(EgressDenied):
        check_egress(url, resolver=_resolves_to("8.8.8.8"))


def test_missing_host_blocked() -> None:
    with pytest.raises(EgressDenied):
        check_egress("http://", resolver=_resolves_to("8.8.8.8"))


def test_resolution_failure_blocked() -> None:
    def boom(host: str):
        raise socket.gaierror("nope")

    with pytest.raises(EgressDenied):
        check_egress("http://nx.invalid/", resolver=boom)


def test_fetch_url_blocks_metadata_ip() -> None:
    # literal internal IP → getaddrinfo resolves it offline → blocked before any request
    out = FetchUrl().run({"url": "http://169.254.169.254/latest/meta-data/"})
    assert out.startswith("blocked:")


def test_fetch_url_blocks_localhost() -> None:
    out = FetchUrl().run({"url": "http://localhost:8080/admin"})
    assert out.startswith("blocked:")
