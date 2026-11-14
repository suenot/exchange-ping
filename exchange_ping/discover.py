"""Find a public server-time endpoint of an exchange from its domain.

Tries the usual API host names and the usual time paths, one keep-alive connection per host, and
keeps the first 2xx JSON answer that carries a plausible server time. Light on purpose: a host that
does not resolve costs one DNS lookup, a live host at most len(PATHS) small GET requests.
"""
from __future__ import annotations

import http.client
import json
import socket
import ssl
import urllib.parse

from .core import UA, server_time_ms

HOSTS = ("api.{d}", "{d}", "www.{d}", "openapi.{d}", "open-api.{d}", "sapi.{d}", "api-cloud.{d}", "public.{d}")
PATHS = (
    "/api/v3/time", "/api/v1/time", "/api/v2/time", "/v1/time", "/v2/time", "/v3/time", "/time",
    "/api/v1/common/time", "/api/v1/common/timestamp", "/v1/common/timestamp", "/v1/common/time",
    "/api/v1/timestamp", "/api/v1/public/time", "/api/v2/public/time", "/v1/public/time",
    "/openapi/v1/time", "/openApi/spot/v1/common/time", "/api/v4/public/time", "/public/time",
    "/api/v1/server-time", "/api/v1/serverTime", "/api/time", "/api/servertime", "/api/v3/servertime",
)


def base_domain(x: str) -> str:
    """'https://www.foo.com/en' -> 'foo.com'."""
    h = urllib.parse.urlsplit(x if "//" in x else "//" + x).hostname or x
    parts = h.split(".")
    if len(parts) > 2 and parts[-2] in ("co", "com", "net", "org") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])  # foo.co.kr, foo.com.br
    return ".".join(parts[-2:])


def try_host(host, paths, timeout):
    conn = http.client.HTTPSConnection(host, 443, timeout=timeout, context=ssl.create_default_context())
    try:
        for p in paths:
            try:
                if conn.sock is None:
                    conn.connect()
                conn.request("GET", p, headers={"User-Agent": UA, "Accept": "application/json"})
                r = conn.getresponse()
                raw = r.read(65536)
                if r.will_close:
                    conn.close()
                if 200 <= r.status < 300 and "json" in (r.getheader("Content-Type") or "json"):
                    try:
                        body = json.loads(raw)
                    except ValueError:
                        continue
                    if server_time_ms(body) is not None:
                        return f"https://{host}{p}"
            except (OSError, http.client.HTTPException, ssl.SSLError):
                conn.close()
                return None  # host is down or refuses TLS: next host
    finally:
        conn.close()
    return None


def discover(domain_or_url: str, timeout=5.0, hosts=HOSTS, paths=PATHS):
    """Return the first working time URL or None."""
    d = base_domain(domain_or_url)
    seen = set()
    for h in (x.format(d=d) for x in hosts):
        try:
            ip = socket.gethostbyname(h)
        except OSError:
            continue
        if (ip, h) in seen:
            continue
        seen.add((ip, h))
        url = try_host(h, paths, timeout)
        if url:
            return url
    return None
