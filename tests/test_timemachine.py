"""india-timemachine tests.

The fixture tests/fixtures/india-timemachine/RELIANCE-60d5m.json.gz is REAL data: Yahoo's v8 chart for
RELIANCE.NS (range=60d, interval=5m) fetched on 2026-10-31, cut to what Yahoo would have answered at
10:00 IST on Monday 2026-10-26: every bar that started at or after 10:00 that day, and every later day,
is dropped, and meta (regularMarketTime 10:00:00 IST, price, day high/low, previous close, trading
periods) is set to match. The 52-week low was set after that day, so it is left out. A real fetch at
10:00 would reach back about four sessions further than this cut (the download starts 2026-08-05).

What the real data taught: Yahoo leaves most NSE bars from 15:15 on empty for RELIANCE (an F&O stock,
in the closing auction from 15:15), and the 11:45 and 11:50 bars of 2026-10-15 are empty too. So a
session counts as complete when it has every bar the replay can draw, not all 75.
"""

import gzip
import hashlib
import io
import json
import os
import random
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

tm = load("india-timemachine", "timemachine")

DIR = os.path.join(FIX, "india-timemachine")
RELIANCE = os.path.join(DIR, "RELIANCE-60d5m.json.gz")
with gzip.open(RELIANCE, "rt", encoding="utf-8") as _fh:
    TEXT = _fh.read()
NOW = "2026-10-26 10:00"
NOW_UTC = tm.parse_now(NOW)

# the reel's plan, as india-exit writes it (docs/SPEC.md)
PLAN = {"created_by": "india-exit", "symbol": "RELIANCE", "exchange": "NSE", "side": "buy", "date": "2026-10-26",
        "setup": "orb", "entry": 1193.6, "entry_time": "10:00 AM", "entry_bar": 8, "interval": "5m",
        "opening_range": {"high": 1192.0, "low": 1175.3, "minutes": 15},
        "stop": 1183.6, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 1203.6, "target_r": 1.0, "target_label": "1R (one times the risk)",
        "risk_per_share": 10.0, "reward_risk": 1.0, "flat_by": "15:00", "tick": 0.1}

# fingerprints of the reel run (seed 19, 10,000 replays, window 2, to 15:00), computed with this port on
# 2026-10-31 under Python 3.14: they pin the draws bit for bit
SIM_BANDS = "88394190ea1a38b4"
SIM_PATHS = "35fea760711a6a2f"
SIM_TRADES = "95e83adf546c8cc1"
SIM_HIST = {"lo": 1172.28, "hi": 1213.12,
            "bins": [124, 104, 143, 230, 298, 369, 461, 536, 594, 700, 724, 784, 753, 787, 650, 588, 482, 461,
                     309, 281, 162, 128, 104, 72]}


def sha(x):
    return hashlib.sha256(json.dumps(x).encode("utf-8")).hexdigest()[:16]


def plan(**kw):
    raw = dict(PLAN)
    raw.update(kw)
    return tm.normalize_plan(raw)


def machine(p=None, text=TEXT, now=NOW_UTC, **kw):
    return tm.time_machine(text, p or plan(), now, **kw)


def edited(fn):
    """The fixture's chart JSON, changed by fn(result), back as text."""
    doc = json.loads(TEXT)
    fn(doc["chart"]["result"][0])
    return json.dumps(doc)


def keep_bars(result, keep):
    """Keep only the bars where keep(ts) is true."""
    q = result["indicators"]["quote"][0]
    idx = [i for i, ts in enumerate(result["timestamp"]) if keep(ts)]
    result["timestamp"] = [result["timestamp"][i] for i in idx]
    for k in q:
        q[k] = [q[k][i] for i in idx]


def ist(ts):
    return tm.to_ist(tm.utc_of(ts))


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tm.main(argv)
    return code, out.getvalue(), err.getvalue()


