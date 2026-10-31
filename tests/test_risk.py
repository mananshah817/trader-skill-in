import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

check = load("india-risk", "check")
ticket = load("india-broker", "ticket")
size = load("india-budget", "size")

CFG_PATH = os.path.join(ROOT, "india-budget", "config.example.json")
REEL_PATH = os.path.join(FIX, "india-risk", "orders-reliance.json")    # india-budget's output for the reel
SPLIT_PATH = os.path.join(FIX, "india-risk", "orders-split.json")      # india-budget weights mode, real prices
NOW = "2026-10-26 10:00"
TODAY = "2026-10-26"
PRICES = {"RELIANCE": 1193.6, "HDFCBANK": 691.9, "ICICIBANK": 1391.9, "INFY": 1140.8, "TCS": 2540.0, "ITC": 200.0}
QUOTE_TIME = datetime.datetime(2026, 10, 26, 4, 28)   # 09:58 IST
# the account checks with every intraday check switched off
ACCT = {"budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 80000, "min_order_inr": 500,
        "max_orders_per_day": 10, "require_stop": False, "no_entries_before": None, "no_entries_after": None,
        "flat_by": None, "max_trades_per_day": 0, "max_daily_loss_inr": 0, "max_loss_per_trade_inr": 0,
        "min_reward_risk": 0}


def at(text):
    return check.parse_now(text)


def cfg(**over):
    with open(CFG_PATH, encoding="utf-8") as fh:
        c = json.load(fh)
    c.update(over)
    return c


def acct(**over):
    c = dict(ACCT)
    c.update(over)
    return c


def reel():
    with open(REEL_PATH, encoding="utf-8") as fh:
        return json.load(fh)["orders"]


def entry(**over):
    o = reel()[0]
    o.update(over)
    return o


def stop(**over):
    o = reel()[1]
    o.update(over)
    return o


def split():
    with open(SPLIT_PATH, encoding="utf-8") as fh:
        return json.load(fh)["orders"]


def group(sym, qty, price, stop_p, stop_limit, target):
    g = "%s-%s-orb" % (sym, TODAY)
    base = {"group": g, "variety": "regular", "exchange": "NSE", "tradingsymbol": sym, "quantity": qty,
            "product": "MIS", "validity": "DAY"}
    e = dict(base, role="entry", transaction_type="BUY", order_type="LIMIT", price=price,
             plan={"entry": price, "stop": stop_p, "target": target})
    s = dict(base, role="stop", after="entry", transaction_type="SELL", order_type="SL", trigger_price=stop_p,
             price=stop_limit)
    return [e, s]


def buy(sym, qty, price=None):
    return {"variety": "regular", "exchange": "NSE", "tradingsymbol": sym, "transaction_type": "BUY",
            "quantity": qty, "product": "MIS", "order_type": "LIMIT", "price": price or PRICES[sym],
            "validity": "DAY"}


def sell(sym, qty, price, role="exit"):
    return {"role": role, "variety": "regular", "exchange": "NSE", "tradingsymbol": sym,
            "transaction_type": "SELL", "quantity": qty, "product": "MIS", "order_type": "LIMIT", "price": price,
            "validity": "DAY"}


def placed(sym, side="BUY", day=TODAY, max_loss=767.69, kind=None, status="placed"):
    r = {"symbol": sym, "side": side, "status": status, "date": day, "at": day + "T10:05:00+05:30"}
    if max_loss is not None:
        r["max_loss"] = max_loss
    if kind:
        r["kind"] = kind
    return r


def verdicts(res):
    return [r["verdict"] for r in res["results"]]


def reasons(res, i=0):
    return " | ".join(res["results"][i]["reasons"])


def warnings(res, i=0):
    return " | ".join(res["results"][i]["warnings"])


PF = {"cash": 25922.4, "positions": [{"ticker": "RELIANCE", "shares": 62,
                                       "lots": [{"date": TODAY, "shares": 62, "cost": 74077.6}]}]}


