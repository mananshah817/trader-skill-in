import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

size = load("india-budget", "size")

CFG = os.path.join(ROOT, "india-budget", "config.example.json")
PLAN = os.path.join(FIX, "india-budget", "plan-reliance.json")
TARGETS = os.path.join(ROOT, "india-budget", "targets.example.json")
# REAL closes of the 09:55 IST bars on 2026-10-26 (Yahoo .NS), see the note in the file
PRICES_PATH = os.path.join(FIX, "india-budget", "prices-2026-10-26-1000.json")
ORDERS_FIXTURE = os.path.join(FIX, "india-risk", "orders-reliance.json")
NOW = "2026-10-26 10:00"
TODAY = "2026-10-26"
# the contract's config (docs/SPEC.md section 7), key for key
CONTRACT_CFG = {
    "budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 80000, "min_order_inr": 500,
    "max_loss_per_trade_inr": 1000, "max_daily_loss_inr": 3000, "max_trades_per_day": 3, "max_orders_per_day": 10,
    "min_reward_risk": 1.0, "require_stop": True,
    "no_entries_before": "09:30", "no_entries_after": "14:30", "flat_by": "15:00",
    "exchange": "NSE", "product": "MIS", "entry_limit_buffer_pct": 0.1, "stop_limit_buffer_pct": 0.5,
    "include_charges": True, "broker": "zerodha", "paper": True, "blocked": [],
    "allow_margin": False, "allow_short": False, "allow_special_sessions": False,
}


def cfg(**over):
    with open(CFG, encoding="utf-8") as fh:
        c = json.load(fh)
    c.update(over)
    return c


def plan(**over):
    with open(PLAN, encoding="utf-8") as fh:
        p = json.load(fh)
    p.update(over)
    return p


def prices():
    with open(PRICES_PATH, encoding="utf-8") as fh:
        return {k: float(v) for k, v in json.load(fh)["prices"].items()}


def rows(path):
    with open(path, encoding="utf-8") as fh:
        return size.parse_weights(json.load(fh))


def shares(result):
    return {p["ticker"]: p["shares"] for p in result["positions"]}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


def prices_doc(price, quote_time="2026-10-26T04:40:00Z"):
    return {"as_of": quote_time, "source": "yahoo", "prices": {"RELIANCE": price},
            "detail": {"RELIANCE": {"time": quote_time, "source": "yahoo"}}}


class Helpers(unittest.TestCase):
    def test_indian_money(self):
        self.assertEqual(size.money(100000), "₹1,00,000")
        self.assertEqual(size.money(1234567.5), "₹12,34,567.50")
        self.assertEqual(size.money(46.875), "₹46.88")
        self.assertEqual(size.money(-500), "-₹500")
        self.assertEqual(size.money(999), "₹999")
        self.assertEqual(size.money(10000000), "₹1,00,00,000")

    def test_nse_ticks_in_paise(self):
        self.assertEqual([size.tick_size(p) for p in (249.99, 250, 1000, 1000.1, 5000, 5000.5, 10000, 20000, 20001)],
                         [0.01, 0.05, 0.05, 0.10, 0.10, 0.50, 0.50, 1.00, 5.00])
        self.assertEqual(size.tick_up(1193.6 * 1.001), 119480)      # an entry limit rounds UP
        self.assertEqual(size.tick_down(1183.6 * 0.995), 117760)    # a stop-limit price rounds DOWN
        self.assertEqual(size.tick_down(1183.65), 118360)           # the reel's stop, down to the 0.10 tick
        self.assertEqual(size.tick_up(1194.80), 119480)             # already on the tick: unchanged
        self.assertEqual(size.tick_up(691.9 * 1.001), 69260)        # 0.05 tick under 1000
        self.assertEqual(size.tick_up(1194.81, "RELIANCE", {"tick_size": {"RELIANCE": 1}}), 119500)

    def test_zerodha_charges(self):
        ch = size.charges(62 * 1194.8, 62 * 1183.6)
        self.assertEqual(ch, {"brokerage": 40.0, "stt": 18.35, "txn": 4.53, "sebi": 0.15, "stamp": 2.22,
                              "gst": 8.04, "total": 73.29})
        small = size.charges(10000, 10000)                          # 0.03% = ₹3 a side, under the ₹20 cap
        self.assertEqual((small["brokerage"], small["stt"], small["stamp"]), (6.0, 2.5, 0.3))
        self.assertEqual(size.charges(10000, 0)["stt"], 0.0)        # STT is sell side only
        self.assertGreater(size.charges(100000, 100000, exchange="BSE")["txn"],
                           size.charges(100000, 100000)["txn"])

    def test_india_time(self):
        self.assertEqual(size.parse_now("2026-10-26 10:00").isoformat(), "2026-10-26T04:30:00")
        self.assertEqual(size.parse_now("2026-10-26 10:00 IST").isoformat(), "2026-10-26T04:30:00")
        self.assertEqual(size.parse_now("2026-10-26T04:30:00Z").isoformat(), "2026-10-26T04:30:00")
        with self.assertRaises(size.SizeError) as e:
            size.parse_now("noon")
        self.assertIn("an India time like", str(e.exception))