class Reel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = machine()

    def test_the_reel_odds(self):
        m = self.doc["mc"]
        self.assertEqual((m["replays"], m["seed"], m["window"], m["until"]), (10000, 19, 2, "15:00"))
        self.assertEqual(m["pool_sessions"], 54)
        self.assertEqual(m["pool_bars"], 71)                     # 09:15 ... 15:05: what a 15:00 replay can draw
        self.assertEqual(m["bars_left"], 60)
        self.assertEqual((m["pool_from"], m["pool_to"]), ("2026-08-05", "2026-10-23"))
        self.assertEqual(m["p_target"], 0.2176)
        self.assertEqual(m["p_stop"], 0.3002)
        self.assertEqual(m["p_neither"], 0.4822)
        self.assertEqual(m["p_green"], 0.4468)
        self.assertEqual(m["median_close"], 1192.42)
        self.assertEqual(m["p5_close"], 1177.95)
        self.assertEqual(m["p95_close"], 1206.86)
        self.assertEqual(m["avg_R"], -0.095)
        self.assertEqual(m["r_at_target"], 1.0)

    def test_bit_for_bit_with_the_pinned_run(self):
        m = self.doc["mc"]
        self.assertEqual(sha(m["bands"]), SIM_BANDS)
        self.assertEqual(len(m["sample_paths"]), 160)
        self.assertEqual(sha(m["sample_paths"]), SIM_PATHS)
        self.assertEqual(m["hist"], SIM_HIST)
        self.assertEqual(m["bands"][-1], [1178.28, 1186.69, 1192.42, 1198.17, 1206.08])

    def test_the_history_replay(self):
        h = self.doc["history"]
        self.assertEqual((h["sessions"], h["triggered"]), (54, 24))
        self.assertEqual((h["target"], h["stop"], h["close"], h["green"]), (11, 10, 3, 12))
        self.assertEqual(h["avg_R"], 0.009)
        self.assertEqual(h["rule"], {"setup": "orb", "or_minutes": 15, "stop_rule": "or_mid", "target_r": 1.0})
        slim = [{"date": t["date"], "entry": t["entry"], "outcome": t["outcome"], "R": t["R"]} for t in h["trades"]]
        self.assertEqual(sha(slim), SIM_TRADES)
        first = h["trades"][0]
        self.assertEqual((first["date"], first["time"], first["entry"], first["stop"], first["target"], first["outcome"]),
                         ("2026-08-06", "9:45 AM", 1300.9, 1292.0, 1309.8, "target"))
        for t in h["trades"]:                                    # every stop and target sits on the NSE tick
            for px in (t["stop"], t["target"]):
                self.assertAlmostEqual(round(px / tm.tick_size(px)) * tm.tick_size(px), px, places=6)

    def test_the_verdict_leans_toward_the_stop(self):
        self.assertEqual(self.doc["verdict"], "Leans toward the stop: target first 21.8 % vs stop first 30.0 %.")
        # 1R breaks even at 50 % of the replays that hit one side; the reel's target got 42.0 %
        gap, share, even = tm.lean(self.doc["mc"])
        self.assertEqual((round(share, 3), even), (0.42, 0.5))
        self.assertEqual(self.doc["verdict_notes"],
                         ["48.2 % of the replays hit neither by 3:00 PM; the price at 3:00 PM decides those."])

    def test_bands_start_at_the_entry(self):
        m = self.doc["mc"]
        self.assertEqual(m["bands"][0], [1193.6] * 5)
        self.assertEqual(len(m["bands"]), 61)
        self.assertEqual((m["band_times"][0], m["band_times"][-1], len(m["band_times"])), ("10:00", "15:00", 61))
        self.assertTrue(all(p[0] == 1193.6 and len(p) == 61 for p in m["sample_paths"]))

    def test_the_plan_is_carried_through(self):
        p = self.doc["plan"]
        self.assertEqual((p["symbol"], p["exchange"], p["entry"], p["stop"], p["target"], p["entry_bar"],
                          p["entry_time"]), ("RELIANCE", "NSE", 1193.6, 1183.6, 1203.6, 8, "10:00 AM"))
        self.assertEqual((p["risk_per_share"], p["target_r"], p["flat_by"]), (10.0, 1.0, "15:00"))
        self.assertEqual(self.doc["warnings"], [])
        self.assertEqual(self.doc["created_by"], "india-timemachine")
        self.assertEqual(self.doc["data"]["symbol"], "RELIANCE.NS")
        self.assertEqual(self.doc["data"]["last_bar"], "2026-10-26 09:55 IST")
        self.assertEqual(self.doc["data"]["short_sessions_left_out"], [{"date": "2026-10-15", "bars": 72}])

    def test_the_text_report(self):
        text = "\n".join(tm.report(self.doc))
        for line in ("RELIANCE buy, Monday 2026-10-26: entry 1193.60 at 10:00 AM IST (bar 8), stop 1183.60, "
                     "target 1203.60 (1R, risk 10.00 a share)",
                     "10,000 replays", "10:00 AM to 3:00 PM IST (60 five-minute bars)", "seed 19",
                     "54 complete sessions", "target first   21.8 %", "stop first     30.0 %",
                     "neither        48.2 %   still open at 3:00 PM", "green          44.7 %",
                     "median 1192.42, 90 % of replays between 1177.95 and 1206.86",
                     "Average R: -0.095", "before Zerodha's charges",
                     "fired 24 times: 11 target, 10 stop, 3 neither. Average R +0.009",
                     "Leans toward the stop: target first 21.8 % vs stop first 30.0 %.\n48.2 % of the replays "
                     "hit neither by 3:00 PM; the price at 3:00 PM decides those.",
                     "Left out 1 incomplete session(s)", "2026-10-15", "not a prediction", "--seed 19"):
            self.assertIn(line, text)
        for word in ("profit", "P&L", "guarantee", "real time", "tick by tick", " ET", "New York", "$"):
            self.assertNotIn(word, text)


