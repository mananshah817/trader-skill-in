"""india-scanner tests. Fixtures in tests/fixtures/india-scanner/, all REAL data fetched on 2026-10-31:

nse/EQUITY_L.csv, nse/sec_list.csv   19 and 18 lines cut byte for byte from NSE's files (nsearchives).
nifty100-5m-2026-10-26.json.gz       {SYMBOL: v8 chart} for the NIFTY 100, 5-minute bars of Mon 2026-10-26
                                     (09:15 to 15:25), cut from range=5d charts. meta trimmed to the fields
                                     that do not change by the day; previousClose is the 2026-10-23 close.
nifty100-5m-2026-10-26-1000.json.gz  the same charts "as fetched at 10:00 IST": only the bars that had started
                                     before 10:00 (09:15 ... 09:55). The values are Yahoo's final ones.
spark-5m-2026-10-26.json.gz          Yahoo's spark answers (5 requests of 20, range=5d, 5m) for the same 100,
                                     merged and cut to 2026-10-26. Spark thins its line: bars are missing.
daily-spark-3mo-2026-10-30.json.gz   Yahoo's spark answers (range=3mo, 1d) for the NIFTY 100, merged. The meta
                                     (price, volume) is Friday 2026-10-30's.
ITC-1d-3mo.json                      ITC's v8 daily chart (range=3mo), the refill for its thinned spark answer.
spark-5m-1d-2026-10-30.json          one spark answer for M&M, BAJAJ-AUTO, RELIANCE and the made-up ZZZZQX.
"""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

scan = load("india-scanner", "scan")

SDIR = os.path.join(FIX, "india-scanner")
NSE = os.path.join(SDIR, "nse")
FULL = os.path.join(SDIR, "nifty100-5m-2026-10-26.json.gz")
AT10 = os.path.join(SDIR, "nifty100-5m-2026-10-26-1000.json.gz")
SPARK5 = os.path.join(SDIR, "spark-5m-2026-10-26.json.gz")
DAILY = os.path.join(SDIR, "daily-spark-3mo-2026-10-30.json.gz")
ITC = os.path.join(SDIR, "ITC-1d-3mo.json")
SPARK1D = os.path.join(SDIR, "spark-5m-1d-2026-10-30.json")
DT = datetime.datetime
D = datetime.date
DAY = D(2026, 10, 26)
TEN = DT(2026, 10, 26, 10, 0)
RELIANCE_HIT = {"symbol": "RELIANCE", "setups": ["orb"], "price": 1193.6, "at": "10:00 AM", "bar_start": "09:55",
                "bar_index": 8, "or_high": 1192.0, "or_low": 1175.3, "pct_over": 0.13}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


def ts(ist_text):
    """India wall-clock text -> unix seconds."""
    utc = scan.ist_to_utc(DT.strptime(ist_text, "%Y-%m-%d %H:%M"))
    return int((utc - DT(1970, 1, 1)).total_seconds())


