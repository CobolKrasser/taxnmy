"""Only public addresses (TAX-34, K4): a source must not point the reader to a place it does not belong.

Two checks, one for each place that needs one:

    problem(url)         without the network: the scheme is https or http, the host is a name with a
                         dot in it, not an IP address, not localhost and not a name that only lives
                         inside a network (.local, .internal, .home.arpa and the like). The lint uses
                         it on every URL of a file (TR1); the reader uses it before each request.
    public_socket(...)   at the moment the reader connects: every address the name resolves to must be
                         a public one, and the reader connects to the address it checked. A public page
                         that redirects to an internal address, or a name that resolves to one, is
                         refused there. urllib goes through it for every request and every redirect
                         (PublicOpener), so nothing reaches the network around it.

No network access happens at import.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import urllib.parse
import urllib.request

# Names that only resolve inside a network (RFC 6761, 6762, 8375, and the names networks use for
# themselves). The reserved names for examples and tests (.example, .test, .invalid) never resolve at
# all, so the reader cannot reach them; the tests of TAXNMY use them.
INTERNAL_SUFFIXES = (
    ".localhost", ".local", ".localdomain", ".internal", ".intranet", ".lan", ".home", ".corp", ".private",
    ".home.arpa",
)
SCHEMES = ("https", "http")


class Blocked(OSError):
    """A request to an address that is not public. An OSError, so urllib reports it as it reports a
    connection that failed: the source confirms nothing."""


def problem(url: str, schemes: tuple = SCHEMES) -> str:
    """Why `url` is not a public address, or '' when it is one. No network."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        parts.port  # noqa: B018 - raises ValueError on a port that is not a number
    except ValueError:
        return "the URL cannot be read"
    if parts.scheme.lower() not in schemes:
        return f"the scheme must be {' or '.join(schemes)}"
    if parts.username is not None or parts.password is not None:
        return "a URL with a user name or password in it is not a source"
    host = host.rstrip(".").lower()
    if not host:
        return "the URL has no host"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return f"the host {host} is an IP address, not a name"
    if _numeric(host):
        return f"the host {host} is an IP address, not a name"
    if host == "localhost" or host.endswith(INTERNAL_SUFFIXES):
        return f"the host {host} is not a public address"
    if "." not in host:
        return f"the host {host} is a name inside a network, not a public address"
    return ""


def _numeric(host: str) -> bool:
    """An address written as a number in another form, such as 2130706433 or 0x7f.1."""
    labels = host.split(".")
    return all(lbl and (lbl.isdigit() or (lbl[:2] == "0x" and all(c in "0123456789abcdef" for c in lbl[2:])))
               for lbl in labels)


def is_public(ip: str) -> bool:
    """An IP address that can be reached on the internet: not private, loopback, link-local, shared,
    reserved or multicast. An IPv4 address inside IPv6 is judged as IPv4."""
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    if isinstance(addr, ipaddress.IPv6Address):
        inner = addr.ipv4_mapped or addr.sixtofour or (addr.teredo[1] if addr.teredo else None)
        if inner is not None:
            addr = inner
    return addr.is_global and not addr.is_multicast


def resolve(host: str, port: int) -> list[tuple]:
    """The addresses of `host`, all of them public; Blocked when one is not. One internal address is
    enough to refuse: which one the system would take is not ours to know."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for *_, sockaddr in infos:
        if not is_public(sockaddr[0]):
            raise Blocked(f"{host} resolves to {sockaddr[0]}, which is not a public address")
    if not infos:
        raise Blocked(f"{host} does not resolve")
    return infos


def public_socket(host: str, port: int, timeout, source_address=None) -> socket.socket:
    """A connection to `host`, made to an address that was checked: the same address, so a name that
    changes its answer between the check and the connection gains nothing."""
    why = problem(f"https://{host}/")
    if why:
        raise Blocked(why)
    last: OSError | None = None
    for family, type_, proto, _, sockaddr in resolve(host, port):
        sock = socket.socket(family, type_, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:  # type: ignore[attr-defined]
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            sock.close()
            last = exc
    raise last or OSError(f"no connection to {host}")


class _PublicHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = public_socket(self.host, self.port, self.timeout, self.source_address)


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        sock = public_socket(self.host, self.port, self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PublicHTTPConnection, req)


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PublicHTTPSConnection, req, context=self._context)


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is followed only to a public address; the connection checks it again."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        why = problem(newurl)
        if why:
            raise Blocked(f"redirect to {newurl}: {why}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def opener() -> urllib.request.OpenerDirector:
    """urllib with only public addresses, also after a redirect, and without proxies (the conduct of
    tools/fetch.py: no proxies)."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _PublicHTTPHandler(),
                                       _PublicHTTPSHandler(), _CheckedRedirect())