class TheReel(unittest.TestCase):
    def test_the_reliance_group_passes_every_check(self):
        res = check.check(reel(), cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"], res["results"][0]["reasons"])
        e, s = res["results"]
        self.assertEqual((e["reasons"], e["warnings"], s["reasons"], s["warnings"]), ([], [], [], []))
        self.assertEqual((e["kind"], e["entry_ref"], e["entry_ref_from"], e["stop"], e["target"]),
                         ("entry", 1194.8, "limit", 1183.6, 1203.6))
        self.assertEqual((e["risk_per_share"], e["charges"], e["max_loss"], e["reward_risk"]), (11.2, 73.29, 767.69, 1.0))
        self.assertEqual(e["loss_at_stop_limit"], 1139.58)
        self.assertEqual(e["value_inr"], 74077.6)
        self.assertEqual((s["kind"], s["for_index"], s["stop"]), ("stop", 1, 1183.6))
        self.assertEqual(res["cash_after"], 25922.4)     # the stop leg is contingent: it does not free cash
        self.assertEqual(res["date"], TODAY)
        self.assertIs(res["paper"], True)

    def test_the_max_loss_counts_the_charges(self):
        res = check.check(reel(), cfg(max_loss_per_trade_inr=700), now=at(NOW))
        self.assertEqual(verdicts(res), ["BLOCK", "BLOCK"])
        self.assertEqual(res["results"][0]["reasons"],
                         ["loss: 62 shares x 11.20 risk a share = ₹694.40 plus ₹73.29 charges = ₹767.69 at the stop, "
                          "over your ₹700 max loss per trade"])
        self.assertEqual(reasons(res, 1), "stop: its entry (#1) was blocked, so there is nothing to protect")
        res = check.check(reel(), cfg(max_loss_per_trade_inr=700, include_charges=False), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual(res["results"][0]["max_loss"], 694.4)

    def test_charges_are_the_same_function_as_the_budget(self):
        for b, s_ in ((74077.6, 73383.2), (10000, 9000), (250000, 0)):
            self.assertEqual(check.charges(b, s_), size.charges(b, s_))
        self.assertEqual(check.charges(1000, 1000, exchange="BSE"), size.charges(1000, 1000, exchange="BSE"))
        self.assertEqual(check.money(100000), "₹1,00,000")

    def test_fingerprint_is_shared_with_the_broker(self):
        o = entry()
        self.assertEqual(check.fingerprint(o), ticket.fingerprint(o))
        self.assertEqual(check.fingerprint(dict(o, why="x", plan={}, role="exit", group="g")), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, quantity=63)), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, price=1194.9)), check.fingerprint(o))
        self.assertEqual(check.KITE_FIELDS, ticket.KITE_FIELDS)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "state", "risk.json")
            none = os.path.join(d, "none")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", REEL_PATH, "--config", CFG_PATH, "--portfolio", none,
                                   "--prices", none, "--state", none, "--now", NOW, "--out", out])
            text = buf.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("PASS  #1 BUY RELIANCE 62 shares, entry", text)
            self.assertIn("entry 1194.80 (the limit price), stop 1183.60, target 1203.60: risk 11.20 a share", text)
            self.assertIn("max loss ₹767.69 with ₹73.29 charges (limit ₹1,000), reward:risk 1.00 from 1193.60 "
                          "(0.79 at the limit) (min 1)", text)
            self.assertIn("if the stop fills at its limit 1177.60: ₹1,139.58", text)
            self.assertIn("today before this: 0 of 3 trades, ₹0 of ₹3,000 daily loss", text)
            self.assertIn("PASS  #2 SELL RELIANCE 62 shares, stop for #1", text)
            self.assertIn("SL trigger 1183.60, limit 1177.60: placed only after #1 fills", text)
            self.assertIn("2 passed, 0 blocked at 2026-10-26 10:00 IST. Cash after the passed orders: ₹25,922.40",
                          text)
            self.assertIn("Not financial advice", text)
            with open(out, encoding="utf-8") as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], TODAY)
            self.assertEqual([r["kind"] for r in rec["results"]], ["entry", "stop"])
            self.assertEqual(rec["results"][0]["fingerprint"], check.fingerprint(entry()))


