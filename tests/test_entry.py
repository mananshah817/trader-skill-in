"""india-entry tests. Fixtures in tests/fixtures/india-entry/:

RELIANCE-2026-10-26-5m.json       RELIANCE's Mon 2026-10-26 five-minute bars (09:15 to 15:25), cut from a
                                  60-day Yahoo chart (v8, .NS) fetched 2026-10-31. Real: the 9:15 bar has no
                                  volume (Yahoo's way on NSE) and the 15:20 bar is null (closing auction).
                                  meta trimmed to one day, like a range=1d answer; no regularMarketTime.
RELIANCE-2026-10-26-5m-1000.json  the same "as fetched at 10:00 IST": only the bars that had started before
                                  10:00 (09:15 ... 09:55), values as Yahoo has them now. meta.regularMarketTime
                                  is SET to 10:00:00 IST, the moment the cut stands for.
DEMO-1m-synthetic.json            SYNTHETIC one-minute bars (not market data): a wick above the range high, a
                                  close AT the high, a null bar, a live tick.
"""

import copy
import datetime
import gzip
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

entry = load("india-entry", "entry")

EDIR = os.path.join(FIX, "india-entry")
REL = os.path.join(EDIR, "RELIANCE-2026-10-26-5m.json")
REL10 = os.path.join(EDIR, "RELIANCE-2026-10-26-5m-1000.json")
DEMO = os.path.join(EDIR, "DEMO-1m-synthetic.json")
OPEN_TS = 1792986300       # 2026-10-26 09:15 IST (03:45 UTC)
DT = datetime.datetime
RS = "₹"


def ist(text):
    return entry.parse_now(text)


def doc(path=REL):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def chart(d=None, sym="RELIANCE"):
    return entry.parse_chart(json.dumps(d if d is not None else doc()), sym)


def at(text, d=None, **kw):
    return entry.evaluate(chart(d), ist(text), **kw)


def shift_to(d, day, open_time="09:15"):
    """The same session moved to another date (and open). IST has no daylight saving."""
    d = copy.deepcopy(d)
    r = d["chart"]["result"][0]
    new_open = int((entry.ist_to_utc(DT.combine(day, entry.parse_hhmm(open_time, "open"))) - DT(1970, 1, 1))
                   .total_seconds())
    delta = new_open - OPEN_TS
    r["timestamp"] = [t + delta for t in r["timestamp"]]
    if "regularMarketTime" in r["meta"]:
        r["meta"]["regularMarketTime"] += delta
    return d


def no_breakout(d=None):
    """Every bar after the range stays under the 1192.00 high."""
    d = copy.deepcopy(d if d is not None else doc())
    q = d["chart"]["result"][0]["indicators"]["quote"][0]
    for i in range(3, len(q["close"])):
        if q["close"][i] is None:
            continue
        q["close"][i] = min(q["close"][i], 1191.0)
        q["high"][i] = min(q["high"][i], 1191.5)
        q["open"][i] = min(q["open"][i], 1191.0)
    return d


def cut_live(d, last_trade):
    """SYNTHETIC live view: the bars that had started by `last_trade` (IST text with seconds) plus a live
    tick at that second, the way Yahoo serves a session in progress."""
    d = copy.deepcopy(d)
    r = d["chart"]["result"][0]
    lt = entry.ts_of(ist(last_trade))
    keep = [i for i, t in enumerate(r["timestamp"]) if t <= lt]
    q = r["indicators"]["quote"][0]
    r["timestamp"] = [r["timestamp"][i] for i in keep] + [lt]
    for k in q:
        q[k] = [q[k][i] for i in keep] + [q["close"][keep[-1]] if k != "volume" else 0]
    r["meta"]["regularMarketTime"] = lt
    return d


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = entry.main(argv)
    return code, out.getvalue(), err.getvalue()


