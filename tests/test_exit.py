"""india-exit tests. Bars for the ATR stop: tests/fixtures/india-exit/RELIANCE-2026-10-26-5m.json,
cut from REAL Yahoo data (v8 chart RELIANCE.NS, range=60d interval=5m, fetched 2026-10-31) down to the
Mon 2026-10-26 session as Yahoo would have answered at 10:45 IST: 18 closed bars, 09:15 to 10:40, with
meta (regularMarketTime, day high/low, previous close) set to match. The entry.json below is the
contract shape india-entry writes (the original's fields plus "exchange"), written by hand so these
tests do not depend on another skill."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, FIX, ROOT

exitp = load("india-exit", "exit")

BARS = os.path.join(FIX, "india-exit", "RELIANCE-2026-10-26-5m.json")

# trader/plan.json for the reel, exactly as the contract writes it
REEL_PLAN = {"created_by": "india-exit", "symbol": "RELIANCE", "exchange": "NSE", "side": "buy",
             "date": "2026-10-26", "setup": "orb",
             "entry": 1193.6, "entry_time": "10:00 AM", "entry_bar": 8, "interval": "5m",
             "opening_range": {"high": 1192.0, "low": 1175.3, "minutes": 15},
             "stop": 1183.6, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
             "target": 1203.6, "target_r": 1.0, "target_label": "1R (one times the risk)",
             "risk_per_share": 10.0, "reward_risk": 1.0, "flat_by": "15:00", "tick": 0.1}
REEL_ENTRY = {"created_by": "india-entry", "symbol": "RELIANCE", "exchange": "NSE", "setup": "orb", "side": "buy",
              "date": "2026-10-26", "status": "triggered", "time": "10:00 AM", "bar_start": "09:55", "bar_index": 8,
              "price": 1193.6, "level": 1192.0, "interval": "5m",
              "opening_range": {"high": 1192.0, "low": 1175.3, "minutes": 15},
              "source": "yahoo", "checked_at": "2026-10-26T04:30:00Z"}


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def c(entry_px, stop, target="2R", oh=1192.0, ol=1175.3, atr=None, n=None, tick=None):
    sr, sp = exitp.parse_stop(stop)
    tr, tp = exitp.parse_target(target)
    return exitp.compute(entry_px, sr, sp, tr, tp, oh, ol, atr, n, tick)


class Ticks(unittest.TestCase):
    def test_the_nse_tick_schedule(self):
        for price, tick in ((249.99, 0.01), (250, 0.05), (1000, 0.05), (1000.05, 0.10), (1193.6, 0.10),
                            (5000, 0.10), (5000.5, 0.50), (10000, 0.50), (10001, 1.00), (20000, 1.00),
                            (20005, 5.00)):
            self.assertEqual(exitp.tick_size(price), tick, price)

    def test_round_down_and_up_in_paise(self):
        self.assertEqual(exitp.floor_tick(1183.65), (Decimal("1183.6"), Decimal("0.1")))
        self.assertEqual(exitp.floor_tick(1183.6), (Decimal("1183.6"), Decimal("0.1")))
        self.assertEqual(exitp.floor_tick(1183.5999755859375)[0], Decimal("1183.5"))   # not a paisa above it
        self.assertEqual(exitp.floor_tick(249.999)[0], Decimal("249.99"))
        self.assertEqual(exitp.floor_tick(999.97)[0], Decimal("999.95"))
        self.assertEqual(exitp.floor_tick(1000.03)[0], Decimal("1000"))          # across the band edge, on tick
        self.assertEqual(exitp.floor_tick(20007)[0], Decimal("20005"))
        self.assertEqual(exitp.ceil_tick(1194.79)[0], Decimal("1194.8"))
        self.assertEqual(exitp.floor_tick(1183.65, Decimal("0.05"))[0], Decimal("1183.65"))   # a config override

    def test_rupees_with_indian_grouping(self):
        self.assertEqual(exitp.money(100000), "₹1,00,000")
        self.assertEqual(exitp.money(1234567.5), "₹12,34,567.50")
        self.assertEqual(exitp.money(46.88), "₹46.88")
        self.assertEqual(exitp.money(-500), "-₹500")
        self.assertEqual(exitp.money(1193.6), "₹1,193.60")
        self.assertEqual(exitp.money(10), "₹10")

    def test_symbols(self):
        self.assertEqual(exitp.clean_symbol("reliance"), ("RELIANCE", "NSE"))
        self.assertEqual(exitp.clean_symbol("RELIANCE.NS"), ("RELIANCE", "NSE"))
        self.assertEqual(exitp.clean_symbol("m&m"), ("M&M", "NSE"))
        self.assertEqual(exitp.clean_symbol("BAJAJ-AUTO.BO"), ("BAJAJ-AUTO", "BSE"))
        self.assertEqual(exitp.yahoo_symbol("M&M"), "M&M.NS")
        for bad in ("", "RELIANCE INDUSTRIES", "-X", "A" * 21):
            with self.assertRaises(exitp.PlanError):
                exitp.clean_symbol(bad)


class Math(unittest.TestCase):
    def test_the_reel_or_mid_1r(self):
        p = c(1193.6, "or-mid", "1R")
        self.assertEqual(p["stop"], Decimal("1183.6"))           # 1183.65 rounded down to the 0.10 tick
        self.assertEqual(p["risk"], Decimal("10.00"))
        self.assertEqual(p["target"], Decimal("1203.6"))
        self.assertEqual(p["reward_risk"], Decimal("1.00"))
        self.assertEqual(p["tick"], Decimal("0.1"))
        self.assertEqual(p["target_label"], "1R (one times the risk)")
        self.assertEqual(p["stop_detail"], "(₹1,192 + ₹1,175.30) / 2 = ₹1,183.65, rounded down to "
                                           "the ₹0.10 tick")

    def test_the_default_2r(self):
        p = c(1193.6, "or-mid")
        self.assertEqual(p["target"], Decimal("1213.6"))
        self.assertEqual(p["target_label"], "2R (two times the risk)")

    def test_raw_yahoo_floats_give_the_same_prices(self):
        p = c(1193.5999755859375, "or-mid", "1R", 1192.0, 1175.300048828125)
        self.assertEqual((p["entry"], p["stop"], p["target"]), (Decimal("1193.60"), Decimal("1183.6"), Decimal("1203.6")))

    def test_other_stops(self):
        p = c(1193.6, "or-low")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("1175.3"), Decimal("18.30"), Decimal("1230.2")))
        p = c(1193.6, "price:1183.69")
        self.assertEqual(p["stop"], Decimal("1183.6"))              # rounds down to the tick, away from the entry
        p = c(1193.6, "pct:1")                                       # 1181.664 -> 1181.60
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("1181.6"), Decimal("12.00"), Decimal("1217.6")))
        p = c(1193.6, "atr:1.5", atr=6.3222, n=9)                   # 1193.60 - 9.4833 = 1184.1167
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("1184.1"), Decimal("9.50"), Decimal("1212.6")))
        self.assertEqual(p["stop_label"], "1.5 x ATR(9) under the entry")

    def test_targets_round_down_to_the_tick_and_price_targets(self):
        p = c(100, "price:99.01", "1.5R", None, None)
        self.assertEqual(p["target"], Decimal("101.48"))            # 100 + 1.485, rounded down
        self.assertEqual(p["target_label"], "1.5R (1.5 times the risk)")
        self.assertEqual(p["reward_risk"], Decimal("1.49"))
        p = c(1193.6, "or-mid", "price:1210")
        self.assertEqual(p["reward_risk"], Decimal("1.64"))
        self.assertEqual(p["target_label"], "your price (1.64R)")
        self.assertEqual(p["target_r"], Decimal("1.64"))
        p = c(1193.6, "or-mid", "price:1210.07")
        self.assertEqual(p["target"], Decimal("1210"))
        p = c(995, "price:990", "2R", None, None)                   # a target over the Rs 1,000 band edge
        self.assertEqual((p["tick"], p["target"], p["target_tick"]), (Decimal("0.05"), Decimal("1005"), Decimal("0.1")))

    def test_refusals_say_why(self):
        cases = [("price:1193.6", "2R", "at or above the entry"),
                 ("price:1195", "2R", "at or above the entry"),
                 ("or-mid", "price:1193.6", "at or below the entry"),
                 ("price:1193.5", "2R", "too tight to survive the spread"),
                 ("pct:0.04", "2R", "too tight to survive the spread")]
        for stop, target, why in cases:
            with self.assertRaises(exitp.PlanError) as cm:
                c(1193.6, stop, target)
            self.assertIn(why, str(cm.exception), stop)
        # 0.05% of 1193.60 is 0.5968: a 0.60 risk (6 ticks) is allowed, 0.50 is not
        self.assertEqual(c(1193.6, "price:1193.0")["risk"], Decimal("0.60"))
        with self.assertRaises(exitp.PlanError) as cm:
            c(1193.6, "price:1193.1")
        self.assertIn("less than 0.05% of the price (₹0.60 on ₹1,193.60)", str(cm.exception))
        # at Rs 20,005 the tick is 5.00: an 11-rupee risk clears 0.05% (10.00) but not 3 ticks (15)
        with self.assertRaises(exitp.PlanError) as cm:
            c(20005, "price:19994", "2R", None, None)
        self.assertIn("less than 3 ticks (3 x ₹5 = ₹15): too tight to survive the spread", str(cm.exception))
        self.assertEqual(c(20005, "price:19990", "2R", None, None)["risk"], Decimal("15.00"))
        with self.assertRaises(exitp.PlanError):
            c(1183.0, "or-mid")                                     # the midpoint is above this entry
        for bad in ("mid-ish", "pct:100", "price:0", "atr:-1"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_stop(bad)
        for bad in ("0R", "R", "lots", "price:x"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_target(bad)
        with self.assertRaises(exitp.PlanError):
            c(1193.6, "or-mid", oh=None, ol=None)
        with self.assertRaises(exitp.PlanError):
            c(1193.6, "or-mid", oh=1175.3, ol=1192.0)
        self.assertEqual(exitp.parse_stop("price:₹1,183.6"), ("price", Decimal("1183.6")))

    def test_atr_on_the_session_bars(self):
        ch = exitp.load_chart("RELIANCE", "5m", None, BARS)
        day = exitp.datetime.date(2026, 10, 26)
        bars = exitp.session_bars(ch, day, "5m", upto_slot=8)
        self.assertEqual(len(bars), 9)                              # 09:15 ... the 09:55 entry bar
        # by hand: the true range of each bar, the first one without the overnight gap
        trs, prev = [], None
        for b in bars:
            r = b["h"] - b["l"]
            if prev is not None:
                r = max(r, abs(b["h"] - prev), abs(b["l"] - prev))
            trs.append(r)
            prev = b["c"]
        self.assertAlmostEqual(trs[0], 1192.0 - 1182.5, places=4)
        atr, n = exitp.atr_of(bars)
        self.assertEqual(n, 9)
        self.assertAlmostEqual(atr, sum(trs) / 9, places=9)
        self.assertAlmostEqual(atr, 6.3222, places=4)
        # later in the day it uses the last 14 bars
        all_bars = exitp.session_bars(ch, day, "5m")
        self.assertEqual(len(all_bars), 18)
        self.assertEqual(exitp.atr_of(all_bars)[1], 14)
        # a clock at 10:01 IST sees nothing after the 09:55 bar
        self.assertEqual(len(exitp.session_bars(ch, day, "5m", now_utc=exitp.parse_now("2026-10-26 10:01"))), 9)
        with self.assertRaises(exitp.PlanError) as cm:
            exitp.load_chart("TCS", "5m", None, BARS)
        self.assertIn("holds RELIANCE.NS, not TCS.NS", str(cm.exception))


class Time(unittest.TestCase):
    def test_india_time_has_no_daylight_saving(self):
        self.assertEqual(exitp.parse_now("2026-10-26 10:00"), exitp.datetime.datetime(2026, 10, 26, 4, 30))
        self.assertEqual(exitp.parse_now("2026-10-26 10:00 IST"), exitp.datetime.datetime(2026, 10, 26, 4, 30))
        self.assertEqual(exitp.parse_now("2026-01-05 10:00"), exitp.datetime.datetime(2026, 1, 5, 4, 30))
        self.assertEqual(exitp.parse_now("2026-10-26T04:30Z"), exitp.datetime.datetime(2026, 10, 26, 4, 30))
        with self.assertRaises(exitp.PlanError) as cm:
            exitp.parse_now("Monday")
        self.assertIn('an India time like "2026-10-26 10:00"', str(cm.exception))
        self.assertEqual(exitp.parse_clock("10:00 AM IST", "t"), exitp.datetime.time(10, 0))

    def test_the_nse_calendar(self):
        d = exitp.datetime.date
        self.assertEqual(exitp.session(d(2026, 10, 26)), (exitp.datetime.time(9, 15), exitp.datetime.time(15, 30)))
        self.assertIsNone(exitp.session(d(2026, 10, 20)))           # Dussehra
        self.assertIsNone(exitp.session(d(2026, 10, 25)))           # a Sunday
        self.assertEqual(exitp.session(d(2026, 11, 8)), (exitp.datetime.time(14, 20), exitp.datetime.time(15, 30)))


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, path, doc):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def plan(self, *args):
        return run(exitp, ["plan", "--now", "2026-10-26 10:01"] + list(args))

    def read_plan(self):
        with open(os.path.join("trader", "plan.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_entry_to_plan_end_to_end(self):
        self.write(os.path.join("trader", "entry.json"), REEL_ENTRY)
        code, text, err = self.plan("--stop", "or-mid", "--target", "1R", "--write")
        self.assertEqual(code, 0, err)
        p = self.read_plan()
        for k, v in REEL_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["created_at"], "2026-10-26T04:31:00Z")
        self.assertNotIn("target_tick", p)
        self.assertIn("Exit plan: RELIANCE (NSE) long, Mon 2026-10-26, opening-range breakout", text)
        self.assertIn("Entry         ₹1,193.60 at 10:00 AM IST (bar 8)", text)
        self.assertIn("Stop          ₹1,183.60  opening-range midpoint", text)
        self.assertIn("rounded down to the ₹0.10 tick", text)
        self.assertIn("Target        ₹1,203.60  1R (one times the risk)", text)
        self.assertIn("Risk a share  ₹10 (0.84% of the entry)", text)
        self.assertIn("Reward:risk   1.0", text)
        self.assertIn("Flat by       15:00 IST", text)
        self.assertIn("not a guarantee", text)
        self.assertIn("/india-timemachine", text)
        self.assertNotIn("Note:", text)
        self.assertNotIn("pro" + "fit", text.lower())   # never on screen
        code, text, _ = self.plan("--write")                       # the defaults: or-mid, 2R
        self.assertEqual(self.read_plan()["target"], 1213.6)
        self.assertEqual(self.read_plan()["target_r"], 2.0)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-26 10:05"])
        self.assertEqual(code, 0)
        self.assertIn("₹1,213.60", text)
        self.assertNotIn("not today", text)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-27 10:05"])
        self.assertIn("not today", text)

    def test_manual_flags_and_json(self):
        code, text, _ = self.plan("--symbol", "reliance.ns", "--entry", "1193.60", "--or-high", "1192",
                                  "--or-low", "1175.30", "--time", "10:00", "--entry-bar", "8", "--date", "2026-10-26",
                                  "--target", "1R", "--json")
        self.assertEqual(code, 0)
        p = json.loads(text)
        for k, v in REEL_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["warnings"], [])

    def test_a_config_tick_override(self):
        self.write(os.path.join("trader", "entry.json"), REEL_ENTRY)
        self.write(os.path.join("trader", "config.json"), {"tick_size": {"reliance": 0.05}})
        code, text, err = self.plan("--target", "1R", "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["stop"], p["risk_per_share"], p["target"], p["tick"]), (1183.65, 9.95, 1203.55, 0.05))
        self.write(os.path.join("trader", "config.json"), {"tick_size": {"RELIANCE": 0.003}})
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("whole paise", err)

    def test_atr_stop_reads_bars(self):
        self.write(os.path.join("trader", "entry.json"), REEL_ENTRY)
        code, text, err = self.plan("--stop", "atr:1.5", "--offline", BARS, "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["stop"], p["risk_per_share"], p["target"]), (1184.1, 9.5, 1212.6))
        self.assertEqual((p["stop_rule"], p["atr_bars"]), ("atr", 9))
        self.assertAlmostEqual(p["atr"], 6.3222, places=4)
        code, _, err = self.plan("--stop", "atr:1.5", "--offline", BARS, "--entry-bar", "1")
        self.assertEqual(code, 1)
        self.assertIn("at least 3 closed", err)
        folder = os.path.join(self.d, "charts")
        os.mkdir(folder)
        shutil.copy(BARS, os.path.join(folder, "RELIANCE.json"))
        code, text, err = self.plan("--stop", "atr:1.5", "--offline", folder, "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(text)["stop"], 1184.1)

    def test_refusals_from_the_cli(self):
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, status="waiting", time=None, price=None,
                                                              bar_index=None, bar_start=None))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("RELIANCE is waiting, not triggered", err)
        code, text, _ = self.plan("--entry", "1192.10", "--json")   # plan ahead just over the level
        self.assertEqual(code, 0)
        p = json.loads(text)
        self.assertEqual((p["entry"], p["stop"], p["entry_time"]), (1192.1, 1183.6, None))

        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, setup="level", level=1190.0))
        code, _, err = self.plan()
        self.assertIn("level setup has no opening-range stop", err)
        self.assertEqual(self.plan("--stop", "pct:1")[0], 0)

        code, _, err = self.plan("--side", "sell")
        self.assertIn("plans longs only", err)
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, side="sell"))
        code, _, err = self.plan()
        self.assertIn("longs only", err)
        code, _, err = self.plan("--stop", "price:1193.50")
        self.assertIn("longs only", err)
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, symbol="RELIANCE INDUSTRIES"))
        code, _, err = self.plan()
        self.assertIn("not an NSE trading symbol", err)
        os.remove(os.path.join("trader", "entry.json"))
        code, _, err = self.plan()
        self.assertIn("no entry", err)
        self.assertIn("/india-entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE")
        self.assertIn("needs --entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--from", "x.json", "--entry", "1")
        self.assertIn("not both", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--entry", "1193.60", "--or-high", "1192")
        self.assertIn("both --or-high and --or-low", err)
        self.assertNotIn("Traceback", err)

    def test_flat_by_config_square_off_and_late_entries(self):
        self.write(os.path.join("trader", "entry.json"), REEL_ENTRY)
        self.write(os.path.join("trader", "config.json"), {"flat_by": "14:45", "min_reward_risk": 1.5})
        code, text, _ = self.plan("--target", "1R", "--json")
        p = json.loads(text)
        self.assertEqual(p["flat_by"], "14:45")
        self.assertEqual(len(p["warnings"]), 1)
        self.assertIn("under your min_reward_risk 1.5", p["warnings"][0])
        os.remove(os.path.join("trader", "config.json"))

        # after Zerodha's 15:12 square-off of F&O stocks: allowed, with the reason to get out first
        code, text, _ = self.plan("--flat-by", "15:12")
        self.assertEqual(code, 0)
        self.assertIn("Zerodha's 15:12 MIS auto square-off for stocks with F&O contracts", text)
        self.assertIn("closing auction at 15:15, where stop-loss orders are not allowed", text)
        self.assertIn("₹50 + GST an order", text)
        code, text, _ = self.plan("--flat-by", "15:25")
        self.assertIn("auto square-off for every stock", text)
        code, text, _ = self.plan("--flat-by", "15:11")
        self.assertNotIn("square-off", text)

        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, date="2026-11-08", time="2:45 PM",
                                                              bar_index=None))
        code, text, _ = run(exitp, ["plan", "--now", "2026-11-08 14:46"])
        self.assertEqual(code, 0)
        self.assertIn("special session (Muhurat trading (Diwali), 14:20 to 15:30 IST)", text)
        code, _, err = run(exitp, ["plan", "--now", "2026-11-08 14:46", "--flat-by", "14:00"])
        self.assertEqual(code, 1)
        self.assertIn("before the 2:20 PM open", err)

        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, date="2026-10-20"))
        code, text, _ = run(exitp, ["plan", "--now", "2026-10-20 10:01"])
        self.assertIn("NSE is closed on 2026-10-20 (Dussehra)", text)

        self.write(os.path.join("trader", "entry.json"), REEL_ENTRY)
        code, _, err = self.plan("--flat-by", "15:30")
        self.assertIn("after the 3:30 PM close", err)
        self.write(os.path.join("trader", "config.json"), {"flat_by": "16:00"})
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("flat_by 16:00 in the config is at or after the 3:30 PM close", err)
        os.remove(os.path.join("trader", "config.json"))
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, time="3:05 PM"))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("no time left to hold it", err)

    def test_show_without_a_plan(self):
        code, text, _ = run(exitp, ["show"])
        self.assertEqual(code, 1)
        self.assertIn("No plan", text)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "india-exit", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: india-exit\n"))
        self.assertGreater(len(text.splitlines()), 60)
        low = " ".join(text.lower().split())
        for must in ("before the entry", "a stop is a plan, not a guarantee", "/india-timemachine", "/india-budget",
                     "longs only", "₹1,183.60", "₹1,203.60", "₹10", "1,213.60",
                     "too tight to survive the spread", "15:12", "15:25", "closing auction", "₹50 + gst",
                     "reviewed monthly", "not financial advice", "71%", "yes", "python3 on macos/linux"):
            self.assertIn(must, low)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit", "trader-", "new york",
                      "early close"):
            self.assertNotIn(never, low)


if __name__ == "__main__":
    unittest.main()