class KiteFormat(unittest.TestCase):
    def one(self, order, **over):
        return check.check([order], cfg(**over), now=at(NOW))

    def test_market_orders_are_blocked(self):
        res = self.one(entry(order_type="MARKET", price=None))
        self.assertIn("MARKET order needs market protection", reasons(res))

    def test_order_type_rules(self):
        self.assertIn("a LIMIT order needs a price", reasons(self.one(entry(price=None))))
        self.assertIn("needs a trigger_price and a price", reasons(check.check(
            [entry(), stop(trigger_price=None)], cfg(), now=at(NOW)), 1))
        self.assertIn("price (1184.00) at or below its trigger_price (1183.60)", reasons(check.check(
            [entry(), stop(price=1184.0)], cfg(), now=at(NOW)), 1))
        res = check.check([entry(), stop(order_type="SL-M", price=None, trigger_price=None)], cfg(), now=at(NOW))
        self.assertIn("an SL-M order needs a trigger_price", reasons(res, 1))
        res = check.check([entry(), stop(order_type="SL-M", price=None)], cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])

    def test_quantity_and_prices(self):
        for q in ("62", 62.5, 0, -1, True):
            self.assertIn("quantity must be a positive whole number", reasons(self.one(entry(quantity=q))), q)
        self.assertIn("price must be a positive JSON number", reasons(self.one(entry(price="1194.80"))))
        self.assertIn("price 1194.83 is not on the 0.10 tick", reasons(self.one(entry(price=1194.83))))
        self.assertIn("Kite's instrument list", reasons(self.one(entry(price=1194.83))))
        res = self.one(entry(price=1195.0), tick_size={"RELIANCE": 1.0})
        self.assertNotIn("tick", reasons(res))
        self.assertIn("not on the 1.00 tick", reasons(self.one(entry(price=1194.8), tick_size={"RELIANCE": 1.0})))

    def test_kite_fields(self):
        self.assertIn("exchange must be NSE or BSE", reasons(self.one(entry(exchange="NYSE"))))
        self.assertIn("no exchange", reasons(self.one(entry(exchange=None))))
        self.assertIn("not RELIANCE.NS", reasons(self.one(entry(tradingsymbol="RELIANCE.NS"))))
        self.assertIn("regular orders only, not amo", reasons(self.one(entry(variety="amo"))))
        self.assertIn("unknown variety", reasons(self.one(entry(variety="bracket"))))
        self.assertIn("product must be MIS", reasons(self.one(entry(product="NRML"))))
        self.assertIn("entries are intraday (MIS) only", reasons(self.one(entry(product="CNC"))))
        self.assertIn("validity must be DAY, IOC or TTL", reasons(self.one(entry(validity="GTC"))))
        self.assertIn("validity_ttl", reasons(self.one(entry(validity="TTL"))))
        self.assertEqual(verdicts(check.check([entry(validity="TTL", validity_ttl=5), stop()], cfg(), now=at(NOW))),
                         ["PASS", "PASS"])
        self.assertIn("tag must be at most 20", reasons(self.one(entry(tag="india-opening-range-breakout"))))
        self.assertIn("transaction_type must be BUY or SELL", reasons(self.one(entry(transaction_type="HOLD"))))
        self.assertIn("transaction_type 'buy' must be written exactly as Kite's constant 'BUY'",
                      reasons(self.one(entry(transaction_type="buy"))))
        self.assertIn("variety 'Regular' must be written exactly", reasons(self.one(entry(variety="Regular"))))
        self.assertIn("no tradingsymbol (\"symbol\" is not a Kite field)",
                      reasons(self.one(entry(tradingsymbol=None, symbol="RELIANCE"))))

    def test_derivatives_are_always_blocked(self):
        for sym, exch in (("RELIANCE26NOVFUT", "NFO"), ("RELIANCE26NOVFUT", "NSE"), ("NIFTY26N0324500CE", "NSE"),
                          ("BANKNIFTY26NOV52000PE", "NFO"), ("SENSEX26N0580000CE", "BFO")):
            res = self.one(entry(tradingsymbol=sym, exchange=exch))
            self.assertIn("is a derivative (F&O)", reasons(res), sym)
        self.assertIn("does not look like an NSE or BSE equity symbol",
                      reasons(self.one(entry(tradingsymbol="NIFTY 50"))))
        self.assertIn("trade-for-trade", reasons(self.one(entry(tradingsymbol="IDEA-BE"))))
        for sym in ("M&M", "BAJAJ-AUTO", "3MINDIA"):
            res = self.one(entry(tradingsymbol=sym))
            self.assertNotIn("asset:", reasons(res), sym)


