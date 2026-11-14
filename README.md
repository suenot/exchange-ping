# exchange-ping

How far is this box from each crypto exchange? One command measures ~200 exchange APIs from the
machine it runs on: DNS, TCP connect, TLS handshake, a warm request on an open connection, and the
exchange's clock offset. Use it to pick where to put a trading server, to check a new server, or to
watch a venue that moved its matching engine.

Python 3.8+, standard library only: copy it to a 512 MB VPS and run it, nothing to install.

```bash
pip install git+https://github.com/suenot/exchange-ping
exchange-ping run --table                       # every venue with a known endpoint
exchange-ping run --venues bybit,okx,binance -n 9 > here.json
python3 -m exchange_ping run --table            # or straight from a checkout
```

From an AWS Singapore (ap-southeast-1) box, 2026-11-14, a few rows:

```
venue                     req_min  req_med     tcp     tls http   offset  host
phemex                        6.3      7.1     0.5     3.2  200      0.7  api.phemex.com
bitfinex                      9.8     14.2     1.8     5.7  200        -  api-pub.bitfinex.com
bitflyer                     10.6     10.8     1.3     5.1  200        -  api.bitflyer.com
upbit                        70.4     73.3    65.4    68.4  200  -1369.2  api.upbit.com
binance                      71.2     71.8     0.9     3.8  200     -0.7  api.binance.com
bybit                        73.1     75.1     0.5     3.1  200      1.5  api.bybit.com
gateio                       71.8     79.1    68.4   139.4  200     -0.3  api.gateio.ws
okx                          84.7     86.2     1.7     6.8  200      3.0  www.okx.com
kraken                      172.2    184.6     2.1     9.3  200   -755.6  api.kraken.com
coinbase                    250.6    254.4     1.8     7.5  200     -3.0  api.exchange.coinbase.com
delta                         3.0      3.7     0.8     3.7  200  -4452.4  api.delta.exchange
```

## What the numbers mean

| field | meaning |
|---|---|
| `req_min_ms`, `req_ms` | best and median of `n` warm requests on one keep-alive TLS connection, request sent -> first byte. **This is the number that matters.** Server-time endpoints are rarely cached, so the request has to reach the exchange's API gateway (a few ms together with an `offset_ms` seconds off, like `delta` above, is a CDN cache hit); an order sent from the same box pays about the same round trip before matching. |
| `tcp_ms` | TCP connect. Most exchanges sit behind a CDN, so this is often the nearest CDN edge, not the exchange. Do not pick a location by it. |
| `tls_ms`, `cold_ms`, `dns_ms` | handshake, first request on a fresh connection, name resolution |
| `offset_ms` | exchange clock minus local clock from the server time in the body, corrected by half the round trip (positive: exchange ahead). Venues that send whole seconds are only good to ±500 ms. |
| `http_status`, `ok` | only a 2xx answer came from the exchange. A 403 is usually a geo-block or WAF answered by the CDN edge in your city: its timing says nothing about the exchange. |
| `cname` | the DNS chain often names the cloud region (`*.ap-northeast-1.elb.amazonaws.com` = AWS Tokyo) |

Rule of thumb: under ~5 ms you are in the same city/data center, 5-15 ms the same metro or a
neighbour, above 40 ms another region.

## Registry

`exchange_ping/venues.json`: 220 venues (centralized exchanges and order-book DEXes), each
with one public endpoint and its `kind`:

- `time` (120): a server-time endpoint, the best probe: tiny and never cached;
- `status`, `ticker`, `markets` (52): no time endpoint, a light public one instead;
- `host` (48): no known public API, only DNS/TCP/TLS (`run --include-untested`).

`verified: "singapore"` means the endpoint answered 2xx JSON when the registry was built;
`blocked: {"singapore": 403}` means it exists but refused that location. Endpoints come from public
API docs, ccxt metadata and `exchange-ping discover`. On-chain AMMs have no venue location and are
not listed. Add or fix a venue with a PR, or pass your own file with `--registry`.

```bash
exchange-ping list --tag perp-dex
exchange-ping discover gopax.co.kr bitkub.com   # look for a server-time endpoint by domain
```

## Many cities without servers

`exchange-ping globalping --cities Tokyo,Singapore,Seoul,Frankfurt` sends the same GET from public
[Globalping](https://globalping.io) probes in each city (HTTPS request -> first byte). The anonymous
quota is 250 measurements an hour per IP; with a free account token in `GLOBALPING_TOKEN` it is 500.
A full registry still takes a few hours; the tool waits out HTTP 429 itself. POST endpoints (Hyperliquid, CEX.IO, GRVT, Derive) are skipped there.

## Output

`run` prints JSON: `{"kind": "local", "node", "region", "measured_at", "n", "venues": {id: {...}}}`.
`--node` and `--region` label the run so several servers can be merged later.

## Etiquette

Each venue gets 1 cold + `n` warm requests (default 7) plus `n` TCP connects, four venues at a time.
That is far below any public rate limit. Do not run it in a tight loop.

## License

MIT
