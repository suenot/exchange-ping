"""Latency probe for one exchange endpoint. Python stdlib only.

Numbers per venue:
  dns_ms       name resolution
  tcp_ms       TCP connect (median of n): network round trip to the venue's front door. Behind a CDN
               this is the nearest CDN edge, not the venue.
  tls_ms       TLS handshake on a fresh connection
  cold_ms      first request on a fresh connection -> first byte
  req_ms       median of n warm requests on one open keep-alive connection -> first byte. The request
               has to reach the venue's API gateway (time endpoints are never cached), so this follows
               the venue's real location. An order sent from the same box pays about the same.
  req_min_ms   best warm request: the floor of the path
  offset_ms    venue clock minus local clock, from the server time in the body, corrected by half the
               round trip (positive: the venue is ahead)
Only a 2xx answer came from the venue: a geo-block or a wrong method is often answered by the CDN edge.
"""
from __future__ import annotations

import datetime as dt
import http.client
import json
import socket
import ssl
import statistics
import time
import urllib.parse

UA = "Mozilla/5.0 (X11; Linux x86_64) exchange-ping/0.1"
EPOCH_LO, EPOCH_HI = 1.5e9, 2.5e9  # plausible server time in seconds (2017..2049)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None


def _epoch_ms(v):
    """A number or string that looks like a Unix time (s, ms, us or ns) -> ms, else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        s = v.strip()
        if s.replace(".", "", 1).isdigit():
            v = float(s)
        else:
            try:  # ISO 8601: 2026-11-14T10:00:00.123Z
                t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=dt.timezone.utc)
                return t.timestamp() * 1e3
            except ValueError:
                return None
    if not isinstance(v, (int, float)):
        return None
    for scale in (1, 1e3, 1e6, 1e9):
        if EPOCH_LO <= v / scale <= EPOCH_HI:
            return v / scale * 1e3
    return None


def _precision(v):
    """Rank of a time value's resolution: whole seconds 0, ms or an ISO string with a fraction 1, finer 2."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 1 if isinstance(v, str) and "." in v else 0
    if EPOCH_LO <= f <= EPOCH_HI:
        return 1 if f != int(f) else 0
    return 1 if f <= EPOCH_HI * 1e3 else 2


def server_time_ms(body):
    """Server time in a JSON body, ms. Keys that name a time win over other epoch-like numbers, then the finest
    resolution (Bybit sends both timeSecond and timeNano)."""
    best = []

    def walk(x, key, depth):
        if depth > 6:
            return
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, str(k).lower(), depth + 1)
        elif isinstance(x, list):
            for v in x[:8]:
                walk(v, key, depth + 1)
        else:
            ms = _epoch_ms(x)
            if ms is not None:
                score = 2 if any(w in key for w in ("time", "stamp", "now", "epoch", "ts")) else 1
                best.append((score, _precision(x), ms))

    walk(body, "", 0)
    return max(best, key=lambda t: (t[0], t[1]))[2] if best else None


def resolve(host):
    t = time.perf_counter()
    try:
        canon, aliases, ips = socket.gethostbyname_ex(host)
    except OSError:
        return None, [], [], None
    return canon, aliases, ips, round((time.perf_counter() - t) * 1e3, 2)


def tcp_ms(ip, port, n, timeout):
    out = []
    for _ in range(n):
        t = time.perf_counter()
        try:
            socket.create_connection((ip, port), timeout).close()
            out.append((time.perf_counter() - t) * 1e3)
        except OSError:
            pass
    return out


def tls_ms(host, port, timeout):
    ctx = ssl.create_default_context()
    try:
        s = socket.create_connection((host, port), timeout)
        t = time.perf_counter()
        ctx.wrap_socket(s, server_hostname=host).close()
        return round((time.perf_counter() - t) * 1e3, 2)
    except (OSError, ssl.SSLError, ValueError):
        return None


def requests_ms(url, n, timeout, method="GET", body=None):
    """1 cold + n warm requests on one keep-alive connection.
    Returns (cold_ms, [warm_ms], status, server_offsets_ms, error)."""
    u = urllib.parse.urlsplit(url)
    path = (u.path or "/") + ("?" + u.query if u.query else "")
    conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout,
                                       context=ssl.create_default_context())
    data = body.encode() if isinstance(body, str) else body
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    cold, warm, offsets, status, err = None, [], [], None, None
    try:
        for i in range(n + 1):
            if conn.sock is None:
                conn.connect()  # (re)connect outside the timed part
            w0 = time.time()
            t = time.perf_counter()
            conn.request(method, path, body=data, headers=headers)
            r = conn.getresponse()
            ms = (time.perf_counter() - t) * 1e3
            raw = r.read()
            w1 = time.time()
            status = r.status
            if i == 0:
                cold = round(ms, 2)
            else:
                warm.append(ms)
            if 200 <= status < 300:
                try:
                    st = server_time_ms(json.loads(raw))
                except ValueError:
                    st = None
                if st is not None:
                    offsets.append((st - (w0 + w1) / 2 * 1e3, ms))
            if r.will_close:
                conn.close()
    except (OSError, http.client.HTTPException, ssl.SSLError) as e:
        err = f"{type(e).__name__}: {e}"[:160]
    finally:
        conn.close()
    return cold, warm, status, offsets, err


def measure(venue, n=7, timeout=6.0):
    """Measure one registry entry ({"rest": url, "method"?, "body"?}). Returns a flat dict."""
    url = venue["rest"]
    u = urllib.parse.urlsplit(url)
    port = u.port or 443
    canon, aliases, ips, dns = resolve(u.hostname)
    row = {"host": u.hostname, "cname": canon if canon != u.hostname else (aliases[-1] if aliases else None),
           "ip": ips[0] if ips else None, "dns_ms": dns}
    if not ips:
        row["error"] = "dns"
        return row
    row["tcp_ms"] = med(tcp_ms(ips[0], port, n, timeout))
    row["tls_ms"] = tls_ms(u.hostname, port, timeout)
    cold, warm, status, offsets, err = requests_ms(url, n, timeout, venue.get("method", "GET"), venue.get("body"))
    row.update(http_status=status, cold_ms=cold, req_ms=med(warm),
               req_min_ms=round(min(warm), 2) if warm else None, requests=len(warm))
    if offsets:  # the answer with the shortest round trip pins the clock best
        off, _ = min(offsets, key=lambda t: t[1])
        row["offset_ms"] = round(off, 1)
    row["ok"] = bool(warm) and status is not None and 200 <= status < 300
    if err:
        row["error"] = err
    return row