class Seeds(unittest.TestCase):
    def test_same_seed_same_numbers(self):
        a = machine(replays=2000, seed=5)["mc"]
        b = machine(replays=2000, seed=5)["mc"]
        self.assertEqual(a, b)

    def test_another_seed_gives_other_numbers(self):
        m = machine(seed=7)["mc"]
        self.assertNotEqual((m["p_target"], m["p_stop"], m["median_close"]), (0.2176, 0.3002, 1192.42))
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertNotEqual(sha(m["bands"]), SIM_BANDS)

    def test_the_draws_are_stable_across_pythons(self):
        # random.Random(seed), randint and choice are stable since Python 3.2; the reel numbers above
        # are the real proof, this pins the first draws so a failure points at the RNG
        r = random.Random(19)
        self.assertEqual([r.randint(-2, 2) for _ in range(8)], [-2, 2, -2, 2, -1, 1, 0, 2])
        self.assertEqual(r.choice(range(54)), 18)

    def test_window_and_replays(self):
        a = machine(replays=500, window=0)["mc"]
        self.assertEqual((a["replays"], a["window"], a["pool_bars"]), (500, 0, 69))
        self.assertNotEqual(a["bands"], machine(replays=500)["mc"]["bands"])
        with self.assertRaises(tm.TMError):
            machine(replays=0)
        with self.assertRaises(tm.TMError):
            machine(window=11)


