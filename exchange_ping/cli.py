"""exchange-ping: how far is this box from each crypto exchange.

  exchange-ping list [--tag cex]                        registry
  exchange-ping run [--venues binance,bybit] [-n 7]     measure from this box -> JSON on stdout
  exchange-ping run --table                             same, a sorted table
  exchange-ping discover gate.com mexc.com              find a server-time endpoint by domain
  exchange-ping globalping --cities Tokyo,Singapore     public Globalping probes per city (GET only)
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__
from .core import UA, measure, med, now_iso
from .discover import discover

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(HERE, "venues.json")
GLOBALPING = "https://api.globalping.io/v1/measurements"


def load_venues(path=None, ids=None, tags=None):
    with open(path or REGISTRY) as f:
        d = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    if ids:
        want = [x.strip() for x in ids.split(",") if x.strip()]
        miss = [x for x in want if x not in d]
        if miss:
            sys.exit(f"unknown venue: {', '.join(miss)}")
        d = {k: d[k] for k in want}
    if tags:
        t = set(tags.split(","))
        d = {k: v for k, v in d.items() if t & set(v.get("tags", []))}
    return d


def cmd_list(a):
    for k, v in load_venues(a.registry, a.venues, a.tag).items():
        print(f"{k:24s} {v.get('kind', 'time'):8s} {v.get('method', 'GET'):4s} {v['rest']}")


def cmd_run(a):
    venues = load_venues(a.registry, a.venues, a.tag)
    if not a.include_untested:
        venues = {k: v for k, v in venues.items() if v.get("kind") != "host" or a.venues}
    res = {"kind": "local", "tool": f"exchange-ping {__version__}", "node": a.node or socket.gethostname(),
           "region": a.region, "reference": a.reference, "measured_at": now_iso(), "n": a.n, "venues": {}}
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=max(1, a.parallel)) as ex:
        futs = {ex.submit(measure, v, a.n, a.timeout): k for k, v in venues.items()}
        for f in cf.as_completed(futs):
            k = futs[f]
            row = f.result()
            row.update(label=venues[k].get("label", k), endpoint=venues[k]["rest"], endpoint_kind=venues[k].get("kind", "time"))
            res["venues"][k] = row
            if not a.quiet:
                print(f"{k:24s} req {row.get('req_min_ms')}/{row.get('req_ms')} tcp {row.get('tcp_ms')} "
                      f"http {row.get('http_status')} {row.get('error', '')}", file=sys.stderr, flush=True)
    res["venues"] = dict(sorted(res["venues"].items()))
    res["seconds"] = round(time.time() - t0, 1)
    if a.table:
        rows = sorted(res["venues"].items(), key=lambda kv: (kv[1].get("req_min_ms") is None or not kv[1].get("ok"),
                                                             kv[1].get("req_min_ms") or 1e9))
        print(f"{'venue':24s} {'req_min':>8s} {'req_med':>8s} {'tcp':>7s} {'tls':>7s} {'http':>4s} {'offset':>8s}  host")
        for k, r in rows:
            f = lambda x: "-" if x is None else f"{x:.1f}"
            print(f"{k:24s} {f(r.get('req_min_ms')):>8s} {f(r.get('req_ms')):>8s} {f(r.get('tcp_ms')):>7s} "
                  f"{f(r.get('tls_ms')):>7s} {str(r.get('http_status') or '-'):>4s} {f(r.get('offset_ms')):>8s}  "
                  f"{r['host']}{'' if r.get('ok') else '  (' + (r.get('error') or 'not 2xx') + ')'}")
    else:
        json.dump(res, sys.stdout, ensure_ascii=False, indent=1)
        print()


def cmd_discover(a):
    out = {}
    with cf.ThreadPoolExecutor(max_workers=max(1, a.parallel)) as ex:
        for d, url in zip(a.domains, ex.map(lambda d: discover(d, a.timeout), a.domains)):
            out[d] = url
            print(f"{d:30s} {url or '-'}", file=sys.stderr, flush=True)
    json.dump(out, sys.stdout, indent=1)
    print()


def gp_call(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def gp_post(body, tries):
    """Create a measurement; waits out the anonymous rate limit (HTTP 429) instead of failing."""
    for i in range(tries):
        try:
            return gp_call(GLOBALPING, body)["id"]
        except urllib.error.HTTPError as e:
            if e.code != 429 or i == tries - 1:
                raise
            wait = min(int(e.headers.get("Retry-After") or e.headers.get("X-RateLimit-Reset") or 60), 900)
            print(f"globalping rate limit, waiting {wait} s", file=sys.stderr, flush=True)
            time.sleep(wait + 1)


def cmd_globalping(a):
    venues = {k: v for k, v in load_venues(a.registry, a.venues, a.tag).items()
              if v.get("method", "GET") == "GET" and v.get("kind") != "host"}
    cities = [c.strip() for c in a.cities.split(",") if c.strip()]
    res = {"kind": "globalping", "tool": f"exchange-ping {__version__}", "measured_at": now_iso(),
           "cities": cities, "venues": {}}
    for name, v in venues.items():
        u = urllib.parse.urlsplit(v["rest"])
        req = {"method": "GET", "path": u.path or "/", "headers": {"Accept": "application/json"}}
        if u.query:
            req["query"] = u.query
        body = {"type": "http", "target": u.hostname, "locations": [{"magic": c, "limit": a.limit} for c in cities],
                "measurementOptions": {"protocol": "HTTPS", "port": u.port or 443, "request": req}}
        probes = []
        try:
            mid = gp_post(body, a.tries)
            for _ in range(40):
                time.sleep(1.5)
                d = gp_call(f"{GLOBALPING}/{mid}")
                if d.get("status") != "in-progress":
                    break
            for r in d.get("results", []):
                t = r["result"].get("timings") or {}
                if t.get("firstByte") is not None:
                    probes.append({"city": r["probe"].get("city"), "network": r["probe"].get("network"),
                                   "status": r["result"].get("statusCode"), "tcp_ms": t.get("tcp"),
                                   "req_ms": t.get("firstByte")})
        except (OSError, ValueError, KeyError) as e:
            print(f"{name}: globalping failed: {e}", file=sys.stderr, flush=True)
        by_city = {}
        for c in cities:
            here = [p for p in probes if (p["city"] or "").lower() == c.lower()]
            ok = [p for p in here if 200 <= (p["status"] or 0) < 300]
            reqs = [p["req_ms"] for p in ok]
            by_city[c.lower()] = {"probes": len(ok), "req_ms": med(reqs), "req_min_ms": min(reqs) if reqs else None,
                                  "tcp_ms": med([p["tcp_ms"] for p in ok]),
                                  "blocked": sorted({p["status"] for p in here if p not in ok}),
                                  "networks": sorted({p["network"] for p in ok if p["network"]})}
        res["venues"][name] = {"host": u.hostname, "cities": by_city, "raw": probes}
        print(f"{name:24s} " + " ".join(f"{c} {x['req_min_ms']}" for c, x in by_city.items()), file=sys.stderr, flush=True)
    json.dump(res, sys.stdout, ensure_ascii=False, indent=1)
    print()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="exchange-ping", description=__doc__.split("\n")[0])
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--registry", help="venues.json to use instead of the bundled one")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--venues", help="comma-separated venue ids")
        p.add_argument("--tag", help="only venues with one of these tags (cex, dex, perp, ...)")

    p = sub.add_parser("list"); common(p); p.set_defaults(f=cmd_list)
    p = sub.add_parser("run"); common(p)
    p.add_argument("-n", type=int, default=7, help="warm requests per venue (default 7)")
    p.add_argument("--timeout", type=float, default=6)
    p.add_argument("--parallel", type=int, default=4, help="venues measured at once (default 4)")
    p.add_argument("--node", help="node name in the output (default: hostname)")
    p.add_argument("--region", help="region label in the output, e.g. singapore")
    p.add_argument("--reference", action="store_true", help="mark this node as a reference, not a trading candidate")
    p.add_argument("--include-untested", action="store_true", help="also venues without a known endpoint (kind host)")
    p.add_argument("--table", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(f=cmd_run)
    p = sub.add_parser("discover")
    p.add_argument("domains", nargs="+")
    p.add_argument("--timeout", type=float, default=5)
    p.add_argument("--parallel", type=int, default=4)
    p.set_defaults(f=cmd_discover)
    p = sub.add_parser("globalping"); common(p)
    p.add_argument("--cities", default="Tokyo,Singapore,Seoul,Hong Kong,Frankfurt,London,Ashburn")
    p.add_argument("--limit", type=int, default=2, help="probes per city")
    p.add_argument("--tries", type=int, default=6, help="HTTP 429 retries per venue")
    p.set_defaults(f=cmd_globalping)
    a = ap.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
