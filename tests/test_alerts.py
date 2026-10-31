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

from _load import load, ROOT, FIX

alerts = load("india-alerts", "alerts")

# Quote files: Yahoo chart JSON for the .NS symbols (and RELIANCE.BO), fetched for real on 2026-10-31
# (Saturday), so each holds Friday 30 Oct's close. The reel-HHMM folders hold RELIANCE as it stood at
# HH:MM IST on Monday 26 Oct 2026, rebuilt from the real 5-minute bars (see the "_note" in each file).
QDIR = os.path.join(FIX, "india-alerts")
REEL = {t: os.path.join(QDIR, "reel-" + t) for t in ("1005", "1330", "1340")}
PLAN = os.path.join(QDIR, "plan-reliance.json")
DT = datetime.datetime
UA = "Mozilla/5.0 (compatible; india-trader-skill/1.0)"

# NSE trading holidays 2026, circular NSE/CMTR/71775 (weekday closures only)
NSE_2026 = ["2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03",
            "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02",
            "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25"]


def ist(text):
    return alerts.parse_now(text)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = alerts.main(argv)
    return code, out.getvalue(), err.getvalue()


def plan_rules():
    with open(PLAN, encoding="utf-8") as fh:
        return alerts.rules_from_plan(json.load(fh))


class Money(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(alerts.money(100000), "₹1,00,000")
        self.assertEqual(alerts.money(1234567.5), "₹12,34,567.50")
        self.assertEqual(alerts.money(46.88), "₹46.88")
        self.assertEqual(alerts.money(1193.6), "₹1,193.60")
        self.assertEqual(alerts.money(1200), "₹1,200")
        self.assertEqual(alerts.money(-500), "-₹500")
        self.assertEqual(alerts.money(999), "₹999")

    def test_symbols(self):
        self.assertEqual(alerts.norm_symbol("reliance.ns"), "RELIANCE")
        self.assertEqual(alerts.norm_symbol("M&M"), "M&M")
        self.assertEqual(alerts.norm_symbol("bajaj-auto"), "BAJAJ-AUTO")
        self.assertEqual(alerts.norm_symbol("RELIANCE.BO"), "RELIANCE.BO")
        for bad in ("", "BRK.B", "-X", "A" * 21, "TSLA US"):
            with self.assertRaises(alerts.AlertError):
                alerts.norm_symbol(bad)


class Calendar(unittest.TestCase):
    def test_holidays_match_the_circular(self):
        self.assertEqual(sorted(alerts.NSE_HOLIDAYS), NSE_2026)
        for d in alerts.NSE_HOLIDAYS:
            self.assertLess(datetime.date.fromisoformat(d).weekday(), 5, d)
        self.assertEqual(alerts.CALENDAR_YEARS, (2026,))
        self.assertEqual(alerts.SPECIAL_SESSIONS["2026-11-08"]["open"], "14:20")

    def test_ist_is_a_fixed_offset(self):
        self.assertEqual(alerts.to_ist(DT(2026, 10, 26, 3, 45)), DT(2026, 10, 26, 9, 15))
        self.assertEqual(alerts.to_ist(DT(2026, 1, 15, 3, 45)), DT(2026, 1, 15, 9, 15))   # no daylight saving
        self.assertEqual(alerts.ist_to_utc(DT(2026, 10, 26, 15, 0)), DT(2026, 10, 26, 9, 30))

    def test_parse_now(self):
        self.assertEqual(ist("2026-10-26 10:00"), DT(2026, 10, 26, 4, 30))
        self.assertEqual(ist("2026-10-26T09:15"), DT(2026, 10, 26, 3, 45))
        self.assertEqual(ist("2026-10-26 10:00 IST"), DT(2026, 10, 26, 4, 30))
        self.assertEqual(ist("2026-10-26T04:30:00Z"), DT(2026, 10, 26, 4, 30))
        self.assertEqual(ist("2026-10-26T10:00:00+05:30"), DT(2026, 10, 26, 4, 30))
        today = ist("10:00")
        self.assertEqual(alerts.to_ist(today).strftime("%H:%M"), "10:00")
        with self.assertRaises(alerts.AlertError) as cm:
            ist("tomorrow")
        self.assertIn("an India time like \"2026-10-26 10:00\"", str(cm.exception))

    def test_market_status(self):
        st = alerts.market_status(ist("2026-10-26 10:00"))
        self.assertTrue(st["open"])
        self.assertEqual((st["opens"], st["closes"], st["reason"]), ("09:15", "15:30", "regular session"))
        pre = alerts.market_status(ist("2026-10-26 09:05"))
        self.assertFalse(pre["open"])
        self.assertIn("pre-open", pre["reason"])
        self.assertEqual(pre["next_open"], "2026-10-26 09:15")
        self.assertEqual(alerts.market_status(ist("2026-10-26 08:30"))["reason"], "before the open")
        self.assertTrue(alerts.market_status(ist("2026-10-26 09:15"))["open"])
        cas = alerts.market_status(ist("2026-10-26 15:20"))
        self.assertTrue(cas["open"])
        self.assertIn("closing auction", cas["reason"])
        after = alerts.market_status(ist("2026-10-26 15:30"))
        self.assertFalse(after["open"])
        self.assertEqual(after["next_open"], "2026-10-27 09:15")
        dussehra = alerts.market_status(ist("2026-10-20 11:00"))
        self.assertFalse(dussehra["open"])
        self.assertIn("Dussehra", dussehra["reason"])
        self.assertEqual(dussehra["next_open"], "2026-10-21 09:15")
        self.assertEqual(alerts.market_status(ist("2026-10-31 10:00"))["reason"], "weekend")
        self.assertEqual(alerts.market_status(ist("2026-10-31 10:00"))["next_open"], "2026-11-02 09:15")
        self.assertEqual(alerts.market_status(ist("2026-10-26 10:00"))["calendar_notes"], [])

    def test_the_muhurat_special_session(self):
        sat = alerts.market_status(ist("2026-11-07 10:00"))
        self.assertEqual((sat["next_open"], sat["next_open_special"]), ("2026-11-08 14:20", "Muhurat trading (Diwali)"))
        self.assertFalse(alerts.market_status(ist("2026-11-08 14:19"))["open"])
        sp = alerts.market_status(ist("2026-11-08 14:20"))
        self.assertTrue(sp["open"])
        self.assertEqual(sp["special"], "Muhurat trading (Diwali)")
        self.assertEqual(sp["reason"], "special session: Muhurat trading (Diwali)")
        after = alerts.market_status(ist("2026-11-08 15:30"))
        self.assertFalse(after["open"])
        self.assertEqual(after["next_open"], "2026-11-09 09:15")
        self.assertEqual(alerts.market_status(ist("2026-11-09 16:00"))["next_open"], "2026-11-11 09:15")  # Nov 10 holiday

    def test_2027_is_not_published(self):
        st = alerts.market_status(ist("2027-01-26 10:00"))     # a weekday: counted as trading, with the warning
        self.assertTrue(st["open"])
        self.assertFalse(st["calendar_known"])
        self.assertEqual(st["calendar_notes"], ["NSE has not published its 2027 holiday list in this tool yet: "
                                                "check nseindia.com before trading"])
        eve = alerts.market_status(ist("2026-12-31 16:00"))
        self.assertTrue(eve["calendar_known"])
        self.assertEqual(eve["next_open"], "2027-01-01 09:15")
        self.assertEqual(len(eve["calendar_notes"]), 1)


class Quotes(unittest.TestCase):
    def test_yahoo_fixtures(self):
        with open(os.path.join(QDIR, "yahoo-RELIANCE.json"), encoding="utf-8") as fh:
            q = alerts.parse_yahoo(fh.read(), "RELIANCE")
        self.assertEqual((q["price"], q["prev_close"], q["currency"], q["exchange"]), (1164.9, 1167.4, "INR", "NSE"))
        self.assertEqual(q["time"], "2026-10-30T09:58:17Z")                       # 15:28 IST
        with open(os.path.join(REEL["1005"], "yahoo-RELIANCE.json"), encoding="utf-8") as fh:
            q = alerts.parse_yahoo(fh.read(), "RELIANCE")
        self.assertEqual((q["price"], q["prev_close"], round(q["open"], 2)), (1192.5, 1190.0, 1186.1))
        self.assertEqual(q["time"], "2026-10-26T04:35:00Z")

    def test_bse_fixture(self):
        with open(os.path.join(QDIR, "bse-RELIANCE.json"), encoding="utf-8") as fh:
            q = alerts.parse_yahoo(fh.read(), "RELIANCE", "BSE")
        self.assertEqual((q["price"], q["exchange"], q["source"]), (1163.55, "BSE", "yahoo-bse"))

    def test_urls_and_user_agent(self):
        self.assertEqual(alerts.UA, UA)
        self.assertEqual(alerts.yahoo_url("reliance"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=1d")
        self.assertEqual(alerts.yahoo_url("M&M"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/M%26M.NS?range=1d&interval=1d")
        self.assertIn("/chart/RELIANCE.BO?", alerts.yahoo_url("RELIANCE", exchange="BSE"))
        self.assertIn("//query1.finance.yahoo.com/", alerts.yahoo_url("RELIANCE", alerts.YAHOO_HOSTS[1]))

    def test_offline_quotes_and_errors(self):
        q, errors = alerts.get_quotes(["RELIANCE", "infy.ns", "M&M", "ZZZZ", "NOPE"], "yahoo", QDIR)
        self.assertEqual(sorted(q), ["INFY", "M&M", "RELIANCE"])
        self.assertEqual(q["INFY"]["price"], 1185.0)
        self.assertEqual(q["M&M"]["price"], 2840.3)
        self.assertEqual(len(errors), 2)
        self.assertIn("No data found", errors[0])                                   # Yahoo's own 404 answer
        self.assertIn("BSE fallback", errors[1])

    def test_offline_bse_source_and_suffix(self):
        q, errors = alerts.get_quotes(["RELIANCE"], "bse", QDIR)
        self.assertEqual((q["RELIANCE"]["price"], q["RELIANCE"]["exchange"], errors), (1163.55, "BSE", []))
        q, errors = alerts.get_quotes(["RELIANCE.BO"], "yahoo", QDIR)
        self.assertEqual(sorted(q), ["RELIANCE.BO"])

    def test_offline_gz(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(QDIR, "yahoo-RELIANCE.json"), "rb") as src:
                with gzip.open(os.path.join(d, "yahoo-RELIANCE.json.gz"), "wb") as dst:
                    dst.write(src.read())
            q, errors = alerts.get_quotes(["RELIANCE"], "yahoo", d)
            self.assertEqual((q["RELIANCE"]["price"], errors), (1164.9, []))
        finally:
            shutil.rmtree(d)


class FakeNet(object):
    """Stands in for urllib: answers each request with the next status code."""

    def __init__(self, codes, body=None):
        self.codes, self.calls, self.sleeps = list(codes), [], []
        with open(os.path.join(QDIR, "yahoo-RELIANCE.json"), encoding="utf-8") as fh:
            self.body = body or fh.read()
        with open(os.path.join(QDIR, "bse-RELIANCE.json"), encoding="utf-8") as fh:
            self.bse = fh.read()

    def urlopen(self, req, timeout=None):
        self.calls.append((req.full_url.split("/")[2], req.full_url.split("/")[6].split("?")[0],
                           req.get_header("User-agent")))
        code = self.codes.pop(0)
        if code != 200:
            fp = io.BytesIO(b'{"chart":{"result":null,"error":{"code":"Not Found",'
                            b'"description":"No data found, symbol may be delisted"}}}' if code == 404 else b"")
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, fp)
        body = self.bse if ".BO" in req.full_url else self.body
        return io.BytesIO(body.encode("utf-8"))


class Network(unittest.TestCase):
    def setUp(self):
        self.saved = (alerts._urlopen, alerts._sleep)

    def tearDown(self):
        alerts._urlopen, alerts._sleep = self.saved

    def net(self, codes):
        f = FakeNet(codes)
        alerts._urlopen = f.urlopen
        alerts._sleep = f.sleeps.append
        return f

    def hosts(self, f):
        return [h.split(".")[0] for h, _, _ in f.calls]

    def test_query2_first_with_the_contract_user_agent(self):
        f = self.net([200])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertEqual((q["RELIANCE"]["price"], errors), (1164.9, []))
        self.assertEqual(f.calls, [("query2.finance.yahoo.com", "RELIANCE.NS", UA)])

    def test_m_and_m_is_url_quoted(self):
        f = self.net([200])
        alerts.get_quotes(["M&M"])
        self.assertEqual(f.calls[0][1], "M%26M.NS")

    def test_one_retry_with_a_pause_on_429(self):
        f = self.net([429, 200])
        self.assertEqual(alerts.get_quotes(["RELIANCE"])[0]["RELIANCE"]["price"], 1164.9)
        self.assertEqual(self.hosts(f), ["query2", "query2"])
        self.assertEqual(f.sleeps, [alerts.BACKOFF])

    def test_falls_back_to_query1_on_429_and_5xx(self):
        f = self.net([429, 429, 200])
        self.assertIn("RELIANCE", alerts.get_quotes(["RELIANCE"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query2", "query1"])
        f = self.net([503, 200])
        self.assertIn("RELIANCE", alerts.get_quotes(["RELIANCE"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query1"])

    def test_then_falls_back_to_the_bse_listing_once(self):
        f = self.net([500, 502, 200])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertEqual(errors, [])
        self.assertEqual((q["RELIANCE"]["price"], q["RELIANCE"]["source"], q["RELIANCE"]["exchange"]),
                         (1163.55, "yahoo-bse", "BSE"))
        self.assertEqual([c[1] for c in f.calls], ["RELIANCE.NS", "RELIANCE.NS", "RELIANCE.BO"])
        f = self.net([500, 502, 500, 502])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertEqual(q, {})
        self.assertIn("query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS answered HTTP 502", errors[0])
        self.assertIn("BSE fallback: https://query1.finance.yahoo.com/v8/finance/chart/RELIANCE.BO answered HTTP 502",
                      errors[0])
        self.assertEqual(len(f.calls), 4)                                            # never loops

    def test_a_404_is_yahoos_answer_and_a_403_is_not_retried(self):
        f = self.net([404])
        q, errors = alerts.get_quotes(["NOPE"])
        self.assertIn("No data found", errors[0])
        self.assertEqual(self.hosts(f), ["query2"])                                  # no BSE try for an unknown symbol
        f = self.net([403, 403])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertIn("HTTP 403", errors[0])
        self.assertEqual([c[1] for c in f.calls], ["RELIANCE.NS", "RELIANCE.BO"])

    def test_a_pause_between_symbols(self):
        f = self.net([200, 200])
        alerts.get_quotes(["RELIANCE", "INFY"])
        self.assertEqual(f.sleeps, [alerts.PAUSE])


class Rules(unittest.TestCase):
    def setUp(self):
        self.q, _ = alerts.get_quotes(["RELIANCE", "INFY", "SBIN", "HDFCBANK"], "yahoo", QDIR)
        self.now = ist("2026-10-30 15:29")
        self.open = alerts.market_status(self.now)

    def at(self, text, rules, state=None, quotes=None):
        now = ist(text)
        return alerts.evaluate(rules, self.q if quotes is None else quotes, alerts.market_status(now),
                               state or {}, now)

    def reel(self, folder):
        return alerts.get_quotes(["RELIANCE"], "yahoo", REEL[folder])[0]

    def test_an_above_level_rings_once(self):
        rules = [{"id": "infy-1180", "symbol": "INFY", "type": "above", "price": 1180}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertEqual(len(rings), 1)
        self.assertIn("INFY is ₹1,185: at or above your alert level ₹1,180", rings[0]["message"])
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])

    def test_below_and_rearm(self):
        rules = [{"id": "sbin-965", "symbol": "SBIN", "type": "below", "price": 965, "rearm": True}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertIn("SBIN is ₹961.85: at or below your alert level ₹965", rings[0]["message"])
        self.assertNotIn("SL order", rings[0]["message"])                           # a level, not a stop
        self.q["SBIN"]["price"] = 966.0
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])
        self.assertNotIn("sbin-965", st["rung"])
        self.q["SBIN"]["price"] = 964.5
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(len(rings), 1)

    def test_pct_move_from_previous_close(self):
        big = [{"id": "sbin-3", "symbol": "SBIN", "type": "pct_move", "pct": 3}]
        self.assertEqual(alerts.evaluate(big, self.q, self.open, {}, self.now)[0], [])
        small = [{"id": "sbin-1", "symbol": "SBIN", "type": "pct_move", "pct": 1, "direction": "down"}]
        rings, st = alerts.evaluate(small, self.q, self.open, {}, self.now)
        self.assertIn("SBIN moved -1.03% to ₹961.85 (from ₹971.90)", rings[0]["message"])
        self.assertEqual(alerts.evaluate(small, self.q, self.open, st, self.now)[0], [])
        up = [{"id": "sbin-up", "symbol": "SBIN", "type": "pct_move", "pct": 1, "direction": "up"}]
        self.assertEqual(alerts.evaluate(up, self.q, self.open, {}, self.now)[0], [])
        infy = [{"id": "infy-3", "symbol": "INFY", "type": "pct_move", "pct": 3}]
        rings, _ = alerts.evaluate(infy, self.q, self.open, {}, self.now)
        self.assertIn("INFY moved +3.36% to ₹1,185 (from ₹1,146.50)", rings[0]["message"])

    def test_opening_bell_once_per_trading_day(self):
        rules = [{"id": "bell", "type": "opening_bell"}]
        self.assertEqual(self.at("2026-10-26 09:10", rules, quotes={})[0], [])     # pre-open: not yet
        rings, st = self.at("2026-10-26 09:15", rules, quotes={})
        self.assertIn("Opening bell: NSE is open (2026-10-26 09:15 IST), continuous trading ends at 15:30 IST "
                      "(15:15 for F&O stocks", rings[0]["message"])
        self.assertEqual(self.at("2026-10-26 11:00", rules, st, {})[0], [])
        self.assertEqual(len(self.at("2026-10-27 09:16", rules, st, {})[0]), 1)
        self.assertEqual(self.at("2026-10-20 10:00", rules, quotes={})[0], [])     # Dussehra
        rings, _ = self.at("2026-11-08 14:21", rules, st, {})
        self.assertIn("special session, Muhurat trading (Diwali), is open", rings[0]["message"])

    def test_the_plan_gives_a_stop_a_target_and_flat_by(self):
        rules = plan_rules()
        self.assertEqual([(r["id"], r["type"]) for r in rules],
                         [("reliance-plan-stop", "stop"), ("reliance-plan-target", "target"),
                          ("reliance-flat-by", "time")])
        self.assertEqual((rules[0]["price"], rules[1]["price"]), (1183.6, 1203.6))
        self.assertEqual((rules[2]["at"], rules[2]["label"]), ("15:00", "flat before square-off"))
        self.assertTrue(all(r["date"] == "2026-10-26" for r in rules))
        rings, st = self.at("2026-10-26 10:05", rules, quotes=self.reel("1005"))   # 1192.50, inside the bracket
        self.assertEqual(rings, [])
        rings, st = self.at("2026-10-26 13:30", rules, st, self.reel("1330"))      # 1202.50: the 13:25 bar touched
        self.assertEqual(rings, [])                                                # 1204 between two checks
        rings, st = self.at("2026-10-26 13:40", rules, st, self.reel("1340"))      # 1204.40
        self.assertEqual([r["id"] for r in rings], ["reliance-plan-target"])
        msg = rings[0]["message"]
        self.assertIn("RELIANCE is ₹1,204.40: at or above your target ₹1,203.60 "
                      "(plan target: 1R (one times the risk)).", msg)
        self.assertIn("The target is not an order at the broker: to take it, india-broker flatten RELIANCE "
                      "(cancel the stop order, then sell)", msg)
        self.assertEqual(self.at("2026-10-26 13:45", rules, st, self.reel("1340"))[0], [])   # once

    def test_a_stop_says_check_the_sl_order(self):
        rules = plan_rules()[:1]
        q = json.loads(json.dumps(self.reel("1005")))
        q["RELIANCE"]["price"] = 1183.5          # synthetic: the reel never fell this far
        rings, _ = self.at("2026-10-26 11:30", rules, quotes=q)
        msg = rings[0]["message"]
        self.assertIn("RELIANCE is ₹1,183.50: at or below your stop ₹1,183.60 "
                      "(plan stop: opening-range midpoint).", msg)
        self.assertIn("The SL order at Zerodha should have triggered: check get_orders", msg)
        self.assertIn("An SL-limit can stay unfilled in a fast fall or a gap", msg)

    def test_flat_by_rings_once_per_trading_day(self):
        rules = plan_rules()[2:]
        self.assertEqual(self.at("2026-10-26 14:59", rules)[0], [])
        rings, st = self.at("2026-10-26 15:00", rules)
        self.assertEqual(len(rings), 1)
        msg = rings[0]["message"]
        self.assertIn("Flat before square-off: it is 15:00 IST (set for 15:00), continuous trading ends at "
                      "15:30 IST (15:15 for F&O stocks, which go into the closing auction).", msg)
        self.assertIn("india-broker flatten RELIANCE builds the sell ticket (cancel the open stop order first)", msg)
        self.assertIn("15:12 for F&O stocks and 15:25 for the others", msg)
        self.assertIn("₹50 + GST", msg)
        self.assertEqual(self.at("2026-10-26 15:10", rules, st)[0], [])
        rings, _ = self.at("2026-10-26 15:40", rules)
        self.assertIn("the market has closed", rings[0]["message"])
        self.assertEqual(self.at("2026-10-27 15:01", rules, st)[0], [])     # plan rules: their day only

    def test_a_time_alert_on_weekends_holidays_and_special_sessions(self):
        rule = [{"id": "t", "type": "time", "at": "15:00", "label": "check"}]
        self.assertEqual(self.at("2026-10-31 15:01", rule)[0], [])          # Saturday: no session
        self.assertEqual(self.at("2026-11-10 15:01", rule)[0], [])          # Diwali-Balipratipada
        rings, st = self.at("2026-11-08 15:00", rule)                       # Muhurat, a Sunday
        self.assertIn("the special session ends at 15:30 IST", rings[0]["message"])
        self.assertEqual(st["rung"]["t"], "2026-11-08T09:30:00Z")

    def test_the_example_rules_are_valid(self):
        with open(os.path.join(ROOT, "india-alerts", "alerts.example.json"), encoding="utf-8") as fh:
            rules = json.load(fh)["rules"]
        for r in rules:
            alerts.validate(r)
        self.assertEqual([r for r in rules if r["id"].startswith("reliance-plan") or r["id"] == "reliance-flat-by"],
                         plan_rules())

    def test_bad_rules(self):
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "moon"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "above", "symbol": "INFY"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "time", "at": "3pm"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "above", "symbol": "BRK.B", "price": 1}], {}, self.open, {},
                            self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan({"symbol": "RELIANCE", "stop": 1183.6})

    def test_flat_by_from_the_config_then_the_default(self):
        plan = {"symbol": "RELIANCE", "stop": 1183.6, "target": 1203.6}
        self.assertEqual(alerts.rules_from_plan(plan, "14:45")[2]["at"], "14:45")
        self.assertEqual(alerts.rules_from_plan(plan)[2]["at"], "15:00")


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rules = os.path.join(self.d, "alerts.json")
        self.state = os.path.join(self.d, "state", "alerts.json")
        self.prices = os.path.join(self.d, "prices.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def check(self, now, folder=QDIR):
        return run(["check", "--rules", self.rules, "--state", self.state, "--prices-out", self.prices,
                    "--offline", folder, "--now", now])

    def test_the_reel_trade_from_the_plan(self):
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state,
                             "--config", os.path.join(self.d, "none")])
        self.assertEqual(code, 0)
        self.assertIn("Added reliance-flat-by", text)
        self.assertIn("the target is only watched here", text)
        self.assertEqual(run(["add", "--rules", self.rules, "--opening-bell"])[0], 0)

        code, text, _ = self.check("2026-10-26 10:05", REEL["1005"])
        self.assertEqual(code, 0)                                      # the opening bell rings first
        self.assertEqual(text.count("ALERT "), 1)
        code, text, _ = self.check("2026-10-26 10:06", REEL["1005"])
        self.assertEqual(code, 1)
        self.assertIn("NSE OPEN (2026-10-26 10:06 IST, regular session).", text)
        self.assertIn("Nothing hit. 4 rule(s) watched today, RELIANCE ₹1,192.50.", text)
        with open(self.prices, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"RELIANCE": 1192.5})
        self.assertEqual(doc["currency"], "INR")
        self.assertEqual(doc["detail"]["RELIANCE"]["time"], "2026-10-26T04:35:00Z")
        self.assertEqual(doc["detail"]["RELIANCE"]["fetched"], "2026-10-26T04:36:00Z")
        self.assertEqual(doc["detail"]["RELIANCE"]["exchange"], "NSE")

        self.assertEqual(self.check("2026-10-26 13:30", REEL["1330"])[0], 1)
        code, text, _ = self.check("2026-10-26 13:40", REEL["1340"])
        self.assertEqual(code, 0)
        self.assertIn("ALERT RELIANCE is ₹1,204.40: at or above your target", text)
        self.assertIn("not an order", text)
        self.assertIn("the target is watched here, not placed", text)

        code, text, _ = self.check("2026-10-26 15:00", REEL["1340"])
        self.assertEqual(code, 0)
        self.assertIn("ALERT Flat before square-off", text)
        self.assertEqual(self.check("2026-10-26 15:02", REEL["1340"])[0], 1)
        code, text, _ = self.check("2026-10-27 09:20")
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-10-27 09:25")
        self.assertIn("Nothing hit. 1 rule(s) watched today.", text)

        # a new plan replaces the plan rules and re-arms them
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state])
        with open(self.rules, encoding="utf-8") as fh:
            ids = [r["id"] for r in json.load(fh)["rules"]]
        self.assertEqual(sorted(ids), ["opening-bell", "reliance-flat-by", "reliance-plan-stop",
                                       "reliance-plan-target"])
        with open(self.state, encoding="utf-8") as fh:
            self.assertNotIn("reliance-flat-by", json.load(fh)["rung"])

    def test_the_portfolio_evening_and_morning(self):
        for args in (["--opening-bell"], ["--symbol", "INFY", "--above", "1200"], ["--symbol", "SBIN", "--stop", "940"]):
            code, _, _ = run(["add", "--rules", self.rules] + args)
            self.assertEqual(code, 0)
        code, _, err = run(["add", "--rules", self.rules, "--symbol", "INFY", "--above", "1200"])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

        code, text, _ = self.check("2026-10-30 16:00")
        self.assertEqual(code, 1)
        self.assertIn("NSE closed (2026-10-30 16:00 IST, after the close).", text)
        self.assertIn("Nothing hit", text)
        with open(self.prices, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"INFY": 1185.0, "SBIN": 961.85})

        code, text, _ = self.check("2026-11-02 09:16")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-11-02 09:45")
        self.assertEqual(code, 1)

        code, text, _ = run(["list", "--rules", self.rules, "--state", self.state])
        self.assertIn("rang 2026-11-02 09:16 IST", text)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 0)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 1)

    def test_add_time_and_target(self):
        code, text, _ = run(["add", "--rules", self.rules, "--at", "15:00", "--label", "flat before square-off",
                             "--symbol", "reliance.ns"])
        self.assertEqual(code, 0)
        code, text, _ = run(["add", "--rules", self.rules, "--symbol", "RELIANCE", "--target", "1203.6",
                             "--date", "2026-10-26"])
        self.assertEqual(code, 0)
        with open(self.rules, encoding="utf-8") as fh:
            rules = json.load(fh)["rules"]
        self.assertEqual(rules[0], {"id": "time-1500", "type": "time", "at": "15:00",
                                    "label": "flat before square-off", "symbol": "RELIANCE"})
        self.assertEqual(rules[1], {"id": "reliance-target-1203.6", "symbol": "RELIANCE", "type": "target",
                                    "price": 1203.6, "date": "2026-10-26"})
        self.assertEqual(run(["add", "--rules", self.rules, "--at", "3pm"])[0], 1)
        self.assertEqual(run(["add", "--rules", self.rules, "--symbol", "BRK.B", "--above", "1"])[0], 1)
        code, _, err = run(["add", "--rules", self.rules, "--from-plan", "--plan", os.path.join(self.d, "none")])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)

    def test_quote_write_merges(self):
        code, text, _ = run(["quote", "RELIANCE", "INFY", "--offline", QDIR, "--write", self.prices,
                             "--now", "2026-10-30T09:58:17Z"])
        self.assertEqual(code, 0)
        self.assertIn("RELIANCE   ₹1,164.90 (-0.21% vs prev close ₹1,167.40)  2026-10-30 15:28 IST  [yahoo]",
                      text)
        self.assertIn("delayed", text)
        run(["quote", "HDFCBANK", "--offline", QDIR, "--write", self.prices])
        with open(self.prices, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(doc["prices"]), ["HDFCBANK", "INFY", "RELIANCE"])
        self.assertEqual(doc["detail"]["INFY"]["prev_close"], 1146.5)
        self.assertEqual(doc["detail"]["INFY"]["fetched"], "2026-10-30T09:58:17Z")
        self.assertEqual(doc["detail"]["INFY"]["currency"], "INR")

    def test_quote_holdings_from_portfolio_and_plan(self):
        pf = os.path.join(self.d, "portfolio.json")
        with open(pf, "w", encoding="utf-8") as fh:
            json.dump({"cash": 0, "positions": [{"tradingsymbol": "INFY", "quantity": 1}]}, fh)
        code, text, _ = run(["quote", "--holdings", "--portfolio", pf, "--plan", PLAN, "--offline", QDIR, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(json.loads(text)["quotes"]), ["INFY", "RELIANCE"])

    def test_clock_exit_codes(self):
        code, text, _ = run(["clock", "--now", "2026-10-26 10:00"])
        self.assertEqual(code, 0)
        self.assertIn("India 2026-10-26 10:00 IST (Monday): NSE OPEN, regular session.", text)
        self.assertIn("closing auction (15:15 to 15:35)", text)
        self.assertIn("15:12 for F&O stocks", text)
        code, text, _ = run(["clock", "--now", "2026-12-25 10:00"])
        self.assertEqual(code, 1)
        self.assertIn("NSE holiday: Christmas", text)
        self.assertIn("Next open: 2026-12-28 09:15 IST.", text)
        code, text, _ = run(["clock", "--now", "2026-11-07 12:00"])
        self.assertIn("Next open: 2026-11-08 14:20 IST (special session: Muhurat trading (Diwali)).", text)
        code, text, _ = run(["clock", "--now", "2026-11-08 14:30"])
        self.assertEqual(code, 0)
        self.assertIn("allow_special_sessions", text)
        code, text, _ = run(["clock", "--now", "2027-03-01 10:00"])
        self.assertIn("NSE has not published its 2027 holiday list in this tool yet: check nseindia.com "
                      "before trading.", text)

    def test_check_without_rules(self):
        code, _, err = run(["check", "--rules", self.rules, "--offline", QDIR])
        self.assertEqual(code, 1)
        self.assertIn("no rules", err)


if __name__ == "__main__":
    unittest.main()