class Bars(unittest.TestCase):
    def setUp(self):
        self.result = tm.chart_result(TEXT, "RELIANCE.NS")

    def test_the_cut_holds_only_bars_started_before_10(self):
        meta, bars, dropped = tm.parse_bars(self.result, NOW_UTC)
        self.assertEqual(dropped, {"no_price": 109, "off_grid": 0, "not_complete": 0, "outside_hours": 0})
        last = bars[-1]
        self.assertEqual((last["date"], last["start"], last["slot"]), ("2026-10-26", "09:55", 8))
        self.assertEqual(last["c"], 1193.5999755859375)
        self.assertEqual(meta["regularMarketTime"], 1792989000)          # 10:00:00 IST
        # with the real clock (any time later) the data's own time still holds
        self.assertEqual(tm.parse_bars(self.result, tm.parse_now(None))[1][-1]["start"], "09:55")
        self.assertEqual(len([b for b in bars if b["date"] == "2026-10-26"]), 9)

    def test_the_in_progress_bar_and_the_live_tick_are_dropped(self):
        def live(res):  # what a fetch at 10:00:45 would add: the open 10:00 bar and the tick (synthetic)
            res["meta"]["regularMarketTime"] = 1792989045
            res["timestamp"] += [1792989000, 1792989045]
            for k, v in (("open", 1193.9), ("high", 1194.5), ("low", 1193.0), ("close", 1194.2), ("volume", 9000)):
                res["indicators"]["quote"][0][k] += [v, v]
        meta, bars, dropped = tm.parse_bars(tm.chart_result(edited(live), "X"), tm.parse_now("2026-10-26 10:00:45"))
        self.assertEqual((dropped["off_grid"], dropped["not_complete"]), (1, 1))
        self.assertEqual(bars[-1]["start"], "09:55")

    def test_now_pins_the_clock(self):
        bars = tm.parse_bars(self.result, tm.parse_now("2026-10-26 09:57"))[1]
        today = [b for b in bars if b["date"] == "2026-10-26"]
        self.assertEqual(len(today), 8)
        with self.assertRaises(tm.TMError) as cm:
            machine(now=tm.parse_now("2026-10-26 09:57"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("09:50 bar", str(cm.exception))
        self.assertEqual(machine(now=tm.parse_now("2026-10-26 10:00 IST"), replays=200)["mc"]["pool_sessions"], 54)
        self.assertEqual(tm.parse_now("2026-10-26T04:30Z"), NOW_UTC)

    def test_india_time_has_no_daylight_saving(self):
        stamps = [1792986300,   # 2026-10-26 03:45Z = 09:15 IST
                  1793331900,   # 2026-10-30 03:45Z = 09:15 IST (US clocks change on Nov 1, India's never do)
                  1767584700,   # 2026-01-05 03:45Z = 09:15 IST
                  1793331000,   # 2026-10-30 03:30Z = 09:00 IST, the pre-open
                  1793349000,   # 2026-10-30 08:30Z = 14:00 IST
                  1793354400]   # 2026-10-30 10:00Z = 15:30 IST, the close
        res = {"meta": {"symbol": "X.NS"}, "timestamp": stamps,
               "indicators": {"quote": [{"open": [1] * 6, "high": [1] * 6, "low": [1] * 6, "close": [1] * 6,
                                         "volume": [0] * 6}]}}
        meta, bars, dropped = tm.parse_bars(res, tm.parse_now("2026-11-03T00:00Z"))
        self.assertEqual([(b["date"], b["start"], b["slot"]) for b in bars],
                         [("2026-01-05", "09:15", 0), ("2026-10-26", "09:15", 0), ("2026-10-30", "09:15", 0),
                          ("2026-10-30", "14:00", 57)])
        self.assertEqual(dropped["outside_hours"], 2)
        self.assertEqual(tm.ist_to_utc(tm.to_ist(tm.utc_of(1793331900))), tm.utc_of(1793331900))

    def test_a_session_with_a_gap_is_left_out(self):
        def cut(res):  # 2026-09-04 loses its afternoon from 13:00, like a Yahoo outage
            keep_bars(res, lambda ts: not (ist(ts).date().isoformat() == "2026-09-04" and ist(ts).hour >= 13))
        doc = machine(text=edited(cut), replays=300)
        self.assertEqual(doc["mc"]["pool_sessions"], 53)
        self.assertEqual(doc["data"]["short_sessions_left_out"], [{"date": "2026-09-04", "bars": 45},
                                                                  {"date": "2026-10-15", "bars": 72}])
        self.assertEqual(doc["history"]["sessions"], 53)
        self.assertNotIn("2026-09-04", [t["date"] for t in doc["history"]["trades"]])
        self.assertIn("Left out 2 incomplete session", "\n".join(tm.report(doc)))

    def test_the_closing_auction_bars_are_not_needed_for_a_15_00_replay(self):
        # 2026-09-04 has no 15:20 bar (and most days lack 15:15 to 15:25): still in the pool at 15:00
        days = tm.split_sessions(tm.parse_bars(self.result, NOW_UTC)[1])
        self.assertNotIn(73, days["2026-09-04"])
        self.assertEqual(sum(1 for d in days if d < "2026-10-26" and len(days[d]) == tm.SESSION_BARS), 0)
        self.assertIn("2026-09-04", [d for d, _ in tm.full_sessions(days, "2026-10-26", 71)[0]])

    def test_a_replay_into_the_closing_auction_is_refused_with_the_reason(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(until="15:30", replays=10)
        self.assertIn("only 0 complete session(s) of RELIANCE", str(cm.exception))
        self.assertIn("closing auction", str(cm.exception))
        self.assertIn("end the replay by 15:05 with --until", str(cm.exception))
        self.assertEqual(machine(until="15:05", replays=100)["mc"]["pool_sessions"], 54)
        self.assertEqual(machine(until="15:10", replays=100)["mc"]["pool_sessions"], 48)   # six days lack 15:15

    def test_today_is_not_in_the_pool(self):
        doc = machine(replays=200)
        self.assertEqual(doc["mc"]["pool_to"], "2026-10-23")
        self.assertNotIn("2026-10-26", [t["date"] for t in doc["history"]["trades"]])

    def test_a_missing_price_is_dropped(self):
        def hole(res):
            res["indicators"]["quote"][0]["close"][5] = None
        doc = machine(text=edited(hole), replays=200)
        self.assertEqual(doc["data"]["dropped"]["no_price"], 110)
        self.assertEqual(doc["mc"]["pool_sessions"], 53)   # 2026-08-05 now has a gap

    def test_empty_and_broken_data(self):
        empty = json.dumps({"chart": {"result": [{"meta": {"symbol": "RELIANCE.NS"}, "timestamp": [],
                                                  "indicators": {"quote": [{}]}}], "error": None}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=empty)
        self.assertIn("no complete 5-minute bars", str(cm.exception))
        gone = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                               "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=gone)
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(tm.TMError):
            machine(text="<html>rate limited</html>")

    def test_fewer_than_20_sessions_is_refused(self):
        def trim(res):
            keep_bars(res, lambda ts: ist(ts).date().isoformat() >= "2026-09-24")
        with self.assertRaises(tm.TMError) as cm:
            machine(text=edited(trim))
        self.assertIn("only 19 complete session(s)", str(cm.exception))
        self.assertIn("at least 20", str(cm.exception))
        self.assertNotIn("closing auction", str(cm.exception))

        def twenty(res):
            keep_bars(res, lambda ts: ist(ts).date().isoformat() >= "2026-09-23")
        self.assertEqual(machine(text=edited(twenty), replays=200)["mc"]["pool_sessions"], 20)

    def test_the_data_must_be_the_plans_symbol(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(symbol="TCS"))
        self.assertIn("data is for RELIANCE.NS but the plan is for TCS.NS", str(cm.exception))


class Plans(unittest.TestCase):
    def test_an_entry_not_in_the_data_is_refused(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-10-27"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("no bars for that day", str(cm.exception))
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-10-20"))
        self.assertIn("an NSE holiday (Dussehra)", str(cm.exception))
        with self.assertRaises(tm.TMError) as cm:      # the 10:00 bar has not closed in this data
            machine(plan(entry_time="10:05 AM", entry_bar=None))
        self.assertIn("not in the data", str(cm.exception))

    def test_plan_checks(self):
        cases = [
            (dict(entry_time="10:02 AM", entry_bar=None), "5-minute grid"),
            (dict(entry_time="3:30 PM", entry_bar=None), "between 9:20 AM and 3:25 PM IST"),
            (dict(entry_bar=9), "disagrees with itself"),
            (dict(entry_time=None, entry_bar=None), "entry_time"),
            (dict(interval="1m"), "7 days of 1-min bars"),
            (dict(stop=1195.0), "stop < entry < target"),
            (dict(side="short"), "buy or sell"),
            (dict(symbol=""), "NSE trading symbol"),
            (dict(symbol="RELIANCE INDUSTRIES"), "NSE trading symbol"),
            (dict(exchange="NYSE"), "NSE or BSE"),
            (dict(entry="abc"), "number for entry"),
            (dict(date="Oct 26"), "date like"),
            (dict(opening_range={"minutes": 7}), "multiple of 5"),
            (dict(flat_by="noon"), "flat_by"),
        ]
        for change, words in cases:
            with self.assertRaises(tm.TMError, msg=change) as cm:
                plan(**change)
            self.assertIn(words, str(cm.exception), change)

    def test_symbols(self):
        self.assertEqual(plan(symbol="reliance.ns")["symbol"], "RELIANCE")
        self.assertEqual(plan(symbol="M&M")["symbol"], "M&M")
        p = plan(symbol="RELIANCE.BO", exchange=None)
        self.assertEqual((p["symbol"], p["exchange"]), ("RELIANCE", "BSE"))
        self.assertEqual(tm.yahoo_symbol("M&M"), "M&M.NS")

    def test_entry_bar_or_entry_time_alone(self):
        self.assertEqual(plan(entry_time=None)["entry_time"], "10:00 AM")
        self.assertEqual(plan(entry_bar=None)["entry_bar"], 8)
        self.assertEqual(plan(entry_time="10:00", entry_bar=None)["entry_bar"], 8)
        self.assertEqual(plan(entry_time="10:00am", entry_bar=None)["entry_bar"], 8)
        self.assertEqual(plan(entry_time="10:00 AM IST", entry_bar=None)["entry_bar"], 8)

    def test_until_defaults_to_the_flat_by_and_cuts_the_replay(self):
        self.assertEqual(machine(plan(flat_by=None), replays=200)["mc"]["until"], "15:00")
        self.assertEqual(machine(plan(flat_by="14:30"), replays=200)["mc"]["until"], "14:30")
        doc = machine(until="14:30", replays=500)
        m = doc["mc"]
        self.assertEqual((m["until"], m["bars_left"], m["band_times"][-1]), ("14:30", 54, "14:30"))
        self.assertIn("still open at 2:30 PM", "\n".join(tm.report(doc)))
        self.assertEqual(machine(until="2:30 PM", replays=500)["mc"], m)
        for bad, words in (("10:00 AM", "nothing to replay"), ("15:35", "no later than the 3:30 PM close"),
                           ("14:32", "5-minute grid"), ("noon", "India time")):
            with self.assertRaises(tm.TMError) as cm:
                machine(until=bad, replays=10)
            self.assertIn(words, str(cm.exception), bad)
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(flat_by="15:45"), replays=10)
        self.assertIn("the plan's flat_by 15:45", str(cm.exception))

    def test_an_entry_outside_its_bar_is_flagged(self):
        doc = machine(plan(entry=1200.0, stop=1190.0, target=1210.0), replays=200)
        self.assertIn("outside that bar's range", doc["warnings"][0])

    def test_stops_and_targets_round_to_the_tick(self):
        self.assertEqual(tm.tick_away(1183.65, True), 1183.6)
        self.assertEqual(tm.tick_away(1183.65, False), 1183.7)
        self.assertEqual(tm.tick_away(1175.300048828125, True), 1175.3)
        self.assertEqual(tm.tick_away(1175.2999999999997, True), 1175.3)
        self.assertEqual(tm.tick_away(249.995, True), 249.99)
        self.assertEqual(tm.tick_away(999.97, True), 999.95)
        self.assertEqual(tm.tick_toward(1203.65, True), 1203.6)
        self.assertEqual(tm.tick_toward(1183.61, False), 1183.7)

    def test_the_stop_rule_is_worked_out_when_not_given(self):
        rule = machine(plan(stop_rule=None), replays=100)["history"]["rule"]
        self.assertEqual(rule["stop_rule"], "or_mid")
        rule = machine(plan(stop_rule=None, stop=1175.3, target=1211.9), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["target_r"]), ("or_low", 1.0))
        rule = machine(plan(stop_rule="atr", stop=1184.1, target=1212.6), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["stop_pct"], rule["target_r"]), ("pct", 0.7959, 2.0))

    def test_a_setup_other_than_orb_skips_the_history(self):
        doc = machine(plan(setup="flag"), replays=200)
        self.assertIsNone(doc["history"]["rule"])
        self.assertIn("orb", doc["history"]["note"])
        self.assertEqual(doc["mc"]["pool_sessions"], 54)
        self.assertIn("History: skipped", "\n".join(tm.report(doc)))

    def test_a_sell_plan_mirrors_the_rules(self):
        doc = machine(plan(side="sell", stop=1203.6, target=1183.6, stop_rule="or_high"), replays=2000)
        m = doc["mc"]
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertEqual(m["r_at_target"], 1.0)
        self.assertEqual(doc["history"]["rule"]["stop_rule"], "or_high")
        for t in doc["history"]["trades"]:
            self.assertTrue(t["target"] < t["entry"] < t["stop"], t)
        self.assertIn("below the entry", "\n".join(tm.report(doc)))

    def test_the_verdict_thresholds_at_1r(self):
        # 1R: the target's share of the replays that hit one side against 50 %
        self.assertTrue(tm.verdict({"p_target": 0.30, "p_stop": 0.26}).startswith("Close to a coin flip"))
        self.assertTrue(tm.verdict({"p_target": 0.40, "p_stop": 0.30}).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict({"p_target": 0.20, "p_stop": 0.30}).startswith("Leans toward the stop"))
        self.assertTrue(tm.verdict({"p_target": 0.60, "p_stop": 0.30}).startswith("Strongly toward the target"))
        self.assertEqual(tm.verdict({"p_target": 0.10, "p_stop": 0.60, "r_at_target": 1.0}),
                         "Strongly toward the stop: target first 10.0 % vs stop first 60.0 %.")
        self.assertEqual(tm.verdict({"p_target": 0.0, "p_stop": 0.0, "until": "14:30"}),
                         "No replay reached the target or the stop by 2:30 PM: the price at 2:30 PM decides every one.")

    def test_a_2r_target_is_judged_against_its_break_even(self):
        # made-up odds: a 2R plan needs 33 % of the replays that hit one side, this one got 32 %
        mc = {"p_target": 0.241, "p_stop": 0.505, "p_neither": 0.254, "r_at_target": 2.0, "avg_R": 0.095}
        self.assertEqual(tm.verdict(mc), "Close to break-even: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 32 % (target first 24.1 % vs stop "
                                         "first 50.5 %).")
        self.assertEqual(tm.verdict_notes(mc), [])
        lean = dict(mc, p_target=0.15, p_stop=0.55)
        self.assertTrue(tm.verdict(lean).startswith("Leans toward the stop: at 2R the target has to come first in "
                                                    "33 % of the replays that hit one side; it did in 21 %"))
        self.assertEqual(tm.verdict_notes(lean), ["Average R is +0.095 all the same: the replays that hit neither "
                                                  "pull it the other way."])
        self.assertTrue(tm.verdict(dict(mc, p_target=0.30, p_stop=0.40)).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict(dict(mc, p_target=0.40, p_stop=0.40)).startswith("Strongly toward the target"))
        self.assertIn("at 1.5R the target has to come first in 40 %", tm.verdict(dict(mc, r_at_target=1.5)))

    def test_2r_on_the_fixture_near_break_even(self):
        # stop 1190.60, 2R target 1199.60: 29.2 % vs 69.9 % looks lopsided, but 2R only needs a third
        doc = machine(plan(stop=1190.6, target=1199.6, stop_rule="pct"))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["avg_R"]), (2.0, 0.2919, 0.699, -0.111))
        self.assertEqual(doc["verdict"], "Close to break-even: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 29 % (target first 29.2 % vs stop "
                                         "first 69.9 %).")
        self.assertEqual(doc["verdict_notes"], [])

    def test_2r_on_the_fixture_a_clear_lean(self):
        # the reel's entry and stop with a 2R target 1213.60: the target first only 2.0 % vs stop first 30.1 %
        doc = machine(plan(target=1213.6))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["p_neither"], m["avg_R"]),
                         (2.0, 0.0197, 0.3008, 0.6795, -0.104))
        self.assertEqual(doc["verdict"], "Strongly toward the stop: at 2R the target has to come first in 33 % of "
                                         "the replays that hit one side; it did in 6 % (target first 2.0 % vs stop "
                                         "first 30.1 %).")
        self.assertEqual(doc["verdict_notes"], [
            "68.0 % of the replays hit neither by 3:00 PM; the price at 3:00 PM decides those."])
        text = "\n".join(tm.report(doc))
        self.assertIn(doc["verdict"] + "\n" + doc["verdict_notes"][0], text)