def bars_series(day, closes, highs=None, lows=None, start="09:15"):
    """A v8-like series of 5-minute bars from `start` on `day` (YYYY-MM-DD). SYNTHETIC."""
    t0 = DT.strptime(day + " " + start, "%Y-%m-%d %H:%M")
    t = [ts((t0 + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M")) for i in range(len(closes))]
    return {"t": t, "c": list(closes), "h": list(highs or [c + 0.1 if c is not None else None for c in closes]),
            "l": list(lows or [c - 0.1 if c is not None else None for c in closes]), "o": None,
            "v": [1000] * len(closes), "meta": {}}


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class Universe(unittest.TestCase):
    def setUp(self):
        self.texts = {f: read(os.path.join(NSE, f)) for f in (scan.EQUITY_FILE, scan.BANDS_FILE)}
        self.kept, self.counts, self.bands_read = scan.build_universe(self.texts)

    def test_the_kept_list(self):
        self.assertEqual([s for s, _, _ in self.kept],
                         ["20MICRONS", "21STCENMGM", "3MINDIA", "A2ZINFRA", "ABCOTS", "ABDL", "BAJAJ-AUTO",
                          "HDFCBANK", "ICICIBANK", "INFY", "J&KBANK", "M&M", "NAM-INDIA", "RELIANCE", "SBIN", "TCS"])
        self.assertEqual(self.counts, {"rows": 19, "keep": 16, "series BE (trade-for-trade)": 2,
                                       "series BZ (trade-for-trade)": 1})
        k = {s: (n, b) for s, n, b in self.kept}
        self.assertEqual(k["RELIANCE"], ("Reliance Industries Limited", "No Band"))   # F&O stocks have no band
        self.assertEqual(k["21STCENMGM"][1], "5")
        self.assertEqual(k["ABDL"][1], "10")
        self.assertEqual(k["ABCOTS"][1], "")          # EQ in EQUITY_L but not in sec_list (true on Oct 31)
        self.assertTrue(self.bands_read)

    def test_one_case_per_rule(self):
        self.assertEqual(scan.classify({"SYMBOL": "ABAN", "SERIES": "BE"}), "series BE (trade-for-trade)")
        self.assertEqual(scan.classify({"SYMBOL": "ANKITMETAL", "SERIES": "BZ"}), "series BZ (trade-for-trade)")
        self.assertEqual(scan.classify({"SYMBOL": "XYZ", "SERIES": "SM"}), "series SM")      # SME platform
        self.assertEqual(scan.classify({"SYMBOL": "BAD SYM", "SERIES": "EQ"}), "not a plain symbol")
        for sym in ("M&M", "BAJAJ-AUTO", "3MINDIA", "J&KBANK"):
            self.assertEqual(scan.classify({"SYMBOL": sym, "SERIES": "EQ"}), "keep", sym)
        self.assertEqual([scan.band_pct(b) for b in ("5", "No Band", "", "20")], [5, None, None, 20])

    def test_without_the_band_file(self):
        kept, counts, bands_read = scan.build_universe({scan.EQUITY_FILE: self.texts[scan.EQUITY_FILE]})
        self.assertFalse(bands_read)
        self.assertEqual(len(kept), 16)
        self.assertEqual({b for _, _, b in kept}, {""})

    def test_not_an_nse_file(self):
        with self.assertRaises(scan.ScanError):
            scan.parse_equity_list("<html>Access Denied</html>")
        with self.assertRaises(scan.ScanError):
            scan.build_universe({})


class Time(unittest.TestCase):
    def test_ist_and_clock(self):
        self.assertEqual(scan.ts_to_ist(1792986300), DT(2026, 10, 26, 9, 15))    # RELIANCE's first bar, 03:45 UTC
        self.assertEqual(scan.parse_now("2026-10-26 10:00"), DT(2026, 10, 26, 4, 30))
        self.assertEqual(scan.parse_now("2026-10-26 10:00 IST"), DT(2026, 10, 26, 4, 30))
        self.assertEqual(scan.parse_now("2026-12-01 10:00"), DT(2026, 12, 1, 4, 30))   # no daylight saving
        self.assertEqual(scan.parse_now("2026-10-26T04:30:00Z"), DT(2026, 10, 26, 4, 30))
        self.assertEqual(scan.parse_now("2026-10-26T10:00:00+05:30"), DT(2026, 10, 26, 4, 30))
        self.assertEqual(scan.clock_label(DT(2026, 10, 26, 10, 0)), "10:00 AM")
        self.assertEqual(scan.clock_label(datetime.time(15, 5)), "3:05 PM")
        with self.assertRaises(scan.ScanError) as cm:
            scan.parse_now("tomorrow")
        self.assertIn('an India time like "2026-10-26 10:00"', str(cm.exception))
        with self.assertRaises(scan.ScanError):
            scan.parse_hhmm("25:00")

    def test_calendar(self):
        self.assertEqual(scan.trading_day(D(2026, 10, 24)), (False, "weekend"))
        self.assertEqual(scan.trading_day(D(2026, 11, 10)), (False, "NSE holiday: Diwali-Balipratipada"))
        self.assertEqual(scan.trading_day(D(2026, 10, 20))[1], "NSE holiday: Dussehra")
        self.assertFalse(scan.trading_day(D(2026, 11, 8))[0])                      # Muhurat, a Sunday
        self.assertIn("Muhurat", scan.trading_day(D(2026, 11, 8))[1])
        self.assertEqual(scan.session(D(2026, 11, 8), special=True), (datetime.time(14, 20), datetime.time(15, 30)))
        self.assertEqual(scan.session(DAY), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertEqual(scan.session(D(2027, 1, 26)), (datetime.time(9, 15), datetime.time(15, 30)))  # not listed
        self.assertEqual(scan.calendar_note(D(2027, 1, 26)), "NSE has not published its 2027 holiday list in "
                                                             "this tool yet: check nseindia.com before trading")
        self.assertEqual(scan.calendar_note(DAY), "")


class Spark(unittest.TestCase):
    def test_a_real_spark_answer(self):
        wanted = {scan.to_yahoo(s): s for s in ["M&M", "BAJAJ-AUTO", "RELIANCE", "ZZZZQX"]}
        data = scan.parse_spark(read(SPARK1D), wanted)
        self.assertEqual(sorted(data), ["BAJAJ-AUTO", "M&M", "RELIANCE"])     # Yahoo silently drops ZZZZQX.NS
        r = data["RELIANCE"]
        self.assertIsNone(r["h"])                                            # spark is closes only
        self.assertEqual(r["meta"]["regularMarketVolume"], 14722247)
        self.assertEqual(r["c"][-1], 1164.9)
        # spark thins its line: a session is 75 bars and spark left some out (v8 has them)
        self.assertEqual({k: len(v["t"]) for k, v in data.items()}, {"BAJAJ-AUTO": 71, "M&M": 74, "RELIANCE": 73})
        have = {scan.ts_to_ist(t).strftime("%H:%M") for t in r["t"]}
        self.assertEqual(sorted({(DT(2026, 10, 30, 9, 15) + datetime.timedelta(minutes=5 * k)).strftime("%H:%M")
                                 for k in range(75)} - have), ["14:05", "15:20"])

    def test_spark_errors(self):
        too_many = '{"spark":{"result":null,"error":{"code":"Bad Request",' \
                   '"description":"Number of symbols needs to be less than or equal to 20"}}}'
        with self.assertRaises(scan.ScanError) as cm:
            scan.parse_spark(too_many)
        self.assertIn("less than or equal to 20", str(cm.exception))
        self.assertEqual(scan.parse_spark('{"spark":{"result":[],"error":null}}'), {})
        with self.assertRaises(scan.ScanError):
            scan.parse_spark('{"chart":{}}')

    def test_symbols_and_urls(self):
        self.assertEqual(scan.spark_path(["M&M", "BAJAJ-AUTO", "reliance.ns"], "1d", "5m"),
                         "/v7/finance/spark?symbols=M%26M.NS,BAJAJ-AUTO.NS,RELIANCE.NS&range=1d&interval=5m")
        self.assertEqual(scan.chart_path("M&M", "5d", "5m"), "/v8/finance/chart/M%26M.NS?range=5d&interval=5m")
        self.assertEqual(scan.to_yahoo("RELIANCE.BO"), "RELIANCE.BO")
        self.assertEqual(scan.clean_symbol(" reliance.ns "), "RELIANCE")
        self.assertEqual(scan.from_yahoo("M&M.NS"), "M&M")


class Network(unittest.TestCase):
    """429s, 5xx and NSE refusals without the network."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep, self._primed = scan.http_get, scan.SLEEP, scan.primed_get
        scan.SLEEP = self.sleeps.append
        self.body = read(SPARK1D)

    def tearDown(self):
        scan.http_get, scan.SLEEP, scan.primed_get = self._get, self._sleep, self._primed

    def answers(self, codes):
        codes = list(codes)

        def fake(url, timeout=20, headers=None):
            self.calls.append(url.split("/v")[0])
            code = codes.pop(0) if codes else 200
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return self.body
        scan.http_get = fake

    def test_429_falls_back_then_waits_once(self):
        self.answers([429, 429, 200])
        text = scan.yahoo_get("/v7/finance/spark?symbols=RELIANCE.NS&range=1d&interval=5m")
        self.assertEqual(text, self.body)
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com",
                                      "https://query2.finance.yahoo.com"])
        self.assertEqual(self.sleeps, [scan.BACKOFF])

    def test_5xx_goes_to_query1_without_waiting(self):
        self.answers([503, 200])
        scan.yahoo_get("/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.sleeps, [])

    def test_429_for_good_is_reported_as_partial(self):
        self.answers([200] + [429] * 10)
        syms = ["M&M", "BAJAJ-AUTO", "RELIANCE", "ZZZZQX"] + ["X%02d" % i for i in range(36)]
        data, failed = scan.fetch_spark(syms, "1d", "5m", 0.3)
        self.assertEqual(sorted(data), ["BAJAJ-AUTO", "M&M", "RELIANCE"])
        self.assertEqual(failed["ZZZZQX"], "no data from Yahoo")
        self.assertEqual(len(failed), 37)
        self.assertIn("HTTP 429", failed["X20"])
        self.assertEqual(self.sleeps, [0.3, scan.BACKOFF])  # a pause between batches, one backoff

    def test_a_404_is_final(self):
        self.answers([404])
        with self.assertRaises(scan.ScanError):
            scan.yahoo_get("/v8/finance/chart/NOPE.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 1)

    def test_nse_gets_one_retry_after_cookies_then_a_plain_error(self):
        seen, primed = [], []

        def refused(url, timeout=20, headers=None):
            seen.append(headers)
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b""))
        scan.http_get = refused
        scan.primed_get = lambda url, timeout=60: primed.append(url) or "SYMBOL,NAME OF COMPANY, SERIES\n"
        self.assertTrue(scan.nse_get(scan.NSE_URL + "EQUITY_L.csv").startswith("SYMBOL"))
        self.assertEqual(seen[0]["Referer"], "https://www.nseindia.com/")
        self.assertIn("Chrome", seen[0]["User-Agent"])

        def refused_again(url, timeout=60):
            primed.append(url)
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b""))
        scan.primed_get = refused_again
        with self.assertRaises(scan.ScanError) as cm:
            scan.nse_get(scan.NSE_URL + "EQUITY_L.csv")
        self.assertIn("NSE refused EQUITY_L.csv (HTTP 403)", str(cm.exception))
        self.assertIn("try again in a minute", str(cm.exception))
        self.assertEqual((len(seen), len(primed)), (2, 2))   # one plain try and one primed try each, no loop


class Daily(unittest.TestCase):
    # The NIFTY 100 before the open on Mon 2026-10-26: closes through Fri 2026-10-23. Hand-checked:
    EXPECT = {
        "INFY": ["bull_flag"],       # up 11.7 % over the 10 sessions before the last 5, then a 3.2 % range
        "BSE": ["bull_flag"],        # up 10.9 %, 4.1 % range
        "HCLTECH": ["bull_flag"],    # up 10.6 %, 3.1 % range (1422.20, just under its 20-day best 1424.20)
        "ONGC": ["reclaim_ma20"],    # 225.00 under its 20-day average, then 227.50 over 225.65
        "MAXHEALTH": ["oversold"],   # RSI 20.1
        "CHOLAFIN": ["oversold"], "TITAN": ["oversold"], "NTPC": ["oversold"], "CUMMINSIND": ["oversold"],
        "POWERINDIA": ["oversold"], "VEDL": ["oversold"], "POLYCAB": ["oversold"], "ADANIENT": ["oversold"],
        "IDEA": ["oversold"], "VAML": ["oversold"], "NESTLEIND": ["oversold"], "JIOFIN": ["oversold"],
        "MAZDOCK": ["oversold"], "MUTHOOTFIN": ["oversold"], "HINDZINC": ["oversold"], "BAJAJFINSV": ["oversold"],
        "TMPV": ["oversold"], "ABB": ["oversold"], "BRITANNIA": ["oversold"], "ENRIN": ["oversold"],
        "HYUNDAI": ["oversold"], "SIEMENS": ["oversold"],
    }
    # Yahoo's spark answer left out one NSE trading day for these (their v8 charts have it)
    GAPPY = {"AMBUJACEM": D(2026, 8, 20), "DMART": D(2026, 9, 11), "HDFCLIFE": D(2026, 8, 6), "ITC": D(2026, 8, 3),
             "LTM": D(2026, 9, 8), "MOTHERSON": D(2026, 9, 3), "TATASTEEL": D(2026, 8, 20), "TCS": D(2026, 8, 19)}

    @classmethod
    def setUpClass(cls):
        cls.data, cls.errors = scan.load_offline(DAILY)
        cls.syms = sorted(cls.data)
        cls.before = scan.parse_now("2026-10-26 09:00")

    def test_setups_before_the_open(self):
        self.assertEqual(len(self.syms), 100)
        hits, counts, last = scan.scan_daily(self.data, self.syms, self.before, 0, 0)
        self.assertEqual({h["symbol"]: h["setups"] for h in hits}, self.EXPECT)
        self.assertEqual(last, D(2026, 10, 23))
        self.assertEqual(counts, {"requested": 100, "fetched": 100, "illiquid": 0, "short": 0, "gappy": 8,
                                  "refilled": 0, "scanned": 92, "hits": 27})
        infy = [h for h in hits if h["symbol"] == "INFY"][0]
        self.assertEqual((infy["price"], infy["rsi"], infy["close_date"]), (1153.8, 63.7, "2026-10-23"))

    def test_spark_drops_days_and_the_v8_chart_fills_them(self):
        for sym, day in self.GAPPY.items():
            bars = scan.daily_closes(self.data[sym], self.before)
            self.assertEqual(scan.missing_days([d for d, _ in bars]), [day], sym)
        self.assertEqual(scan.missing_days([d for d, _ in scan.daily_closes(self.data["RELIANCE"], self.before)]),
                         [])                                                   # Dussehra (Oct 20) is no gap
        itc = scan.parse_chart(read(ITC), "ITC")
        asked = []
        hits, counts, _ = scan.scan_daily(self.data, ["ITC", "RELIANCE"], self.before, 50, 500000,
                                          refill=lambda s: asked.append(s) or itc)
        self.assertEqual(asked, ["ITC"])
        self.assertEqual((counts["refilled"], counts["gappy"], counts["scanned"]), (1, 0, 2))
        self.assertEqual(len(scan.daily_closes(itc, self.before)), 59)        # spark had 58

        def broken(sym):
            raise scan.ScanError("Yahoo answered HTTP 429")
        _, counts, _ = scan.scan_daily(self.data, ["ITC"], self.before, 50, 500000, refill=broken)
        self.assertEqual((counts["gappy"], counts["scanned"]), (1, 0))

    def test_the_in_progress_bar_is_dropped(self):
        during = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-26 12:00"), 0, 0)
        self.assertEqual(scan.scan_daily(self.data, self.syms, self.before, 0, 0), during)
        bars = scan.daily_closes(self.data["RELIANCE"], scan.parse_now("2026-10-26 15:29"))
        self.assertEqual(bars[-1], (D(2026, 10, 23), 1190.0))
        bars = scan.daily_closes(self.data["RELIANCE"], scan.parse_now("2026-10-26 15:30"))
        self.assertEqual(bars[-1], (D(2026, 10, 26), 1201.8))

    def test_liquidity_filter(self):
        # meta is Yahoo's of Friday Oct 30: price and that day's volume
        hits, counts, _ = scan.scan_daily(self.data, self.syms, self.before, 50, 500000)
        self.assertEqual(counts["illiquid"], 19)    # IDEA under Rs 50; 18 under 5,00,000 shares (DMART, LTM, ...)
        self.assertEqual((counts["gappy"], counts["scanned"], len(hits)), (6, 75, 19))
        self.assertNotIn("IDEA", [h["symbol"] for h in hits])
        self.assertEqual(hits[0]["symbol"], "INFY")  # most rupees traded first

    def test_tight_band_flag(self):
        hits, _, _ = scan.scan_daily(self.data, ["INFY", "ONGC"], self.before, 0, 0, bands={"INFY": "5",
                                                                                          "ONGC": "No Band"})
        self.assertEqual({h["symbol"]: h.get("band") for h in hits}, {"INFY": 5, "ONGC": None})

    def test_short_and_empty_charts(self):
        s = {"t": [ts("2026-10-%02d 09:15" % d) for d in (5, 6, 7, 8, 9, 12, 13, 14, 15, 16)], "c": [100.0] * 10,
             "meta": {"regularMarketPrice": 100, "regularMarketVolume": 5000000}}
        hits, counts, last = scan.scan_daily({"X": s, "E": {"t": [], "c": [], "meta": {}}}, ["X", "E", "GONE"],
                                             scan.parse_now("2026-10-19 09:00"), 50, 500000)
        self.assertEqual(hits, [])
        self.assertEqual((counts["fetched"], counts["short"], counts["illiquid"], counts["scanned"]), (2, 1, 1, 0))
        self.assertIsNone(last)

    def test_rsi_math(self):
        self.assertIsNone(scan.rsi([1.0] * 10))
        self.assertEqual(scan.rsi([float(x) for x in range(1, 40)]), 100.0)
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61,
                  46.28, 46.28]
        self.assertAlmostEqual(scan.rsi(closes), 70.46, places=1)  # Wilder's textbook example


class Orb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.at10, _ = scan.load_offline(AT10)
        cls.full, _ = scan.load_offline(FULL)
        cls.spark, _ = scan.load_offline(SPARK5)
        cls.syms = sorted(cls.full)

    def scan_at(self, now, data=None, min_price=50, syms=None, or_minutes=15):
        day, cutoff = scan.orb_window(scan.parse_now(now), None, or_minutes, datetime.time(14, 30))
        return scan.scan_orb(data or self.full, syms or self.syms, day, cutoff, or_minutes, min_price, 0)

    def test_the_reel_scan_at_ten(self):
        hits, counts, failed = self.scan_at("2026-10-26 10:00", self.at10)
        self.assertEqual(len(self.syms), 100)
        self.assertEqual(counts, {"requested": 100, "fetched": 100, "unusable": 0, "illiquid": 1, "scanned": 99,
                                  "candidates": 89, "confirmed": 89, "unconfirmed": 0, "hits": 63})
        rel = [h for h in hits if h["symbol"] == "RELIANCE"][0]
        # range 9:15 to 9:30: high 1192.00 (the 9:15 bar), low 1175.30 (the 9:25 bar); the 9:55 bar closed 1193.60
        self.assertEqual(rel, RELIANCE_HIT)
        self.assertEqual(hits[0]["symbol"], "BOSCHLTD")      # sorted by time, then symbol
        self.assertEqual(sum(1 for h in hits if h["at"] == "10:00 AM"), 23)
        self.assertEqual(failed, {})
        # the cut file is the full day seen at 10:00
        self.assertEqual(self.scan_at("2026-10-26 10:00")[0], hits)

    def test_price_floor(self):
        hits, counts, _ = self.scan_at("2026-10-26 10:00", self.at10, min_price=0)
        self.assertEqual(len(hits), 64)                      # IDEA at Rs 11.39 comes in
        idea = [h for h in hits if h["symbol"] == "IDEA"][0]
        self.assertEqual((idea["at"], idea["price"], idea["or_high"]), ("9:45 AM", 11.39, 11.37))

    def test_in_progress_bar_and_no_breakout_yet(self):
        hits, _, _ = self.scan_at("2026-10-26 09:59", syms=["RELIANCE"])
        self.assertEqual(hits, [])                           # the 9:55 bar is not done until 10:00
        hits, _, _ = self.scan_at("2026-10-26 10:00", syms=["RELIANCE"])
        self.assertEqual(hits, [RELIANCE_HIT])
        hits, _, _ = self.scan_at("2026-10-26 14:30", syms=["RELIANCE"])
        self.assertEqual(hits[0]["price"], 1193.6)           # still the first close above the range

    def test_the_closing_auction_bars_do_not_make_a_chart_unusable(self):
        cut = DT(2026, 10, 26, 15, 30)
        bars = scan.session_bars(self.full["RELIANCE"], DAY, cut)
        self.assertEqual(len(bars), 74)                      # the 15:20 bar is null (closing auction)
        self.assertEqual(bars[-1]["start"], DT(2026, 10, 26, 15, 25))
        self.assertTrue(scan.usable(bars, DAY, cut, 3))
        self.assertEqual(scan.slots_due(DAY, cut), 72)       # 9:15 ... 15:10; the check stops at 15:15
        self.assertEqual(sum(1 for s in self.syms if scan.usable(scan.session_bars(self.full[s], DAY, cut), DAY,
                                                                 cut, 3)), 100)

    def test_a_missing_bar_makes_a_chart_unusable(self):
        s = bars_series("2026-10-26", [100, 100.2, 100.1, None, 100.5, 100.6, 100.7, 100.8, 100.9])
        bars = scan.session_bars(s, DAY, TEN)
        self.assertFalse(scan.usable(bars, DAY, TEN, 3))

    def test_stage_one_is_a_necessary_condition(self):
        for hhmm in ((9, 35), (10, 0), (11, 0), (14, 30)):
            cut = DT(2026, 10, 26, *hhmm)
            for sym, s in self.full.items():
                bars = scan.session_bars(s, DAY, cut)
                if scan.usable(bars, DAY, cut, 3) and scan.orb_hit(bars, 3):
                    self.assertTrue(scan.orb_candidate(bars, 3, DAY, cut), (sym, cut))

    def test_real_spark_answers_find_every_v8_hit(self):
        """Spark leaves bars out, so a missing bar after the range makes a candidate: no hit is lost."""
        bars = scan.session_bars(self.spark["EICHERMOT"], DAY, TEN)
        self.assertNotIn(DT(2026, 10, 26, 9, 50), [b["start"] for b in bars])   # spark dropped this bar...
        eicher = [h for h in self.scan_at("2026-10-26 10:00")[0] if h["symbol"] == "EICHERMOT"][0]
        self.assertEqual((eicher["bar_start"], eicher["price"], eicher["or_high"]), ("09:50", 7028.0, 7008.0))
        thin = sorted(s for s in self.syms if not scan.usable(scan.session_bars(self.spark[s], DAY, TEN), DAY, TEN, 3))
        self.assertEqual(thin, ["AXISBANK", "CIPLA", "EICHERMOT", "ETERNAL", "ITC", "PFC"])  # ...and five more
        for now, n in (("2026-10-26 10:00", 63), ("2026-10-26 14:30", 91)):
            day, cut = scan.orb_window(scan.parse_now(now), None, 15, datetime.time(14, 30))
            want, _, _ = scan.scan_orb(self.full, self.syms, day, cut, 15, 50, 0)
            got, counts, failed = scan.scan_orb(self.spark, self.syms, day, cut, 15, 50, 0, lambda s: self.full[s])
            self.assertEqual(len(want), n)
            self.assertEqual(got, want, now)
            self.assertEqual((counts["scanned"], failed), (99, {}))

    def test_confirm_uses_the_v8_highs(self):
        spark_like = {k: dict(v, h=None, l=None) for k, v in self.full.items()}  # closes only, like spark
        asked = []

        def confirm(sym):
            asked.append(sym)
            return self.full[sym]
        hits, counts, failed = scan.scan_orb(spark_like, self.syms, DAY, TEN, 15, 50, 0, confirm)
        self.assertEqual(len(hits), 63)
        self.assertEqual(counts["candidates"], len(asked))
        self.assertLess(len(asked), 99)                    # stage 1 skipped some charts

        def broken(sym):
            raise scan.ScanError("Yahoo answered HTTP 429 for /v8/finance/chart/%s.NS" % sym)
        hits, counts, failed = scan.scan_orb(spark_like, ["RELIANCE", "TCS"], DAY, TEN, 15, 50, 0, broken)
        self.assertEqual(hits, [])
        self.assertIn("429", failed["RELIANCE"])

    def test_stage_two_cap_takes_the_most_traded(self):
        spark_like = {k: dict(v, h=None, l=None) for k, v in self.full.items()}
        asked = []

        def confirm(sym):
            asked.append(sym)
            return self.full[sym]
        hits, counts, _ = scan.scan_orb(spark_like, self.syms, DAY, TEN, 15, 50, 0, confirm, max_confirm=10)
        self.assertEqual((counts["confirmed"], counts["unconfirmed"]), (10, 79))
        self.assertEqual(asked[:2], ["INFY", "BSE"])         # the most rupees traded by 10:00
        self.assertIn("RELIANCE", asked)
        hits_all, _, _ = scan.scan_orb(spark_like, self.syms, DAY, TEN, 15, 50, 0, confirm, max_confirm=0)
        self.assertEqual(len(hits_all), 63)
        for h in hits:
            self.assertIn(h, hits_all)

    def test_special_session_and_a_30_minute_range(self):
        # SYNTHETIC: Muhurat trading, Sun 2026-11-08, 14:20 to 15:30
        closes = [100, 101, 100.5, 100.8, 101.2, 100.9, 101.5] + [101.0] * 7
        s = bars_series("2026-11-08", closes, highs=[c + 0.2 for c in closes], start="14:20")
        mday = D(2026, 11, 8)
        hits, counts, _ = scan.scan_orb({"X": s}, ["X"], mday, DT(2026, 11, 8, 15, 30), 30, 0, 0, special=True)
        self.assertEqual(counts["scanned"], 1)
        self.assertEqual(hits[0]["at"], "2:55 PM")           # range 14:20 to 14:50 (high 101.4), 14:50 bar 101.5
        self.assertEqual(hits[0]["or_high"], 101.4)
        _, counts, _ = scan.scan_orb({"X": s}, ["X"], mday, DT(2026, 11, 8, 15, 30), 30, 0, 0)
        self.assertEqual(counts["scanned"], 0)               # not asked for: no session, no bars
        with self.assertRaises(scan.ScanError) as cm:
            scan.orb_window(scan.parse_now("2026-11-08 14:45"), None, 15, datetime.time(15, 30))
        self.assertIn("not a normal session", str(cm.exception))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-11-08 14:45"), None, 15, datetime.time(15, 30),
                                         special=True), (mday, DT(2026, 11, 8, 14, 45)))

    def test_window_messages(self):
        until = datetime.time(14, 30)

        def msg(now, at=None, until=until):
            with self.assertRaises(scan.ScanError) as cm:
                scan.orb_window(scan.parse_now(now), at, 15, until)
            return str(cm.exception)
        self.assertIn("weekend", msg("2026-10-24 10:00"))
        self.assertIn("Diwali-Balipratipada", msg("2026-11-10 10:00"))
        self.assertIn("Run orb again after 9:35 AM", msg("2026-10-26 09:25"))
        self.assertIn("runs 9:15 to 9:30", msg("2026-10-26 09:34"))
        self.assertIn("expired", msg("2026-10-26 14:45"))
        self.assertIn("expired", msg("2026-10-26 11:30", until=datetime.time(11, 0)))
        self.assertIn("closed at 3:30 PM", msg("2026-10-26 15:45", until=None))
        self.assertIn("later than now", msg("2026-10-26 10:00", at=datetime.time(10, 15)))
        self.assertIn("after the 3:30 PM close", msg("2026-10-26 17:00", at=datetime.time(15, 45)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-26 16:30"), datetime.time(10, 0), 15, until),
                         (DAY, TEN))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-26 09:35"), None, 15, until)[1],
                         DT(2026, 10, 26, 9, 35))
        self.assertEqual(scan.orb_window(scan.parse_now("2027-01-26 10:00"), None, 15, until)[0], D(2027, 1, 26))
        with self.assertRaises(scan.ScanError):
            scan.orb_window(scan.parse_now("2026-10-26 10:00"), None, 7, until)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.t = os.path.join(self.d, "trader")
        self.wl = os.path.join(self.t, "watchlist.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_universe_daily_orb_end_to_end(self):
        uni = os.path.join(self.t, "universe.txt")
        code, text, _ = run(["universe", "--offline", NSE, "--out", uni])
        self.assertEqual(code, 0)
        self.assertIn("NSE's EQUITY_L.csv: 19 rows.", text)
        self.assertIn("Kept 16 EQ-series stocks", text)
        self.assertIn("2 series BE (trade-for-trade), 1 series BZ (trade-for-trade)", text)
        self.assertIn("2 on a 2% or 5% band are flagged tight band", text)
        with open(uni, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 16)
        self.assertIn("RELIANCE|Reliance Industries Limited|No Band", lines)
        self.assertIn("M&M|Mahindra & Mahindra Limited|No Band", lines)
        self.assertIn("21STCENMGM|21st Century Management Services Limited|5", lines)

        # daily over that universe with the NIFTY 100 charts: 8 are in both, so partial
        code, text, _ = run(["daily", "--universe", uni, "--offline", DAILY, "--now", "2026-10-26 09:00",
                             "--out", self.wl, "--min-volume", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Fetched 8 of 16 charts (8 failed or had no data)", text)
        self.assertIn("1 skipped for a missing day in Yahoo's answer", text)       # TCS
        doc = self.read(self.wl)
        self.assertEqual((doc["mode"], doc["universe"], doc["scanned"], doc["failed"]), ("daily", 16, 7, 8))
        self.assertEqual(doc["hits"], [{"symbol": "INFY", "setups": ["bull_flag"], "price": 1153.8,
                                        "close_date": "2026-10-23", "rsi": 63.7, "volume": 13029753}])
        self.assertEqual(doc["as_of"], "2026-10-23 close")

        # the reel's 10:00 scan
        code, text, _ = run(["orb", "--offline", AT10, "--now", "2026-10-26 10:00", "--out", self.wl, "--show", "70"])
        self.assertEqual(code, 0)
        self.assertIn("Opening-range breakout scan, Mon 2026-10-26 10:00 AM IST: 15-minute range (9:15 to 9:30)", text)
        self.assertIn("63 of 99 charts closed above their opening-range high by 10:00 AM IST", text)
        self.assertIn("  10:00 AM  RELIANCE      ₹1,193.60  range 1,175.30 to 1,192.00  +0.13% over", text)
        self.assertIn("not a list of stocks to buy", text)
        self.assertIn("/india-entry SYMBOL", text)
        doc = self.read(self.wl)
        self.assertEqual({k: doc[k] for k in ("created_by", "created_at", "mode", "as_of", "universe", "scanned",
                                              "source")},
                         {"created_by": "india-scanner", "created_at": "2026-10-26T04:30:00Z", "mode": "orb",
                          "as_of": "2026-10-26 10:00 IST", "universe": 100, "scanned": 99, "source": "yahoo"})
        self.assertEqual(doc["filters"], {"min_price": 50.0, "min_volume": 0})
        self.assertEqual(len(doc["hits"]), 63)
        self.assertIn(RELIANCE_HIT, doc["hits"])

        # the hits read back as a watchlist, with --at instead of --now
        code, text, _ = run(["orb", "--offline", FULL, "--at", "10:00", "--watchlist", self.wl, "--json",
                             "--no-write"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], len(out["hits"])), (63, 63, 63))

    def test_tight_band_is_flagged_from_the_universe_file(self):
        uni = os.path.join(self.d, "u.txt")
        with open(uni, "w", encoding="utf-8") as fh:   # SYNTHETIC bands: RELIANCE really has none (F&O)
            fh.write("RELIANCE|Reliance Industries Limited|5\nTCS|Tata Consultancy Services Limited|No Band\n")
        code, text, _ = run(["orb", "--offline", AT10, "--universe", uni, "--now", "2026-10-26 10:00", "--no-write"])
        self.assertEqual(code, 0)
        self.assertIn("+0.13% over  (tight band: 5%)", text)
        self.assertEqual(text.count("tight band"), 1)

    def test_partial_stage_two_is_said(self):
        """The networked path with a fake Yahoo that answers from the Oct 26 bundle."""
        bundle = json.loads(scan._read_maybe_gz(FULL))
        calls = []

        def get(path):
            calls.append(path)
            if path.startswith("/v7/finance/spark"):
                res = []
                for y in path.split("symbols=")[1].split("&")[0].split(","):
                    y = scan.urllib.parse.unquote(y)
                    r = ((bundle.get(scan.from_yahoo(y)) or {}).get("chart") or {}).get("result")
                    if r:
                        q = r[0]["indicators"]["quote"][0]
                        # live, spark's meta carries the day's volume so far: here the bars' sum to 10:00
                        so_far = sum(v or 0 for t, v in zip(r[0]["timestamp"], q["volume"]) if t < ts("2026-10-26 10:00"))
                        meta = dict(r[0]["meta"], regularMarketVolume=so_far)
                        res.append({"symbol": y, "response": [dict(r[0], meta=meta,
                                                                   indicators={"quote": [{"close": q["close"]}]})]})
                return json.dumps({"spark": {"result": res, "error": None}})
            return json.dumps(bundle[scan.from_yahoo(scan.urllib.parse.unquote(path.split("/chart/")[1].split("?")[0]))])
        old_get, old_sleep, real_now = scan.yahoo_get, scan.SLEEP, scan.parse_now
        scan.yahoo_get, scan.SLEEP = get, (lambda s: None)
        scan.parse_now = lambda text: real_now(text or "2026-10-26 10:05")  # the machine clock, pinned
        uni = os.path.join(self.d, "u.txt")
        with open(uni, "w", encoding="utf-8") as fh:
            fh.write("\n".join(sorted(bundle)) + "\n")
        try:
            code, text, _ = run(["orb", "--universe", uni, "--now", "2026-10-26 10:00", "--max-confirm", "10",
                                 "--no-write"])
        finally:
            scan.yahoo_get, scan.SLEEP, scan.parse_now = old_get, old_sleep, real_now
        self.assertEqual(code, 0)
        self.assertIn("99 usable (1 under the price / volume floor); 89 candidates, 10 confirmed", text)
        self.assertIn("PARTIAL: 79 more candidates were not checked", text)
        self.assertIn("5 of the 10 checked charts closed above their opening-range high by 10:00 AM IST", text)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v8/")), 10)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v7/")), 5)    # 100 symbols, 20 a request
        self.assertTrue(all("range=1d&interval=5m" in c for c in calls))
        self.assertIn("M%26M.NS", "".join(calls))

    def test_plain_messages_and_exit_codes(self):
        code, _, err = run(["orb", "--offline", FULL, "--now", "2026-10-24 10:00", "--out", self.wl])
        self.assertEqual(code, 1)
        self.assertIn("No session on Sat 2026-10-24 (weekend)", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.wl))
        code, _, err = run(["orb", "--offline", FULL, "--now", "2026-10-26 09:29", "--out", self.wl])
        self.assertIn("after 9:35 AM", err)
        cfg = os.path.join(self.t, "config.json")
        os.makedirs(self.t)
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"no_entries_after": "09:45"}, fh)
        code, _, err = run(["orb", "--offline", FULL, "--now", "2026-10-26 10:00", "--config", cfg])
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        code, _, err = run(["orb", "--offline", FULL])
        self.assertIn("--at HH:MM", err)
        code, text, _ = run(["orb", "--offline", FULL, "--now", "2026-10-26 09:55", "--symbols", "RELIANCE.NS",
                             "--out", self.wl])
        self.assertEqual(code, 1)                          # no breakout yet, but the empty list is written
        self.assertIn("No chart closed above its opening-range high by 9:55 AM IST", text)
        self.assertEqual(self.read(self.wl)["hits"], [])
        code, _, err = run(["daily", "--universe", os.path.join(self.d, "nope.txt")])
        self.assertEqual(code, 1)
        self.assertIn("scan.py universe", err)
        code, _, err = run(["daily", "--offline", os.path.join(self.d, "nothing-here")])
        self.assertIn("no offline file", err)
        empty = os.path.join(self.d, "empty.json")
        with open(empty, "w", encoding="utf-8") as fh:
            fh.write("{}")
        code, _, err = run(["daily", "--offline", empty])
        self.assertEqual(code, 1)
        self.assertIn("no symbols to scan", err)
        code, _, err = run(["universe", "--offline", self.d])
        self.assertIn("no EQUITY_L.csv", err)

    def test_universe_without_bands(self):
        folder = os.path.join(self.d, "nse")
        os.makedirs(folder)
        shutil.copy(os.path.join(NSE, "EQUITY_L.csv"), folder)
        code, text, _ = run(["universe", "--offline", folder, "--out", os.path.join(self.d, "u.txt")])
        self.assertEqual(code, 0)
        self.assertIn("No price bands (no sec_list.csv in", text)
        self.assertIn("one SYMBOL|Name a line", text)

    def test_offline_folder_of_v8_files(self):
        folder = os.path.join(self.d, "charts")
        os.makedirs(folder)
        bundle = json.loads(scan._read_maybe_gz(AT10))
        for sym in ("RELIANCE", "TCS", "IDEA"):
            with open(os.path.join(folder, sym + ".json"), "w", encoding="utf-8") as fh:
                json.dump(bundle[sym], fh)
        with open(os.path.join(folder, "ZZZZQX.json"), "w", encoding="utf-8") as fh:   # Yahoo's real 404 body
            fh.write('{"chart":{"result":null,"error":{"code":"Not Found","description":"No data found, '
                     'symbol may be delisted"}}}')
        code, text, _ = run(["orb", "--offline", folder, "--now", "2026-10-26 10:00", "--no-write", "--json"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], out["failed"]), (4, 2, 1))
        self.assertEqual([h["symbol"] for h in out["hits"]], ["TCS", "RELIANCE"])   # 9:55 AM, then 10:00 AM
        self.assertEqual(code, 0)

    def test_symbol_lists(self):
        txt = os.path.join(self.d, "list.txt")
        with open(txt, "w", encoding="utf-8") as fh:
            fh.write("# my list\nRELIANCE|Reliance Industries Limited|No Band\nm&m\n\nINFY.NS\nRELIANCE\n")
        self.assertEqual(scan.symbols_from_file(txt), ["RELIANCE", "M&M", "INFY", "RELIANCE"])
        self.assertEqual(scan.bands_from_file(txt), {"RELIANCE": "No Band"})
        js = os.path.join(self.d, "wl.json")
        with open(js, "w", encoding="utf-8") as fh:
            json.dump({"hits": [{"symbol": "reliance"}, {"symbol": "M&M"}]}, fh)
        self.assertEqual(scan.symbols_from_file(js), ["RELIANCE", "M&M"])
        code, text, _ = run(["daily", "--offline", DAILY, "--watchlist", txt, "--now", "2026-10-26 09:00",
                             "--json", "--no-write", "--min-volume", "0"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"]), (3, 3))
        code, text, _ = run(["daily", "--offline", DAILY, "--limit", "3", "--now", "2026-10-26 09:00", "--json",
                             "--no-write", "--min-volume", "0"])
        self.assertEqual(json.loads(text)["universe"], 3)

    def test_daily_text_and_json(self):
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-10-26 09:00", "--no-write"])
        self.assertEqual(code, 0)
        self.assertIn("81 passed the liquidity filter (₹50+ and 5,00,000+ shares), 6 skipped for a missing day "
                      "in Yahoo's answer; 75 scanned.", text)
        self.assertIn("19 charts matched a setup: 3 bull flag, 1 reclaimed the 20-day average, 15 oversold", text)
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-10-26 09:00", "--json", "--no-write",
                             "--min-volume", "0", "--min-price", "0"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(len(out["hits"]), 27)
        self.assertEqual(out["created_by"], "india-scanner")
        self.assertEqual(out["note"], "A scan is a list of charts that match a rule, not a list of stocks to buy.")

    def test_money_is_rupees_with_indian_grouping(self):
        self.assertEqual(scan.money(1193.6), "₹1,193.60")
        self.assertEqual(scan.money(100000), "₹1,00,000")
        self.assertEqual(scan.money(1234567.5), "₹12,34,567.50")
        self.assertEqual(scan.money(-500), "-₹500")
        self.assertEqual(scan.group(500000), "5,00,000")
        self.assertEqual(scan._p(1175.3), "1,175.30")


if __name__ == "__main__":
    unittest.main()
