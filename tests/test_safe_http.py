import socket

import pytest

from repocapsule.core.safe_http import PrivateAddressBlocked, SafeHttpClient


def test_safe_http_blocks_private_ip(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("10.0.0.1", port),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    client = SafeHttpClient()
    with pytest.raises(PrivateAddressBlocked):
        client._resolve_ips("example.com")


def test_safe_http_allows_public_ip(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("8.8.8.8", port),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    client = SafeHttpClient()
    infos = client._resolve_ips("example.com")
    assert infos == ["8.8.8.8"]


@pytest.mark.parametrize(
    "src, dest, expected",
    [
        ("example.com", "example.com", True),
        ("example.com", "www.example.com", True),
        ("www.example.com", "example.com", True),
        ("sub.example.com", "example.com", True),
        ("example.com", "sub.example.net", False),
        ("github.com", "docs.github.com", True),
        ("example.com", "malicious.com", False),
    ],
)
def test_hosts_related(src, dest, expected):
    client = SafeHttpClient(allowed_redirect_suffixes=("github.com",))
    assert client._hosts_related(src, dest) is expected
