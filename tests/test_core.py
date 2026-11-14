import json, os, unittest

from exchange_ping.core import _epoch_ms, server_time_ms
from exchange_ping.discover import base_domain

T = 1794650000000  # 2026-11-13 in ms


class TimeParse(unittest.TestCase):
    def test_scales(self):
        for v in (T / 1e3, T, T * 1e3, T * 1e6, str(T)):
            self.assertAlmostEqual(_epoch_ms(v), T, delta=1)

    def test_iso(self):
        self.assertAlmostEqual(_epoch_ms("2026-11-13T00:00:00Z"), 1794528000000, delta=1)

    def test_not_time(self):
        for v in (True, 42, 12345.6, "abc", None):
            self.assertIsNone(_epoch_ms(v))

    def test_bodies(self):
        bodies = [{"serverTime": T}, {"retCode": 0, "result": {"timeSecond": str(T // 1000), "timeNano": str(T * 10**6)}},
                  {"code": "0", "data": [{"ts": str(T)}]}, {"result": {"unixtime": T // 1000, "rfc1123": "x"}}, T]
        for b in bodies:
            self.assertAlmostEqual(server_time_ms(b), T, delta=1000, msg=b)

    def test_time_key_wins(self):
        self.assertEqual(server_time_ms({"id": T - 10**9, "time": T}), T)


class Registry(unittest.TestCase):
    def test_entries(self):
        reg = json.load(open(os.path.join(os.path.dirname(__file__), "..", "exchange_ping", "venues.json")))
        for k, v in reg.items():
            if k.startswith("_"):
                continue
            self.assertTrue(v["rest"].startswith("https://"), k)
            self.assertIn(v["kind"], ("time", "status", "ticker", "markets", "host"), k)


class Domain(unittest.TestCase):
    def test_base(self):
        self.assertEqual(base_domain("https://www.gopax.co.kr/exchange/"), "gopax.co.kr")
        self.assertEqual(base_domain("api.bybit.com"), "bybit.com")


if __name__ == "__main__":
    unittest.main()