class TheReel(unittest.TestCase):
    def test_waiting_at_0958_the_955_bar_is_still_forming(self):
        r = at("2026-10-26 09:58", doc(REL10))
        self.assertEqual(r["status"], "waiting")
        self.assertIsNone(r["trigger"])
        self.assertEqual(r["last"]["bar_start"], "09:50")
        self.assertEqual(r["last"]["close"], 1186.4)
        self.assertEqual(r["forming"]["bar_start"], "09:55")
        self.assertIn(RS + "5.60 under the range high " + RS + "1,192", r["reason"])
        self.assertEqual(at("2026-10-26 09:59:59", doc(REL10))["status"], "waiting")
        text = "\n".join(entry.describe(r))
        self.assertIn("The 9:55 bar is still forming", text)

    def test_triggers_at_1000_on_the_955_bar(self):
        for d in (doc(REL10), doc()):
            r = at("2026-10-26 10:00", d)
            self.assertEqual(r["status"], "triggered")
            self.assertEqual(r["opening_range"]["high"], 1192.0)       # the 9:15 bar's high
            self.assertEqual(r["opening_range"]["low"], 1175.3)        # the 9:25 bar's low
            self.assertEqual(r["opening_range"]["bars"], 3)
            t = r["trigger"]
            self.assertEqual(t["time"], "10:00 AM")
            self.assertEqual(t["close_time"], "10:00")
            self.assertEqual(t["bar_start"], "09:55")
            self.assertEqual(t["bar_index"], 8)
            self.assertEqual(t["price"], 1193.6)
            self.assertEqual(t["close"], 1193.5999755859375)
            self.assertEqual(t["pct_over"], 0.13)
            self.assertEqual(r["level"], 1192.0)
            self.assertEqual(r["prev_close"], 1190.0)
            self.assertEqual(r["exchange"], "NSE")
        text = "\n".join(entry.describe(at("2026-10-26 10:00", doc(REL10))))
        self.assertIn("TRIGGERED 10:00 AM IST at %s1,193.60: the 9:55 bar (bar 8) closed above the range high "
                      "%s1,192 (+0.13%%)." % (RS, RS), text)
        self.assertIn("Opening range 9:15-9:30: high %s1,192, low %s1,175.30 (3 of 3 bars)." % (RS, RS), text)
        self.assertIn("data to 10:00:00", text)

    def test_before_the_open_and_while_the_range_forms(self):
        r = at("2026-10-26 09:10")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "pre-open"))
        self.assertIn("opens at 9:15 (pre-open 9:00 to 9:15)", r["reason"])
        r = at("2026-10-26 09:29")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "forming"))
        self.assertEqual(r["opening_range"]["bars"], 2)
        self.assertFalse(r["opening_range"]["complete"])
        r = at("2026-10-26 09:31")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "open"))
        self.assertEqual(r["opening_range"]["bars"], 3)

    def test_the_live_tick_and_the_in_progress_bar_are_dropped(self):
        c = chart(cut_live(doc(), "2026-10-26 10:49:45"))
        self.assertEqual(entry.to_ist(entry.utc_of(c["bars"][-1]["t"])), DT(2026, 10, 26, 10, 49, 45))
        r = entry.evaluate(c, entry.utc_of(c["data_time"]))     # read as of the file's own time
        self.assertEqual(r["now"], "2026-10-26 10:49:45")
        self.assertEqual(r["_bars"][-1]["slot"], 17)               # the 10:40 bar
        self.assertEqual(r["forming"]["bar_start"], "10:45")
        self.assertEqual(r["bars_closed"], 18)
        # 10:50: closed by the clock, but Yahoo's last trade (10:49:45) is before its close
        r = entry.evaluate(c, ist("2026-10-26 10:50"))
        self.assertEqual(r["_bars"][-1]["slot"], 17)
        self.assertIn("Yahoo's last trade is 10:49:45", r["lag_note"])
        # a minute later with no newer trade it counts; the 10:49:45 tick never becomes a bar
        r = entry.evaluate(c, ist("2026-10-26 10:51"))
        self.assertEqual(r["_bars"][-1]["slot"], 18)
        self.assertEqual(r["bars_closed"], 19)
        self.assertTrue(all(b["t"] % 300 == 0 for b in r["_bars"]))

    def test_after_the_close_it_stays_triggered_and_says_it_is_late(self):
        r = at("2026-10-26 15:40")
        self.assertEqual((r["status"], r["phase"]), ("triggered", "after-close"))
        self.assertEqual(r["trigger"]["time"], "10:00 AM")
        self.assertEqual(r["bars_closed"], 74)                     # 75 slots, the null 15:20 bar dropped
        self.assertEqual(r["last"]["bar_start"], "15:25")
        text = "\n".join(entry.describe(r))
        self.assertIn("340 minutes ago", text)
        self.assertNotIn("This is the moment", text)