class StopLegs(unittest.TestCase):
    def test_an_entry_needs_a_stop(self):
        o = entry(plan={"entry": 1193.6, "target": 1203.6})
        res = check.check([o], cfg(), now=at(NOW))
        self.assertIn("stop: no stop", reasons(res))
        res = check.check([o], cfg(require_stop=False, min_reward_risk=0), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("no stop", warnings(res))

    def test_a_plan_stop_counts_but_warns(self):
        res = check.check([entry()], cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("in the plan only, not a stop leg for Kite", warnings(res))
        self.assertEqual(res["results"][0]["max_loss"], 767.69)

    def test_the_stop_leg_is_the_one_kite_gets(self):
        res = check.check([entry(), stop(trigger_price=1185.0, price=1179.0)], cfg(), now=at(NOW))
        r = res["results"][0]
        self.assertEqual((r["stop"], r["risk_per_share"]), (1185.0, 9.8))
        self.assertIn("differs from the plan's stop 1183.60", warnings(res))
        self.assertIn("must be below the entry 1194.80",
                      reasons(check.check([entry(), stop(trigger_price=1195.0, price=1190.0)], cfg(), now=at(NOW))))

    def test_the_stop_must_cover_the_whole_entry(self):
        res = check.check([entry(), stop(quantity=40)], cfg(), now=at(NOW))
        self.assertIn("the stop leg sells 40 shares but this entry buys 62", reasons(res))

    def test_a_stop_leg_is_tied_to_its_entry_or_your_shares(self):
        res = check.check([stop()], cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertIn("no entry for group RELIANCE-2026-10-26-orb in this batch", reasons(res))
        res = check.check([stop()], cfg(), portfolio=PF, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("protects the 62 shares you hold", res["results"][0]["info"][0])
        res = check.check([stop(quantity=70)], cfg(), portfolio=PF, now=at(NOW))
        self.assertIn("would sell short", reasons(res))
        res = check.check([stop(transaction_type="BUY")], cfg(), portfolio=PF, now=at(NOW))
        self.assertIn("a stop leg must be a SELL", reasons(res))
        res = check.check([stop(order_type="LIMIT")], cfg(), portfolio=PF, now=at(NOW))
        self.assertIn("a stop leg must be order_type SL", reasons(res))

    def test_a_stop_leg_is_never_blocked_by_the_day_limits(self):
        full = {"orders": [placed("HDFCBANK"), placed("INFY"), placed("TCS")] +
                          [placed("T%d" % i, max_loss=1) for i in range(6)] + [placed("RELIANCE", side="SELL")]}
        c = cfg(blocked=["RELIANCE"], max_daily_loss_inr=100)
        res = check.check([stop()], c, portfolio=PF, state=full, now=at("2026-10-26 15:20"))
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"]), ("PASS", "stop"), r["reasons"])
        for word in ("blocked:", "duplicate:", "daily limit:"):
            self.assertIn("stop, not blocked: " + word, warnings(res))

    def test_a_trigger_above_the_last_price_warns(self):
        res = check.check([stop()], cfg(), portfolio=PF, prices={"RELIANCE": 1183.0}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("Kite rejects a stop-loss SELL whose trigger is above the last price", warnings(res))


class DayLimits(unittest.TestCase):
    def one(self, orders, now=NOW, **over):
        return check.check(orders, cfg(**over), now=at(now))

    def test_reward_risk_from_the_price_it_expects_to_pay(self):
        res = check.check(reel(), cfg(), prices={"RELIANCE": 1194.0}, now=at(NOW), price_times={"RELIANCE": QUOTE_TIME})
        self.assertIn("reward:risk: 0.92 (target 1203.60, entry 1194.00, stop 1183.60) is under your minimum 1",
                      reasons(res))
        res = check.check(reel(), cfg(), prices={"RELIANCE": 1196.0}, now=at(NOW), price_times={"RELIANCE": QUOTE_TIME})
        self.assertIn("reward:risk: 0.79", reasons(res))
        self.assertIn("above your limit 1194.80: the order may not fill", warnings(res))
        res = check.check(reel(), cfg(), prices={"RELIANCE": 1190.0}, now=at(NOW), price_times={"RELIANCE": QUOTE_TIME})
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual(res["results"][0]["reward_risk"], 1.0)
        self.assertEqual(verdicts(self.one(reel(), min_reward_risk=0.5)), ["PASS", "PASS"])

    def test_a_stale_price_is_not_used(self):
        res = check.check(reel(), cfg(), prices={"RELIANCE": 1194.0}, now=at("2026-10-26 10:30"),
                          price_times={"RELIANCE": QUOTE_TIME})
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertIn("older than 15 minutes", warnings(res))

    def test_daily_loss_and_trades_per_day(self):
        two = {"orders": [placed("HDFCBANK"), placed("INFY")]}
        res = check.check(reel(), cfg(), state=two, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertIn("today before this: 2 of 3 trades, ₹1,535.38 of ₹3,000 daily loss", res["results"][0]["info"][-1])
        three = {"orders": [placed("HDFCBANK"), placed("INFY"), placed("TCS")]}
        res = check.check(reel(), cfg(), state=three, now=at(NOW))
        why = reasons(res)
        self.assertIn("trades: 3 entries placed today", why)
        self.assertIn("daily loss: ₹2,303.07 already at risk today plus ₹767.69 for this one = ₹3,070.76, over your "
                      "₹3,000", why)
        yesterday = {"orders": [placed(s, day="2026-10-23") for s in ("HDFCBANK", "INFY", "TCS")]}
        self.assertEqual(verdicts(check.check(reel(), cfg(), state=yesterday, now=at(NOW))), ["PASS", "PASS"])

    def test_stops_exits_and_declines_do_not_count_as_trades(self):
        st = {"orders": [placed("HDFCBANK"), placed("HDFCBANK", side="SELL", kind="stop", max_loss=None),
                         placed("HDFCBANK", side="SELL", kind="exit", max_loss=None),
                         placed("INFY", status="declined"), placed("TCS", status="failed")]}
        res = check.check(reel(), cfg(), state=st, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual(res["today"]["entries_placed"], 1)

    def test_the_batch_counts_toward_the_daily_limits(self):
        orders = []
        for s in ("AAA", "BBB", "CCC", "DDD"):
            orders += group(s, 10, 50.0, 40.0, 39.8, 70.0)
        res = check.check(orders, cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"] * 6 + ["BLOCK", "BLOCK"])
        self.assertIn("trades: 0 entries placed today plus 3 passed earlier", reasons(res, 6))
        self.assertIn("its entry (#7) was blocked", reasons(res, 7))

    def test_entry_window_india_time(self):
        cases = [("2026-10-26 09:29", "no new entries before 09:30 IST (it is 09:29)"),
                 ("2026-10-26 14:31", "no new entries after 14:30 IST (it is 14:31)"),
                 ("2026-10-26 09:10", "the market is closed (Mon 2026-10-26 09:10 IST; the session is 09:15 to 15:30)"),
                 ("2026-10-26 15:35", "the market is closed"),
                 ("2026-10-25 10:00", "the market is closed today (weekend"),
                 ("2026-11-10 10:00", "the market is closed today (NSE holiday: Diwali-Balipratipada"),
                 ("2026-10-20 10:00", "the market is closed today (NSE holiday: Dussehra"),
                 ("2026-11-08 14:25", "today is a special session (Muhurat trading (Diwali), 14:20 to 15:30 IST)"),
                 ("2026-10-26T03:55:00Z", "no new entries before 09:30 IST (it is 09:25)")]
        for now, want in cases:
            res = self.one(reel(), now=now)
            self.assertIn("window: " + want, reasons(res), now)
        for now in ("2026-10-26 09:30", "2026-10-26 14:30", "2026-10-26T04:30:00Z", "2026-10-26 10:00 IST"):
            self.assertEqual(verdicts(self.one(reel(), now=now)), ["PASS", "PASS"], now)
        res = self.one(reel(), now="2026-11-08 14:25", allow_special_sessions=True)
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertIn("special session today", " ".join(res["warnings"]))
        res = self.one(reel(), now="2026-10-25 10:00", no_entries_before=None, no_entries_after=None)
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        with self.assertRaises(check.RiskError):
            self.one(reel(), no_entries_before="9.30am")

    def test_no_new_mis_entries_at_or_after_flat_by(self):
        res = self.one(reel(), now="2026-10-26 15:00", no_entries_after=None)
        self.assertIn("no new MIS entries at or after flat_by 15:00 IST (it is 15:00)", reasons(res))
        self.assertIn("₹50 + 18% GST", reasons(res))
        self.assertEqual(verdicts(self.one(reel(), now="2026-10-26 14:59", no_entries_after=None)), ["PASS", "PASS"])

    def test_a_year_nse_has_not_published(self):
        res = self.one(reel(), now="2027-01-04 10:00")
        self.assertIn("NSE has not published its 2027 holiday list in this tool yet: check nseindia.com before "
                      "trading", res["warnings"])
        self.assertEqual(verdicts(res), ["PASS", "PASS"])

    def test_india_time_not_the_machine(self):
        self.assertEqual(at("2026-10-26 10:00"), datetime.datetime(2026, 10, 26, 4, 30))
        self.assertEqual(check.to_ist(at("2026-10-26 10:00")), datetime.datetime(2026, 10, 26, 10, 0))
        self.assertEqual(at("2026-06-01 10:00"), datetime.datetime(2026, 6, 1, 4, 30))   # no daylight saving


class Exits(unittest.TestCase):
    SELL = sell("RELIANCE", 62, 1193.4)

    def test_getting_out_is_never_blocked_by_the_day_limits(self):
        full = {"orders": [placed("RELIANCE"), placed("INFY"), placed("TCS")] +
                          [placed("T%d" % i, max_loss=1) for i in range(6)] +
                          [placed("RELIANCE", side="SELL", kind="stop", max_loss=None)]}
        c = cfg(blocked=["RELIANCE"], max_order_inr=100, max_daily_loss_inr=50)
        res = check.check([self.SELL], c, portfolio=PF, state=full, now=at("2026-10-26 15:20"))
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"]), ("PASS", "exit"), r["reasons"])
        why = warnings(res)
        for word in ("blocked:", "size:", "duplicate:", "daily limit:"):
            self.assertIn("exit, not blocked: " + word, why)
        res = check.check([self.SELL], cfg(), portfolio=PF, now=at("2026-10-25 12:00"))
        self.assertEqual(verdicts(res), ["PASS"])

    def test_a_partial_exit_and_the_rest(self):
        half = dict(self.SELL, quantity=31)
        res = check.check([half, half], cfg(), portfolio=PF, prices={"RELIANCE": 1199.0}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual([r["kind"] for r in res["results"]], ["exit", "exit"])
        self.assertIn("exit, not blocked: duplicate", warnings(res, 1))
        res = check.check([half, half, half], cfg(), portfolio=PF, prices={"RELIANCE": 1199.0}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "BLOCK"])
        self.assertIn("short:", reasons(res, 2))

    def test_format_asset_and_short_still_apply_to_a_sell(self):
        res = check.check([dict(self.SELL, order_type="MARKET", price=None)], cfg(), portfolio=PF, now=at(NOW))
        self.assertIn("MARKET order needs market protection", reasons(res))
        res = check.check([dict(self.SELL, quantity=63)], cfg(), portfolio=PF, now=at(NOW))
        self.assertIn("short: selling more RELIANCE than you hold (62 shares)", reasons(res))
        res = check.check([self.SELL], cfg(), now=at(NOW))
        self.assertIn("record the fill first", reasons(res))
        res = check.check([dict(self.SELL, product="CNC")], cfg(), portfolio=PF, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("CNC sell", warnings(res))

    def test_buy_stop_then_sell_in_one_batch(self):
        res = check.check(reel() + [self.SELL], cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "PASS"])
        self.assertEqual([r["kind"] for r in res["results"]], ["entry", "stop", "exit"])


class AccountChecks(unittest.TestCase):
    """The account checks still hold with every intraday check off (india-budget's weights mode)."""

    def one(self, order, **over):
        return check.check([order], acct(**over), prices=PRICES, today=TODAY)["results"][0]

    def test_the_five_buys_pass_on_a_fresh_paper_account(self):
        res = check.check(split(), acct(), today=TODAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5)
        self.assertEqual(res["cash_after"], 10013.3)
        self.assertEqual(res["results"][0]["value_inr"], 26285.6)
        self.assertEqual(res["results"][0]["amount"], "22 shares")

    def test_a_sixth_buy_is_blocked_three_ways(self):
        res = check.check(split() + [buy("RELIANCE", 1, 1194.8)], acct(max_position_pct=27), today=TODAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5 + ["BLOCK"])
        why = reasons(res, 5)
        for word in ("cash:", "position:", "duplicate:"):
            self.assertIn(word, why)

    def test_the_example_config_blocks_buys_without_a_stop(self):
        res = check.check(split(), cfg(), now=at(NOW))
        self.assertEqual(verdicts(res)[0], "BLOCK")
        self.assertIn("stop: no stop", reasons(res))

    def test_max_and_min_order(self):
        self.assertIn("over your ₹80,000 max order", " ".join(self.one(buy("HDFCBANK", 120, 692.6),
                                                                      max_position_pct=100)["reasons"]))
        self.assertIn("under your ₹500 minimum", " ".join(self.one(buy("ITC", 2))["reasons"]))

    def test_blocked_list(self):
        r = self.one(buy("ITC", 10), blocked=["itc.ns"])
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("blocked list", r["reasons"][0])

    def test_margin_is_off(self):
        r = check.check([buy("ITC", 10)], acct(cash_buffer_pct=0, max_position_pct=100),
                        portfolio={"cash": 1000, "positions": []}, today=TODAY)["results"][0]
        self.assertIn("margin", " ".join(r["reasons"]))

    PF2 = {"cash": 50000.0, "positions": [{"ticker": "ITC", "shares": 10,
                                            "lots": [{"date": TODAY, "shares": 10, "cost": 2000.0}]}]}

    def test_short_sales_are_blocked(self):
        r = check.check([sell("ITC", 11, 200.0)], acct(), portfolio=self.PF2, prices=PRICES, today=TODAY)["results"][0]
        self.assertIn("short:", " ".join(r["reasons"]))
        r = check.check([sell("ITC", 3, 200.0)], acct(), portfolio=self.PF2, prices=PRICES, today=TODAY)["results"][0]
        self.assertEqual(r["verdict"], "PASS")

    def test_a_sell_frees_cash_for_a_later_buy(self):
        pf = {"cash": 1000.0, "positions": [{"ticker": "ITC", "shares": 100,
                                             "lots": [{"date": TODAY, "shares": 100, "cost": 20000.0}]}]}
        c = acct(max_position_pct=100, cash_buffer_pct=0)
        res = check.check([sell("ITC", 50, 200.0), buy("HDFCBANK", 10, 692.6)], c, portfolio=pf, prices=PRICES,
                          today=TODAY)
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        res = check.check([buy("HDFCBANK", 10, 692.6)], c, portfolio=pf, prices=PRICES, today=TODAY)
        self.assertEqual(verdicts(res), ["BLOCK"])

    def test_daily_limit_and_already_placed_today(self):
        st = {"orders": [{"symbol": "T%d" % i, "side": "BUY", "status": "placed", "at": TODAY + "T10:00:00+05:30"}
                         for i in range(10)]}
        res = check.check([buy("ITC", 10)], acct(), state=st, today=TODAY)
        self.assertIn("daily limit", reasons(res))
        res = check.check([buy("ITC", 10)], acct(), state=st, today="2026-10-27")
        self.assertEqual(verdicts(res), ["PASS"])
        itc = {"orders": [{"symbol": "ITC", "side": "BUY", "status": "placed", "at": TODAY + "T10:00:00+05:30"}]}
        self.assertIn("already placed today", reasons(check.check([buy("ITC", 10)], acct(), state=itc, today=TODAY)))
        declined = {"orders": [{"symbol": "ITC", "side": "BUY", "status": "declined", "at": TODAY + "T10:00:00"}]}
        self.assertEqual(verdicts(check.check([buy("ITC", 10)], acct(), state=declined, today=TODAY)), ["PASS"])

    def test_live_account_warns(self):
        res = check.check([buy("ITC", 10)], acct(paper=False), today=TODAY)
        self.assertIn("LIVE", res["warnings"][0])


class Cli(unittest.TestCase):
    def test_prices_file_freshness(self):
        with tempfile.TemporaryDirectory() as d:
            pp = os.path.join(d, "prices.json")
            with open(pp, "w", encoding="utf-8") as fh:
                json.dump({"as_of": "2026-10-26T04:29:00Z", "prices": {"RELIANCE.NS": 1194.0},
                           "detail": {"RELIANCE.NS": {"time": "2026-10-26T04:28:00Z"}}}, fh)
            none = os.path.join(d, "none")
            base = ["--orders", REEL_PATH, "--config", CFG_PATH, "--portfolio", none, "--prices", pp,
                    "--state", none, "--no-write"]
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(check.main(base + ["--now", NOW]), 1)
            self.assertIn("reward:risk: 0.92", buf.getvalue())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(check.main(base + ["--now", "2026-10-26 10:30"]), 0)

    def test_the_split_with_an_account_only_config(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "config.json")
            with open(c, "w", encoding="utf-8") as fh:
                json.dump(acct(), fh)
            none = os.path.join(d, "none")
            out = os.path.join(d, "state", "risk.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", SPLIT_PATH, "--config", c, "--portfolio", none, "--prices", none,
                                   "--state", none, "--today", TODAY, "--out", out])
            self.assertEqual(code, 0)
            self.assertIn("5 passed, 0 blocked", buf.getvalue())
            with open(out, encoding="utf-8") as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], TODAY)
            self.assertTrue(all(len(r["fingerprint"]) == 16 for r in rec["results"]))

    def test_errors_are_one_line(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", "/nonexistent.json", "--no-write"]), 1)
        self.assertIn("no orders file", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", REEL_PATH, "--now", "noon", "--no-write"]), 1)
        self.assertIn("--now wants an India time", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", REEL_PATH, "--today", "monday", "--no-write"]), 1)
        self.assertIn("--today wants", err.getvalue())


if __name__ == "__main__":
    unittest.main()