class TheReel(unittest.TestCase):
    """₹1,00,000, 10% buffer, ₹1,000 max loss, RELIANCE entry 1193.60 -> limit 1194.80, 62 shares."""

    def test_config_example_is_the_contract(self):
        with open(CFG, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), CONTRACT_CFG)

    def test_reliance_is_62_shares_bound_by_the_position_cap(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        self.assertEqual((s["limit"], s["stop"], s["stop_limit"], s["target"]), (1194.8, 1183.6, 1177.6, 1203.6))
        self.assertEqual(s["risk_per_share"], 11.2)
        self.assertEqual(s["qty"], 62)
        self.assertEqual(s["bound_by"], "position cap")
        self.assertEqual([(l["name"], l["shares"]) for l in s["limits"]],
                         [("max loss", 81), ("position cap", 62), ("max order", 66), ("cash", 75)])
        self.assertEqual([l["name"] for l in s["limits"] if l["binds"]], ["position cap"])
        self.assertEqual(s["cost"], 74077.6)
        self.assertEqual(s["loss_before_charges"], 694.4)
        self.assertEqual(s["charges"]["total"], 73.29)
        self.assertEqual(s["max_loss"], 767.69)
        self.assertEqual(s["loss_at_stop_limit"], 1139.58)
        self.assertEqual((s["reward_risk"], s["reward_risk_at_limit"]), (1.0, 0.79))
        self.assertEqual(s["cash_buffer"], 10000)
        self.assertIsNone(s["refusal"])

    def test_charges_decide_the_max_loss_limit(self):
        # 82 shares: 82 x 11.20 = 918.40 + 81.70 charges = 1,000.10, just over; 81 fits at 988.47
        self.assertEqual(size.loss_at(82, 119480, 118360, size.DEFAULTS, "NSE")[0], 1000.1)
        self.assertEqual(size.loss_at(81, 119480, 118360, size.DEFAULTS, "NSE")[0], 988.47)
        s = size.size_trade(plan(), cfg(max_position_pct=100, max_order_inr=200000, cash_buffer_pct=0,
                                         budget=200000), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (81, "max loss"))
        s = size.size_trade(plan(), cfg(max_position_pct=100, max_order_inr=200000, cash_buffer_pct=0,
                                         budget=200000, include_charges=False), today=TODAY)
        self.assertEqual((s["qty"], s["max_loss"]), (89, 996.8))

    def test_the_order_group(self):
        orders = size.order_group(size.size_trade(plan(), cfg(), today=TODAY))
        entry, stop = orders
        why = entry.pop("why")
        stop_why = stop.pop("why")
        self.assertEqual(entry, {
            "role": "entry", "group": "RELIANCE-2026-10-26-orb", "variety": "regular", "exchange": "NSE",
            "tradingsymbol": "RELIANCE", "transaction_type": "BUY", "quantity": 62, "product": "MIS",
            "order_type": "LIMIT", "price": 1194.8, "validity": "DAY", "tag": "india-orb",
            "plan": {"entry": 1193.6, "limit": 1194.8, "stop": 1183.6, "stop_limit": 1177.6, "target": 1203.6,
                     "risk_per_share": 11.2, "charges": 73.29, "max_loss": 767.69, "setup": "orb",
                     "entry_time": "10:00", "flat_by": "15:00", "date": "2026-10-26"}})
        self.assertEqual(stop, {
            "role": "stop", "group": "RELIANCE-2026-10-26-orb", "after": "entry", "variety": "regular",
            "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "SELL", "quantity": 62,
            "product": "MIS", "order_type": "SL", "trigger_price": 1183.6, "price": 1177.6, "validity": "DAY",
            "tag": "india-stop"})
        self.assertIn("bound by your position cap", why)
        self.assertIn("₹694.40 at the stop plus ₹73.29 charges = ₹767.69", why)
        self.assertIn("only after the entry fills", stop_why)
        self.assertIn("gap below 1177.60", stop_why)
        self.assertIsInstance(entry["quantity"], int)

    def test_the_fixture_orders_file_is_what_it_writes(self):
        with open(ORDERS_FIXTURE, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(size.order_group(size.size_trade(plan(), cfg(), today=TODAY)), doc["orders"])
        self.assertEqual(doc["created_by"], "india-budget")
        self.assertEqual(doc["created_at"], "2026-10-26T04:30:00Z")
        self.assertEqual(sorted(doc), ["created_at", "created_by", "note", "orders", "paper", "status"])

    def test_your_own_limit(self):
        s = size.size_trade(plan(), cfg(), limit=1193.6, today=TODAY)
        self.assertEqual((s["limit"], s["risk_per_share"], s["reward_risk"]), (1193.6, 10.0, 1.0))
        s = size.size_trade(plan(), cfg(), limit=1194.03, today=TODAY)
        self.assertEqual(s["limit"], 1194.1)                       # rounded UP to the tick

    def test_cli_writes_the_group(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "trader", "orders.json")
            code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--write", out])
            self.assertEqual(code, 0)
            self.assertIn("Size: 62 shares of RELIANCE = ₹74,077.60 at the limit 1194.80, bound by the position cap",
                          text)
            self.assertIn("62 x 11.20 = ₹694.40 plus ₹73.29 charges = ₹767.69", text)
            self.assertIn("brokerage ₹40, STT ₹18.35, exchange ₹4.53, SEBI ₹0.15, stamp duty ₹2.22, GST ₹8.04",
                          text)
            self.assertIn("the loss is ₹1,139.58 (over your ₹1,000 max loss", text)
            self.assertIn("<- binds", text)
            self.assertIn("SL trigger 1183.60 price 1177.60", text)
            self.assertIn("₹50 + 18% GST", text)
            self.assertIn("Not financial advice", text)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual(doc["status"], "proposed")
            self.assertIs(doc["paper"], True)
            self.assertEqual(doc["created_at"], "2026-10-26T04:30:00Z")
            self.assertEqual([o["role"] for o in doc["orders"]], ["entry", "stop"])
            self.assertEqual(doc["orders"][0]["plan"]["max_loss"], 767.69)

    def test_cli_json_and_limit(self):
        code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent", "--now", NOW,
                             "--limit", "--json"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(out["sizing"]["qty"], 62)
        self.assertEqual(out["orders"][0]["price"], 1193.6)


class PlanRules(unittest.TestCase):
    def test_zero_shares_is_a_plain_refusal(self):
        s = size.size_trade(plan(stop=150.0, target=3000.0), cfg(), today=TODAY)
        self.assertEqual(s["qty"], 0)
        self.assertIn("more than your ₹1,000 max loss per trade", s["refusal"])
        self.assertIsNone(size.order_group(s))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "plan.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(plan(stop=150.0, target=3000.0), fh)
            out = os.path.join(d, "orders.json")
            code, text, _ = run(["--plan", p, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--write", out])
            self.assertEqual(code, 1)
            self.assertIn("No order:", text)
            self.assertFalse(os.path.exists(out))

    def test_each_limit_can_bind(self):
        s = size.size_trade(plan(), cfg(max_order_inr=50000), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (41, "max order"))
        s = size.size_trade(plan(), cfg(), cash=40000, today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["cash_available"]), (25, "cash", 30000))
        s = size.size_trade(plan(), cfg(max_loss_per_trade_inr=500), today=TODAY)
        self.assertEqual(s["bound_by"], "max loss")
        self.assertLessEqual(s["max_loss"], 500)

    def test_a_fresh_higher_price_means_a_bigger_risk(self):
        s = size.size_trade(plan(), cfg(min_reward_risk=0), price=1197.0, today=TODAY)
        self.assertEqual((s["expected_entry"], s["expected_from"], s["limit"]), (1197.0, "price", 1198.2))
        self.assertEqual(s["risk_per_share"], 14.6)
        self.assertIsNone(s["refusal"])
        s = size.size_trade(plan(), cfg(), price=1197.0, today=TODAY)
        self.assertIn("reward:risk 0.49 (target 1203.60, entry 1197.00, stop 1183.60) is under your minimum 1",
                      s["refusal"])
        self.assertIsNone(size.order_group(s))
        lower = size.size_trade(plan(), cfg(), price=1190.0, today=TODAY)
        self.assertEqual((lower["limit"], lower["qty"]), (1194.8, 62))

    def test_prices_file_is_used_only_when_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            pp = os.path.join(d, "prices.json")
            with open(pp, "w", encoding="utf-8") as fh:
                json.dump(prices_doc(1197.0), fh)
            base = ["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"), "--prices", pp, "--json"]
            code, text, _ = run(base + ["--now", "2026-10-26 10:15"])
            out = json.loads(text)
            self.assertEqual((code, out["orders"]), (1, []))
            self.assertEqual(out["sizing"]["limit"], 1198.2)
            self.assertIn("above the plan's entry", out["notes"][0])
            out = json.loads(run(base + ["--now", "2026-10-26 10:40"])[1])
            self.assertEqual(out["sizing"]["qty"], 62)
            self.assertIn("not used", out["notes"][0])

    def test_the_portfolio_cash_caps_the_size(self):
        with tempfile.TemporaryDirectory() as d:
            pf = os.path.join(d, "portfolio.json")
            with open(pf, "w", encoding="utf-8") as fh:
                json.dump({"cash": 40000, "positions": []}, fh)
            out = json.loads(run(["--plan", PLAN, "--config", CFG, "--portfolio", pf, "--now", NOW, "--json"])[1])
            self.assertEqual((out["sizing"]["qty"], out["sizing"]["bound_by"]), (25, "cash"))

    def test_bad_plans(self):
        for p, word in ((plan(stop=1195.0), "not below the entry"),
                        (plan(target=1194.0), "already happened"),
                        (plan(target=None), "positive target"),
                        (plan(side="sell"), "long (buy) intraday trades only"),
                        (plan(symbol=""), "no symbol"),
                        (plan(exchange="NFO"), "cash equity only")):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(p, cfg(), today=TODAY)
            self.assertIn(word, str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), price=1204.0, today=TODAY)
        self.assertIn("already happened", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(blocked=["reliance.ns"]), today=TODAY)
        self.assertIn("blocked list", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), today="2026-10-27")
        self.assertIn("make a new plan", str(e.exception))

    def test_ns_suffix_is_stripped(self):
        s = size.size_trade(plan(symbol="reliance.ns"), cfg(), today=TODAY)
        self.assertEqual((s["symbol"], s["exchange"]), ("RELIANCE", "NSE"))

    def test_under_the_minimum_order(self):
        cheap = plan(symbol="ABC", entry=40.0, stop=10.0, target=100.0)
        s = size.size_trade(cheap, cfg(max_loss_per_trade_inr=300), today=TODAY)
        self.assertEqual((s["qty"], s["limit"], s["cost"]), (9, 40.04, 360.36))
        self.assertIn("under your ₹500 minimum order", s["refusal"])
        self.assertIsNone(size.order_group(s))

    def test_missing_plan_and_weights_only_flags(self):
        code, _, err = run(["--plan", "/nonexistent/plan.json", "--config", CFG, "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", NOW, "--top", "2"])
        self.assertEqual(code, 1)
        self.assertIn("--weights", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", "tomorrow"])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)


class WeightsMode(unittest.TestCase):
    """v1's split, kept behind --weights / --targets: whole shares at limit prices, real 10:00 IST prices."""

    def test_the_example_split(self):
        r = size.size(rows(TARGETS), cfg(), prices=prices())
        self.assertEqual(shares(r), {"RELIANCE": 22, "HDFCBANK": 33, "ICICIBANK": 13, "INFY": 11, "TCS": 4})
        self.assertEqual({p["ticker"]: p["limit"] for p in r["positions"]},
                         {"RELIANCE": 1194.8, "HDFCBANK": 692.6, "ICICIBANK": 1393.3, "INFY": 1142.0, "TCS": 2542.6})
        self.assertEqual((r["cash_buffer"], r["invested"], r["idle"]), (10000, 89986.7, 13.3))
        self.assertEqual([p["ticker"] for p in r["positions"] if p["capped"]], [])

    def test_the_cap_is_shared_out(self):
        r = size.size(rows(TARGETS), cfg(max_position_pct=25), prices=prices())
        self.assertEqual([p["ticker"] for p in r["positions"] if p["capped"]], ["RELIANCE"])
        for p in r["positions"]:
            self.assertLessEqual(p["inr"], 25000)
        self.assertEqual(shares(r)["RELIANCE"], 20)

    def test_example_is_labelled_not_advice(self):
        with open(TARGETS, encoding="utf-8") as fh:
            src = json.load(fh)["source"]
        self.assertIn("EXAMPLE ONLY, not advice", src)
        self.assertIn("made-up weights", src)

    def test_orders_are_kite_shaped_and_have_no_stop(self):
        r = size.size(rows(TARGETS), cfg(), prices=prices())
        orders = size.orders_from(r, "an example", TODAY)
        self.assertEqual(orders[0], {"role": "entry", "group": "RELIANCE-2026-10-26-split", "variety": "regular",
                                     "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "BUY",
                                     "quantity": 22, "product": "MIS", "order_type": "LIMIT", "price": 1194.8,
                                     "validity": "DAY", "tag": "india-split", "why": orders[0]["why"]})
        self.assertFalse(any(o["role"] == "stop" for o in orders))

    def test_cap_redistributes_until_nobody_is_over(self):
        w = size.parse_weights({"A": 60, "B": 30, "C": 10})
        r = size.size(w, {"budget": 1000, "cash_buffer_pct": 0, "max_position_pct": 40, "min_order_inr": 1,
                          "entry_limit_buffer_pct": 0}, prices={"A": 1, "B": 1, "C": 1})
        self.assertEqual(shares(r), {"A": 400, "B": 400, "C": 200})
        self.assertEqual(sorted(p["ticker"] for p in r["positions"] if p["capped"]), ["A", "B"])

    def test_slices_under_the_minimum_are_dropped(self):
        w = size.parse_weights({"A": 90, "B": 7, "C": 3})
        r = size.size(w, {"budget": 10000, "cash_buffer_pct": 10, "max_position_pct": 100, "min_order_inr": 500,
                          "entry_limit_buffer_pct": 0}, prices={"A": 10, "B": 10, "C": 10})
        self.assertNotIn("C", shares(r))
        self.assertEqual(r["dropped"][0]["ticker"], "C")

    def test_blocked_top_and_prices(self):
        r = size.size(rows(TARGETS), cfg(blocked=["infy"]), prices=prices())
        self.assertNotIn("INFY", shares(r))
        self.assertIn("blocked", r["dropped"][0]["reason"])
        r = size.size(rows(TARGETS), cfg(), prices=prices(), top=2)
        self.assertEqual(sorted(shares(r)), ["HDFCBANK", "RELIANCE"])
        with self.assertRaises(size.SizeError) as e:
            size.size(rows(TARGETS), cfg())
        self.assertIn("whole shares need a price", str(e.exception))

    def test_no_budget_and_duplicates_are_errors(self):
        with self.assertRaises(size.SizeError):
            size.size(rows(TARGETS), {"budget": None}, prices=prices())
        with self.assertRaises(size.SizeError):
            size.size([{"ticker": "TCS", "name": "", "weight": 1}, {"ticker": "TCS", "name": "", "weight": 2}],
                      cfg(), prices=prices())

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "trader", "orders.json")
            code, text, _ = run(["--targets", TARGETS, "--config", CFG, "--prices", PRICES_PATH, "--now", NOW,
                                 "--write", out])
            self.assertEqual(code, 0)
            self.assertIn("left idle", text)
            self.assertIn("require_stop", text)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual([o["tradingsymbol"] for o in doc["orders"]],
                             ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS"])
            code, text, _ = run(["--weights", "RELIANCE.NS=50,TCS=50", "--config", CFG, "--prices", PRICES_PATH,
                                 "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(sorted(shares(json.loads(text)["result"])), ["RELIANCE", "TCS"])
            code, _, err = run(["--targets", "/nonexistent/targets.json", "--config", CFG])
            self.assertEqual(code, 1)
            self.assertIn("no weights file", err)


if __name__ == "__main__":
    unittest.main()