class Rules(unittest.TestCase):
    def test_volume_confirmation(self):
        r = at("2026-10-26 10:00", min_volume_x=1.5)
        # Yahoo shows 0 for the 9:15 bar: the average is over the 9:20 and 9:25 bars (215,851 and 210,380)
        self.assertEqual(r["opening_range"]["avg_volume"], 213116)
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_index"], t["price"], t["volume_x"]), ("10:00 AM", 8, 1193.6, 1.51))
        r = at("2026-10-26 10:01", min_volume_x=2)
        self.assertEqual(r["status"], "waiting")
        self.assertIn("1.51x the range's volume, under your 2.00x", r["reason"])
        r = at("2026-10-26 14:30", min_volume_x=2)                # no later close above had 2x either
        self.assertEqual(r["status"], "expired")

    def test_level_setup(self):
        r = at("2026-10-26 11:00", setup="level", level=1195)
        self.assertEqual(r["trigger"]["time"], "10:10 AM")
        self.assertEqual(r["trigger"]["bar_index"], 10)
        self.assertEqual(r["trigger"]["price"], 1195.9)
        # a level under the open: the first eligible close is the 9:25 bar's, at 9:30 (no_entries_before)
        r = at("2026-10-26 11:00", setup="level", level=1100)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:30 AM", 2))
        with self.assertRaises(entry.EntryError):
            at("2026-10-26 11:00", setup="level")

    def test_no_breakout_waits_then_expires_at_the_cutoff(self):
        d = no_breakout()
        r = at("2026-10-26 10:49", d)
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(at("2026-10-26 14:29", d)["status"], "waiting")
        r = at("2026-10-26 14:30", d)
        self.assertEqual(r["status"], "expired")
        self.assertIn("by 14:30", r["reason"])
        self.assertEqual(at("2026-10-26 15:45", d)["phase"], "after-close")

    def test_the_bar_closing_at_the_cutoff_still_counts_when_yahoo_lags(self):
        d = no_breakout()
        r0 = d["chart"]["result"][0]
        keep = [i for i, t in enumerate(r0["timestamp"]) if t <= OPEN_TS + 14 * 300]   # live: up to the 10:25 bar
        r0["timestamp"] = [r0["timestamp"][i] for i in keep]
        q = r0["indicators"]["quote"][0]
        for k in q:
            q[k] = [q[k][i] for i in keep]
        r0["meta"]["regularMarketTime"] = OPEN_TS + 74 * 60 + 40                       # last trade 10:29:40
        cfg = {"no_entries_before": "09:30", "no_entries_after": "10:30"}
        r = at("2026-10-26 10:30:10", d, cfg=cfg)                     # the 10:25 bar closed by the clock only
        self.assertEqual(r["status"], "waiting")
        self.assertIn("Yahoo's last trade is 10:29:40", r["lag_note"])
        self.assertEqual(at("2026-10-26 10:31", d, cfg=cfg)["status"], "expired")

    def test_config_times(self):
        cfg = {"no_entries_before": "09:30", "no_entries_after": "09:55"}
        r = at("2026-10-26 10:00", cfg=cfg)
        self.assertEqual(r["status"], "expired")
        cfg = {"no_entries_before": "10:05", "no_entries_after": "14:30"}
        r = at("2026-10-26 11:00", cfg=cfg)
        self.assertEqual(r["trigger"]["time"], "10:05 AM")         # the 10:00 bar, first to close at or after 10:05
        self.assertEqual(r["trigger"]["price"], 1192.5)
        cfg = {"no_entries_before": "09:30", "no_entries_after": "16:00"}
        self.assertEqual(at("2026-10-26 11:00", cfg=cfg)["no_entries_after"], "15:30")   # never past the close

    def test_bad_inputs(self):
        with self.assertRaises(entry.EntryError):
            at("2026-10-26 10:00", or_minutes=7)
        with self.assertRaises(entry.EntryError):
            at("2026-10-26 10:00", interval="15m")
        with self.assertRaises(entry.EntryError) as cm:
            entry.parse_now("tomorrow")
        self.assertIn('an India time like "2026-10-26 10:00"', str(cm.exception))
        with self.assertRaises(entry.EntryError):
            entry.parse_hhmm("25:00", "x")
        self.assertEqual(entry.parse_now("2026-10-26 10:00"), DT(2026, 10, 26, 4, 30))
        self.assertEqual(entry.parse_now("2026-10-26T04:30:00Z"), DT(2026, 10, 26, 4, 30))
        self.assertEqual(entry.parse_now("2026-10-26 10:00 IST"), DT(2026, 10, 26, 4, 30))
        self.assertEqual(entry.parse_now("2026-10-26T10:00:00+05:30"), DT(2026, 10, 26, 4, 30))

    def test_or_minutes_30(self):
        r = at("2026-10-26 11:00", or_minutes=30)
        self.assertEqual(r["opening_range"]["bars"], 6)
        self.assertEqual(r["opening_range"]["high"], 1192.0)
        self.assertEqual(r["trigger"]["time"], "10:00 AM")

    def test_symbols(self):
        self.assertEqual(entry.norm_symbol("reliance.ns"), ("RELIANCE", "NSE"))
        self.assertEqual(entry.norm_symbol("M&M"), ("M&M", "NSE"))
        self.assertEqual(entry.norm_symbol("BAJAJ-AUTO"), ("BAJAJ-AUTO", "NSE"))
        self.assertEqual(entry.norm_symbol("RELIANCE.BO"), ("RELIANCE", "BSE"))
        with self.assertRaises(entry.EntryError):
            entry.norm_symbol("BRK.B")
        self.assertEqual(entry.money(100000), RS + "1,00,000")
        self.assertEqual(entry.money(1234567.5), RS + "12,34,567.50")
        self.assertEqual(entry.money(-500), "-" + RS + "500")