class Network(unittest.TestCase):
    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep = tm._get, tm._sleep
        tm._sleep = self.sleeps.append

    def tearDown(self):
        tm._get, tm._sleep = self._get, self._sleep

    def answer(self, codes):
        codes = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url.split(".finance")[0].split("//")[1])
            code = codes.pop(0)
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return TEXT
        tm._get = fake

    def test_url_and_user_agent(self):
        self.assertEqual(tm.chart_url("reliance"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=60d&interval=5m")
        self.assertIn("/chart/M%26M.NS?", tm.chart_url("M&M", "query1"))
        self.assertIn("/chart/RELIANCE.BO?", tm.chart_url("RELIANCE", "query1", "BSE"))
        self.assertEqual(tm.UA, "Mozilla/5.0 (compatible; india-trader-skill/1.0)")

    def test_429_waits_retries_then_falls_back_to_query1(self):
        self.answer([429, 429, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2", "query1"])
        self.assertEqual(self.sleeps, [tm.BACKOFF, tm.PAUSE])

    def test_one_429_then_ok(self):
        self.answer([429, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2"])

    def test_5xx_falls_back(self):
        self.answer([503, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query2", "query1"])

    def test_rate_limited_everywhere_is_one_plain_line(self):
        self.answer([429, 429, 429, 429])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("RELIANCE")
        self.assertIn("rate limiting", str(cm.exception))

    def test_404(self):
        self.answer([404])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("ZZZZ")
        self.assertIn("check the NSE symbol", str(cm.exception))

    def test_run_over_the_network_path(self):
        self.answer([200])
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "plan.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(PLAN, fh)
            code, text, _ = run(["run", "--plan", p, "--now", NOW, "--replays", "300"])
            self.assertEqual(code, 0)
            self.assertEqual(self.calls, ["query2"])
        finally:
            shutil.rmtree(d)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.plan = os.path.join(self.d, "trader", "plan.json")
        os.makedirs(os.path.dirname(self.plan))
        with open(self.plan, "w", encoding="utf-8") as fh:
            json.dump(PLAN, fh, indent=2)
        self.out = os.path.join(self.d, "trader", "timemachine.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_run_with_the_plan_end_to_end(self):
        code, text, err = run(["run", "--plan", self.plan, "--offline", RELIANCE, "--now", NOW, "--write", self.out])
        self.assertEqual(code, 0, err)
        self.assertIn("target first   21.8 %", text)
        self.assertIn("Leans toward the stop: target first 21.8 % vs stop first 30.0 %.", text)
        self.assertIn("Wrote %s." % self.out, text)
        self.assertIn("/india-budget", text)
        with open(self.out, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(k for k in ("plan", "mc", "history") if k in doc), ["history", "mc", "plan"])
        self.assertEqual(doc["created_by"], "india-timemachine")
        self.assertEqual(doc["source"], "offline")
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["avg_R"]), (0.2176, -0.095))
        self.assertEqual(sha(doc["mc"]["sample_paths"]), SIM_PATHS)
        self.assertEqual((doc["history"]["triggered"], doc["history"]["avg_R"]), (24, 0.009))
        self.assertEqual(doc["plan"]["from"], self.plan)

    def test_json_output(self):
        code, text, _ = run(["run", "--plan", self.plan, "--offline", RELIANCE, "--now", NOW, "--json"])
        self.assertEqual(code, 0)
        doc = json.loads(text)
        self.assertEqual(doc["mc"]["p_stop"], 0.3002)
        self.assertNotIn("sample_paths", doc["mc"])
        self.assertEqual(doc["verdict"][:21], "Leans toward the stop")
        self.assertFalse(os.path.exists(self.out))

    def test_the_plan_as_flags_matches_the_file(self):
        code, text, err = run(["run", "--symbol", "reliance", "--date", "2026-10-26", "--entry", "1193.60",
                               "--stop", "1183.60", "--target", "1203.60", "--entry-time", "10:00 AM",
                               "--offline", RELIANCE, "--now", NOW, "--json"])
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["p_green"], doc["mc"]["until"]), (0.2176, 0.4468, "15:00"))
        self.assertEqual(doc["plan"]["stop_rule"], "or_mid")
        self.assertEqual((doc["history"]["target"], doc["history"]["stop"], doc["history"]["avg_R"]), (11, 10, 0.009))
        code, _, err = run(["run", "--symbol", "RELIANCE", "--entry", "1193.60", "--offline", RELIANCE])
        self.assertEqual(code, 1)
        self.assertIn("--stop", err)

    def test_offline_folder_and_default_paths(self):
        shutil.copy(RELIANCE, self.d)
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            code, text, err = run(["run", "--offline", ".", "--now", NOW, "--replays", "500", "--write"])
            self.assertEqual(code, 0, err)
            self.assertTrue(os.path.exists(os.path.join("trader", "timemachine.json")))
            code, text, err = run(["chart", "--from"])
            self.assertEqual(code, 0, err)
            self.assertIn("* median", text)
            with open(os.path.join("trader", "timemachine.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["mc"]["replays"], 500)
        finally:
            os.chdir(cwd)

    def test_errors_are_one_plain_line(self):
        for argv in (["run", "--plan", os.path.join(self.d, "nope.json")],
                     ["run", "--plan", self.plan, "--offline", os.path.join(self.d, "missing.json.gz")],
                     ["run", "--plan", self.plan, "--offline", RELIANCE, "--until", "9:00"],
                     ["run", "--plan", self.plan, "--offline", RELIANCE, "--until", "15:30"],
                     ["chart", "--from", os.path.join(self.d, "nope.json")]):
            code, text, err = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertTrue(err.startswith("timemachine.py: "), err)
            self.assertNotIn("Traceback", err)
        self.assertEqual(run([])[0], 1)

    def test_chart(self):
        run(["run", "--plan", self.plan, "--offline", RELIANCE, "--now", NOW, "--write", self.out])
        code, text, _ = run(["chart", "--from", self.out])
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertIn("2,000 replay paths from the 10:00 AM IST entry (seed 19)", lines[0])
        self.assertTrue(any(l.startswith("  1203.60 |") and l.endswith("  target") for l in lines))
        self.assertTrue(any(l.startswith("  1183.60 |") and l.endswith("  stop") for l in lines))
        self.assertTrue(any(l.startswith("  1193.60 |*") and l.endswith("  entry") for l in lines))
        self.assertIn("10:00", text)
        self.assertIn("3:00", text)
        self.assertEqual(lines[-2:], ["Leans toward the stop: target first 21.8 % vs stop first 30.0 %.",
                                      "48.2 % of the replays hit neither by 3:00 PM; the price at 3:00 PM decides those."])
        code, again, _ = run(["chart", "--plan", self.plan, "--offline", RELIANCE, "--now", NOW])
        self.assertEqual(again, text)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "india-timemachine", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: india-timemachine\n"))
        low = " ".join(text.lower().split())
        for must in ("21.8 %", "30.0 %", "48.2 %", "44.7 %", "1192.42", "-0.095", "fired 24 times", "+0.009",
                     "leans toward the stop", "seed 19", "not a prediction", "/india-budget", "/india-exit",
                     "15:00", "closing auction", "not financial advice", "71%", "yes", "python3 on macos/linux"):
            self.assertIn(must, low)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "trader-", "new york", "tsla"):
            self.assertNotIn(never, low)


if __name__ == "__main__":
    unittest.main()