class OneMinute(unittest.TestCase):
    def c(self):
        return entry.parse_chart(json.dumps(doc(DEMO)), "DEMO")

    def test_wick_and_close_at_the_high_do_not_trigger(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m")
        self.assertEqual(r["now"], "2026-10-26 09:51:30")
        self.assertEqual(r["opening_range"], {"high": 100.5, "low": 99.0, "minutes": 15, "bars": 15, "of": 15,
                                              "complete": True, "avg_volume": 10667})
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("9:44 AM", "09:43", 28, 100.62))
        r = entry.evaluate(c, ist("2026-10-26 09:43:59"), interval="1m")
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["forming"]["bar_start"], "09:43")
        self.assertEqual(r["last"]["close"], 100.5)             # the 9:42 bar closed AT the high

    def test_null_bar_tick_and_volume(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m", min_volume_x=2)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:47 AM", 31))
        slots = [b["slot"] for b in r["_bars"]]
        self.assertNotIn(19, slots)                               # 9:34 came as nulls
        self.assertEqual(slots[-1], 35)                           # 9:51 still forming, 9:51:30 tick dropped
        self.assertEqual(r["forming"]["bar_start"], "09:51")

    def test_the_file_and_the_interval_must_agree(self):
        code, _, err = run(["check", "DEMO", "--offline", DEMO])
        self.assertEqual(code, 1)
        self.assertIn("1m bars, not 5m", err)


class Calendar(unittest.TestCase):
    def test_other_days_and_no_daylight_saving(self):
        for day in (datetime.date(2026, 12, 7), datetime.date(2026, 3, 9), datetime.date(2026, 6, 15)):
            d = shift_to(doc(), day)
            r = entry.evaluate(chart(d), ist("%s 10:00" % day))
            self.assertEqual(r["status"], "triggered", day)
            self.assertEqual(r["trigger"]["time"], "10:00 AM", day)
            self.assertEqual(r["trigger"]["bar_index"], 8, day)
            self.assertEqual(entry.evaluate(chart(d), ist("%s 09:58" % day))["status"], "waiting", day)
        self.assertEqual(ist("2026-12-07 10:00"), DT(2026, 12, 7, 4, 30))

    def test_weekend_holiday_and_empty_data(self):
        r = at("2026-10-24 10:30")
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertEqual(r["next_open"], "2026-10-26 09:15")
        r = at("2026-11-10 10:30")
        self.assertIn("NSE holiday: Diwali-Balipratipada", r["reason"])
        self.assertEqual(r["next_open"], "2026-11-11 09:15")
        self.assertEqual(entry.next_open(datetime.date(2026, 11, 6)), "2026-11-09 09:15")  # Muhurat Sunday skipped
        empty = copy.deepcopy(doc())
        res = empty["chart"]["result"][0]
        del res["timestamp"]
        res["indicators"] = {"quote": [{}]}
        r = at("2026-10-26 10:00", empty)
        self.assertEqual((r["status"], r["phase"]), ("no-data", "open"))
        r = at("2026-10-27 10:00")                                # Yahoo sent yesterday's bars only
        self.assertEqual(r["status"], "no-data")

    def test_a_year_nse_has_not_published(self):
        r = entry.evaluate(chart(shift_to(doc(), datetime.date(2027, 1, 26))), ist("2027-01-26 10:00"))
        self.assertEqual(r["status"], "triggered")                # a weekday counts: no 2027 holidays invented
        self.assertFalse(r["calendar_known"])
        self.assertIn("NSE has not published its 2027 holiday list in this tool yet: check nseindia.com before "
                      "trading.", entry.describe(r))

    def test_special_session_only_when_asked(self):
        d = shift_to(doc(), datetime.date(2026, 11, 8), "14:20")   # SYNTHETIC: the reel's bars at Muhurat time
        r = entry.evaluate(chart(d), ist("2026-11-08 15:10"))
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertIn("Muhurat trading (Diwali)", r["reason"])
        self.assertIn("--special", r["reason"])
        r = entry.evaluate(chart(d), ist("2026-11-08 15:10"), special=True)
        self.assertEqual((r["opens"], r["closes"]), ("14:20", "15:30"))
        self.assertEqual(r["status"], "expired")                  # the 15:00 bar broke out, after 14:30 (config)
        cfg = {"no_entries_before": "09:30", "no_entries_after": "15:20"}
        r = entry.evaluate(chart(d), ist("2026-11-08 15:10"), special=True, cfg=cfg)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("3:05 PM", 8))
        self.assertIn("india-risk blocks new entries in it", "\n".join(entry.describe(r)))
        # asked for by date: --now with the day in it
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "RELIANCE.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(d, fh)
            code, text, _ = run(["check", "RELIANCE", "--offline", p, "--now", "2026-11-08 14:50"])
            self.assertIn("Special session today: Muhurat trading (Diwali), 14:20 to 15:30 IST", text)
            code, text, _ = run(["check", "RELIANCE", "--offline", p, "--now", "14:50"])
            self.assertNotIn("Muhurat", text)                     # today, not that day

    def test_yahoo_errors(self):
        bad = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                              "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(entry.EntryError) as cm:
            entry.parse_chart(bad, "ZZZZQX")
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(entry.EntryError):
            entry.parse_chart("<html>", "RELIANCE")


class Network(unittest.TestCase):
    """Yahoo is never called: _open_url and _sleep are swapped for fakes."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._open, self._sleep = entry._open_url, entry._sleep
        entry._sleep = lambda s: self.sleeps.append(s)
        with open(REL, encoding="utf-8") as fh:
            self.body = fh.read()

    def tearDown(self):
        entry._open_url, entry._sleep = self._open, self._sleep

    def answers(self, *codes):
        seq = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url)
            code = seq.pop(0)
            if code == 200:
                return self.body
            raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b'{"chart":{"result":null,'
                                                                           b'"error":{"description":"No data found"}}}'))
        entry._open_url = fake

    def test_url_and_429_retry_once_after_a_pause(self):
        self.answers(429, 200)
        c = entry.get_chart("RELIANCE", "5m", datetime.date(2026, 10, 26), today=datetime.date(2026, 10, 26))
        self.assertEqual(len(c["bars"]), 74)
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS"
                                      "?range=1d&interval=5m"] * 2)
        self.assertEqual(self.sleeps, [entry.BACKOFF])

    def test_429_twice_falls_back_to_query1(self):
        self.answers(429, 429, 200)
        entry.get_chart("RELIANCE", "5m", datetime.date(2026, 10, 26), today=datetime.date(2026, 10, 26))
        self.assertEqual([u.split("/")[2] for u in self.calls],
                         ["query2.finance.yahoo.com", "query2.finance.yahoo.com", "query1.finance.yahoo.com"])

    def test_5xx_falls_back_without_waiting(self):
        self.answers(503, 200)
        entry.get_chart("RELIANCE", "5m", datetime.date(2026, 10, 26), today=datetime.date(2026, 10, 26))
        self.assertEqual(self.sleeps, [])
        self.assertIn("query1", self.calls[1])

    def test_still_busy_is_one_plain_error(self):
        self.answers(429, 429, 429, 429)
        code, out, err = run(["check", "RELIANCE", "--now", "2026-10-26 10:00"])
        self.assertEqual(code, 1)
        self.assertIn("HTTP 429", err)
        self.assertNotIn("Traceback", err)

    def test_unknown_symbol_404(self):
        self.answers(404)
        with self.assertRaises(entry.EntryError) as cm:
            entry.get_chart("ZZZZQX", "5m", datetime.date(2026, 10, 26), today=datetime.date(2026, 10, 26))
        self.assertIn("No data found", str(cm.exception))

    def test_ranges_and_paths(self):
        today = datetime.date(2026, 10, 27)
        self.assertEqual(entry.chart_range(today, today, "5m"), "1d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 26), today, "5m"), "5d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 5), today, "5m"), "1mo")
        self.assertEqual(entry.chart_range(datetime.date(2026, 9, 7), today, "5m"), "60d")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(datetime.date(2026, 10, 5), today, "1m")
        self.assertEqual(entry.chart_path("M&M.NS", "1d", "5m"), "/v8/finance/chart/M%26M.NS?range=1d&interval=5m")
        self.assertIn("/chart/RELIANCE.BO?", entry.chart_path(entry.yahoo_symbol("RELIANCE", "BSE"), "1d", "5m"))


def live_provider(d, lag_until=None, calls=None):
    """Bars the way Yahoo serves them at `now`: closed bars final, the in-progress bar
    showing a spike to 1192.50 (above the high, then it closes under), plus a live tick.
    lag_until: before this UTC time Yahoo's last trade lags 30 seconds behind. SYNTHETIC on real bars."""
    base = chart(d)

    def provider(now):
        if calls is not None:
            calls.append(now)
        nts = entry.ts_of(now)
        lagging = lag_until is not None and now < lag_until
        data_ts = nts - 30 if lagging else nts
        bars = []
        for b in base["bars"]:
            if b["t"] % 300 or b["t"] > data_ts:
                continue
            b = dict(b)
            if b["t"] + 300 > data_ts:
                b["c"], b["h"] = 1192.50, max(b["h"], 1192.50)
            bars.append(b)
        if data_ts % 300:
            bars.append({"t": data_ts, "o": 1192.5, "h": 1192.5, "l": 1192.5, "c": 1192.5, "v": 0})
        return {"symbol": "RELIANCE", "exchange": "NSE", "meta": {}, "bars": bars, "prev_close": 1190.0,
                "data_time": data_ts}
    return provider


class Watch(unittest.TestCase):
    OPTS = {"setup": "orb", "or_minutes": 15, "interval": "5m", "level": None, "min_volume_x": None, "cfg": None}

    def loop(self, provider, start, **kw):
        clock = entry.SimClock(ist(start))
        lines, fired = [], []
        code, res = entry.watch_loop(provider, clock.now, clock.sleep, lines.append, dict(self.OPTS),
                                     on_trigger=lambda r, now: fired.append((r, now)), **kw)
        return code, res, lines, fired, clock

    def test_fires_on_the_first_closed_bar_not_the_spike_in_progress(self):
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(doc(), calls=calls), "2026-10-26 09:52", every=60)
        self.assertEqual(code, 0)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0][0]["trigger"]["time"], "10:00 AM")
        self.assertEqual(fired[0][0]["trigger"]["price"], 1193.6)
        # it looked at 9:52 (the 9:50 bar spiking to 1192.50 in progress) and at 9:55:02 (that bar closed 1186.40)
        self.assertEqual([entry.to_ist(c).strftime("%H:%M:%S") for c in calls], ["09:52:00", "09:55:02", "10:00:02"])
        text = "\n".join(lines)
        self.assertIn(" 9:50 bar  closed %s1,186.40  %s5.60 under %s1,192" % (RS, RS, RS), text)
        self.assertIn(" 9:55 bar  closed %s1,193.60  TRIGGERED" % RS, text)
        self.assertEqual(text.count("TRIGGERED"), 1)

    def test_waits_for_yahoo_to_catch_up(self):
        lag = ist("2026-10-26 10:00:30")
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(doc(), lag_until=lag, calls=calls),
                                                   "2026-10-26 09:57", every=30)
        self.assertEqual(code, 0)
        times = [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times, ["09:57:00", "10:00:02", "10:00:32"])   # 10:00:02: Yahoo still at 9:59:32
        self.assertEqual(fired[0][0]["trigger"]["time"], "10:00 AM")

    def test_from_before_the_open_prints_the_range_bar_by_bar(self):
        code, res, lines, fired, clock = self.loop(live_provider(doc()), "2026-10-26 09:00", every=30)
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        self.assertIn("not open yet", lines[0])
        self.assertIn("watching from the first bar's close at 9:20", lines[0])
        self.assertIn(" 9:25 bar  closed %s1,184.50  opening range set: high %s1,192, low %s1,175.30" % (RS, RS, RS),
                      text)
        self.assertEqual(sum(1 for x in lines if " bar  closed " in x), 9)    # 9:15 ... 9:55, one line each

    def test_a_computer_that_dozes_off_catches_up_when_it_wakes(self):
        class Dozing(entry.SimClock):
            naps = 0

            def sleep(self, seconds):
                entry.SimClock.sleep(self, seconds)
                self.naps += 1
                if self.naps == 3:                      # the laptop sleeps for 17 minutes mid-wait
                    self.t += datetime.timedelta(minutes=17)
        clock = Dozing(ist("2026-10-26 09:31"))
        calls, lines = [], []
        code, res = entry.watch_loop(live_provider(doc(), calls=calls), clock.now, clock.sleep, lines.append,
                                     dict(self.OPTS), every=30)
        self.assertEqual(code, 0)
        times = [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times[:2], ["09:31:00", "09:49:30"])   # straight after waking, not a long sleep later
        text = "\n".join(lines)
        for start in (" 9:30", " 9:35", " 9:40", " 9:45"):
            self.assertIn(start + " bar  closed", text)          # the bars it slept through, one line each
        self.assertEqual(res["trigger"]["time"], "10:00 AM")

    def test_expires_and_until(self):
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), "2026-10-26 14:00", every=300)
        self.assertEqual(code, 1)
        self.assertEqual(res["status"], "expired")
        self.assertEqual(fired, [])
        self.assertEqual(entry.to_ist(clock.now()).strftime("%H:%M"), "14:30")
        until = DT(2026, 10, 26, 11, 0)
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), "2026-10-26 10:30", every=300,
                                                   until=until)
        self.assertEqual(code, 1)
        self.assertIn("--until 11:00", lines[-1])

    def test_closed_day_and_repeated_errors(self):
        code, _, lines, _, _ = self.loop(live_provider(doc()), "2026-10-24 10:00")
        self.assertEqual(code, 1)
        self.assertIn("weekend", lines[0])

        def broken(now):
            raise entry.EntryError("Yahoo did not answer")
        code, _, lines, _, _ = self.loop(broken, "2026-10-26 10:00", every=30)
        self.assertEqual(code, 1)
        self.assertIn("Stopping after 5 failed reads", lines[-1])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_check_write_is_the_contract_shape(self):
        code, text, _ = run(["check", "RELIANCE", "--offline", REL10, "--now", "2026-10-26 10:00", "--write"])
        self.assertEqual(code, 0)
        self.assertIn("TRIGGERED 10:00 AM IST at %s1,193.60" % RS, text)
        self.assertIn("/india-exit", text)
        with open(os.path.join("trader", "entry.json"), encoding="utf-8") as fh:
            e = json.load(fh)
        want = {"created_by": "india-entry", "symbol": "RELIANCE", "exchange": "NSE", "setup": "orb", "side": "buy",
                "date": "2026-10-26", "status": "triggered", "time": "10:00 AM", "bar_start": "09:55",
                "bar_index": 8, "price": 1193.6, "level": 1192.0, "interval": "5m",
                "opening_range": {"high": 1192.0, "low": 1175.3, "minutes": 15},
                "source": "yahoo", "checked_at": "2026-10-26T04:30:00Z"}
        for k, v in want.items():
            self.assertEqual(e[k], v, k)
        self.assertEqual(sorted(e), sorted(list(want) + ["reason"]))

    def test_check_waiting_json_and_exit_codes(self):
        code, text, _ = run(["check", "RELIANCE", "--offline", REL10, "--now", "2026-10-26 09:58", "--json"])
        self.assertEqual(code, 1)
        r = json.loads(text)
        self.assertEqual(r["status"], "waiting")
        self.assertNotIn("_bars", r)
        code, text, _ = run(["check", "RELIANCE", "--offline", REL10, "--now", "2026-10-26 09:58", "--write",
                             "e.json"])
        with open("e.json", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["status"], "waiting")
        self.assertIn("still forming", text)
        # without --now an offline file is read as of its own time: 10:00:00
        code, text, _ = run(["check", "reliance.ns", "--offline", REL10])
        self.assertEqual(code, 0)
        self.assertIn("RELIANCE  Mon 2026-10-26 10:00 IST", text)

    def test_offline_gz_folder_and_mismatch(self):
        os.mkdir("charts")
        with open(REL, "rb") as src, gzip.open(os.path.join("charts", "RELIANCE.NS.json.gz"), "wb") as dst:
            dst.write(src.read())
        code, text, _ = run(["check", "reliance", "--offline", "charts", "--now", "2026-10-26 10:00"])
        self.assertEqual(code, 0)
        code, _, err = run(["check", "TCS", "--offline", REL10])
        self.assertEqual(code, 1)
        self.assertIn("holds RELIANCE, not TCS", err)
        code, _, err = run(["check", "BRK.B", "--offline", REL10])
        self.assertIn("not an NSE symbol", err)

    def test_longs_only_and_flag_errors(self):
        code, _, err = run(["check", "RELIANCE", "--offline", REL10, "--side", "short"])
        self.assertEqual(code, 1)
        self.assertIn("longs only", err)
        code, _, err = run(["check", "RELIANCE", "--offline", REL10, "--level", "1100"])
        self.assertIn("--level is for --setup level", err)
        code, _, err = run(["check", "RELIANCE", "--offline", REL10, "--setup", "level"])
        self.assertIn("needs --level", err)

    def test_config_is_read(self):
        os.mkdir("trader")
        with open(os.path.join("trader", "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"no_entries_after": "09:55"}, fh)
        code, text, _ = run(["check", "RELIANCE", "--offline", REL10, "--now", "2026-10-26 10:00"])
        self.assertEqual(code, 1)
        self.assertIn("EXPIRED", text)
        with open(os.path.join("trader", "config.json"), "w", encoding="utf-8") as fh:
            fh.write("{nope")
        code, _, err = run(["check", "RELIANCE", "--offline", REL10])
        self.assertIn("not valid JSON", err)

    def test_watch_replays_the_file_and_writes_the_entry(self):
        code, text, _ = run(["watch", "RELIANCE", "--offline", REL, "--now", "2026-10-26 09:35", "--every", "30"])
        self.assertEqual(code, 0)
        self.assertIn(" 9:55 bar  closed %s1,193.60  TRIGGERED" % RS, text)
        with open(os.path.join("trader", "entry.json"), encoding="utf-8") as fh:
            e = json.load(fh)
        self.assertEqual((e["bar_index"], e["time"], e["exchange"]), (8, "10:00 AM", "NSE"))
        code, text, _ = run(["watch", "RELIANCE", "--offline", REL, "--now", "2026-10-26 09:35", "--until", "09:50",
                             "--out", "x.json"])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists("x.json"))

    def test_watchlist(self):
        os.mkdir("charts")
        shutil.copy(REL, os.path.join("charts", "RELIANCE.json"))
        shutil.copy(DEMO, os.path.join("charts", "DEMO.json"))
        os.mkdir("trader")
        with open(os.path.join("trader", "watchlist.json"), "w", encoding="utf-8") as fh:
            json.dump({"created_by": "india-scanner", "hits": [
                {"symbol": "RELIANCE", "setups": ["orb"], "or_high": 1192.0, "or_low": 1175.3},
                {"symbol": "ZZZZQX", "setups": ["orb"]}, {"symbol": "DEMO", "setups": ["orb"]}]}, fh)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", "2026-10-26 10:00"])
        self.assertEqual(code, 0)
        rows = text.splitlines()
        self.assertTrue(rows[1].startswith("RELIANCE    triggered  10:00 AM  %s1,193.60   %s1,192" % (RS, RS)),
                        rows[1])
        self.assertIn("ZZZZQX      error: no offline file", rows[2])
        self.assertIn("DEMO        error: DEMO has 1m bars", rows[3])
        self.assertIn("1 of 3 triggered", text)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", "2026-10-26 09:58", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)[0]["result"]["status"], "waiting")
        code, _, err = run(["watchlist", "--watchlist", "missing.json"])
        self.assertIn("run /india-scanner first", err)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "india-entry", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: india-entry\n"))
        self.assertGreater(len(text.splitlines()), 60)
        low = " ".join(text.lower().split())
        for must in ("bar by bar", "can lag", "/india-exit", "longs only", "in the background", "/loop",
                     "nothing was placed", "10:00 am ist", "₹1,193.60", "bar 8", "not financial advice",
                     "71%", "say yes", "paper", "no f&o", "ist", "`python3` on macos/linux", "9:15 bar"):
            self.assertIn(must, low)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit", "trader-", "new york", "$3", "$1"):
            self.assertNotIn(never, low)


if __name__ == "__main__":
    unittest.main()
