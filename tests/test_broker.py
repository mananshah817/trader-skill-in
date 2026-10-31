import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

ticket = load("india-broker", "ticket")
check = load("india-risk", "check")

CFG_PATH = os.path.join(ROOT, "india-budget", "config.example.json")
ORDERS_PATH = os.path.join(FIX, "india-risk", "orders-reliance.json")   # india-budget's output for the reel
SPLIT_PATH = os.path.join(FIX, "india-risk", "orders-split.json")
PLAN_PATH = os.path.join(FIX, "india-budget", "plan-reliance.json")
NOW = "2026-10-26 10:00"
TODAY = "2026-10-26"
ENTRY_CALL = {"variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "BUY",
              "quantity": 62, "product": "MIS", "order_type": "LIMIT", "price": 1194.8, "validity": "DAY",
              "tag": "india-orb"}
STOP_CALL = {"variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "SELL",
             "quantity": 62, "product": "MIS", "order_type": "SL", "trigger_price": 1183.6, "price": 1177.6,
             "validity": "DAY", "tag": "india-stop"}
ENTRY_PLAIN = "BUY 62 RELIANCE on NSE, intraday (MIS), limit 1194.80, good for today"
STOP_PLAIN = "SELL 62 RELIANCE on NSE, intraday (MIS), stop-loss: trigger 1183.60, limit 1177.60, good for today"
TICKET_PARTS = ["entry: " + ENTRY_PLAIN,
                "stop: " + STOP_PLAIN + "; placed only after the entry fills",
                "target 1203.60: watched by india-alerts, not placed (the exit is ticket.py flatten)",
                "max loss about ₹767.69 if the stop fills at 1183.60, charges included (₹1,139.58 at its limit "
                "1177.60; a gap below that leaves the stop unfilled)"]


def reel():
    with open(ORDERS_PATH, encoding="utf-8") as fh:
        return json.load(fh)["orders"]


def legs():
    return list(enumerate(reel(), 1))


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ticket.main(argv)
    return code, out.getvalue(), err.getvalue()


def first_json(text):
    start = text.index("{")
    depth = 0
    for i in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return json.loads(text[start:i + 1])


class ExplicitYes(unittest.TestCase):
    def yes(self, reply, batch=("RELIANCE", "TCS")):
        return ticket.parse_yes(reply, legs(), batch)[0]

    def test_plain_yes_counts(self):
        for r in ("yes", "Yes.", "YES", "y", "yes place it", "Yes, buy it", "yes RELIANCE", "yes 62 RELIANCE",
                  "yes 62", "yes 1183.60", "yes max loss 767.69", "yes target 1203.6", "yes ₹767.69", "yes 1194.80"):
            self.assertTrue(self.yes(r), r)

    def test_anything_else_does_not(self):
        for r in ("", "sure", "ok", "okay", "go", "do it", "yeah", "yep", "haan", "absolutely", "send it",
                  "yes to all", "yes, all of them", "yes to the rest", "yes both", "yes TCS", "yes 63",
                  "yes but make it 50", "yes?", "no", "not yes", "yes, wait", "yes if it is under 1190",
                  "yes, stop 1183.60"):
            self.assertFalse(self.yes(r), r)

    def test_amount_must_match_this_order(self):
        sell = {"role": "exit", "variety": "regular", "exchange": "NSE", "tradingsymbol": "TCS",
                "transaction_type": "SELL", "quantity": 3, "product": "MIS", "order_type": "LIMIT", "price": 2540.0,
                "validity": "DAY"}
        self.assertTrue(ticket.parse_yes("yes sell 3 TCS", sell)[0])
        self.assertFalse(ticket.parse_yes("yes sell 4", sell)[0])


class TicketShape(unittest.TestCase):
    def test_tool_input_is_the_kite_call(self):
        e, s = reel()
        self.assertEqual(ticket.tool_input(e), ENTRY_CALL)
        self.assertEqual(ticket.tool_input(s), STOP_CALL)
        for o in (e, s):
            self.assertTrue(set(ticket.tool_input(o)) <= set(ticket.KITE_FIELDS))
            self.assertEqual(ticket.fingerprint(o), ticket.fingerprint(ticket.tool_input(o)))
        self.assertIsInstance(ticket.tool_input(e)["quantity"], int)

    def test_a_group_is_one_ticket(self):
        with open(SPLIT_PATH, encoding="utf-8") as fh:
            split = json.load(fh)["orders"]
        self.assertEqual([[i for i, _ in l] for _, l in ticket.tickets(reel())], [[1, 2]])
        self.assertEqual(len(ticket.tickets(split)), 5)

    def test_plain_words(self):
        e, s = reel()
        self.assertEqual(ticket.plain(e), ENTRY_PLAIN)
        self.assertEqual(ticket.plain(s), STOP_PLAIN)
        rv = {1: {"max_loss": 767.69, "charges": 73.29, "loss_at_stop_limit": 1139.58}}
        self.assertEqual(ticket.plain_parts(legs(), rv), TICKET_PARTS)
        self.assertEqual(ticket.max_loss(e, s), (767.69, 1183.6, True))
        bare = dict(e)
        bare.pop("plan")
        self.assertEqual(ticket.max_loss(bare, s), (694.4, 1183.6, False))
        self.assertEqual(ticket.max_loss(bare), (None, None, False))

    def test_money_and_time(self):
        self.assertEqual(ticket.money(100297.6), "₹1,00,297.60")
        self.assertEqual(ticket.ist_iso(ticket.parse_now(NOW)), "2026-10-26T10:00:00+05:30")


class DesktopConfig(unittest.TestCase):
    def test_example_is_the_hosted_read_only_kite_server(self):
        path = os.path.join(ROOT, "india-broker", "claude_desktop_config.example.json")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        server = json.loads(text)["mcpServers"]["kite"]
        self.assertEqual((server["command"], server["args"]), ("npx", ["mcp-remote", "https://mcp.kite.trade/mcp"]))
        for secret in ("KITE_API_KEY", "KITE_API_SECRET", "access_token", "env"):
            self.assertNotIn(secret, text)


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        shutil.copy(ORDERS_PATH, self.orders)
        self.cfg = os.path.join(self.d, "config.json")
        shutil.copy(CFG_PATH, self.cfg)
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "broker.json")
        self.pf = os.path.join(self.d, "portfolio.json")
        self.none = os.path.join(self.d, "none")

    def tearDown(self):
        shutil.rmtree(self.d)

    def live(self):
        with open(self.cfg, encoding="utf-8") as fh:
            c = json.load(fh)
        c["paper"] = False
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump(c, fh)

    def args(self, *extra, **kw):
        return list(extra) + ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                              "--state", self.state, "--now", kw.get("now", NOW)]

    def fill(self, *extra, **kw):
        return run(self.args("fill", *extra, **kw) + ["--portfolio", self.pf, "--prices", self.none])

    def run_risk(self, now=NOW):
        with redirect_stdout(io.StringIO()):
            return check.main(["--orders", self.orders, "--config", self.cfg, "--portfolio", self.pf,
                               "--prices", self.none, "--state", self.state, "--now", now, "--out", self.risk])

    def state_doc(self):
        with open(self.state, encoding="utf-8") as fh:
            return json.load(fh)

    def portfolio(self):
        with open(self.pf, encoding="utf-8") as fh:
            return json.load(fh)

    def flatten(self, *extra, **kw):
        return run(["flatten", "--symbol", "RELIANCE", "--orders", self.orders, "--portfolio", self.pf,
                    "--prices", self.none, "--plan", PLAN_PATH, "--config", self.cfg, "--state", self.state,
                    "--now", kw.get("now", "2026-10-26 15:00")] + list(extra))

    def test_nothing_is_ready_before_the_risk_check(self):
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", text)
        self.assertNotIn("Reply \"yes\"", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("not ready", err)

    def test_the_paper_flow(self):
        self.assertEqual(self.run_risk(), 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertIn("TICKET 1 of 1, PAPER mode (nothing is sent to Kite), Zerodha Kite", text)
        for part in TICKET_PARTS:
            self.assertIn("  " + part + "\n", text)
        self.assertIn("READY: india-risk PASS for both orders, waiting for your yes", text)
        self.assertIn("Kite place_order input, stop, only after the entry fill is confirmed with get_orders / "
                      "get_positions (paper: shown, not sent)", text)
        self.assertEqual(first_json(text), ENTRY_CALL)
        for word in ('"plan"', '"why"', '"role"', '"group"', '"after"'):
            self.assertNotIn(word, text)
        self.assertIn("Reply \"yes\" to take this trade (the entry and its stop)", text)

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "sure"))
        self.assertEqual(code, 1)
        self.assertIn("Not recorded", text)
        self.assertFalse(os.path.exists(self.state))

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 0)
        self.assertIn("PAPER mode: do not call place_order, modify_order or cancel_order", text)
        self.assertNotIn("Now call place_order", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("APPROVED", err)
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed"))
        self.assertEqual(code, 1)
        self.assertIn("paper mode", err)

        code, _, err = self.fill("--index", "1", "--paper", "--price", "1195.00", now="2026-10-26 10:01")
        self.assertEqual(code, 1)
        self.assertIn("a LIMIT BUY at 1194.80 does not fill at 1195.00", err)
        code, text, _ = self.fill("--index", "1", "--price", "1194.20", now="2026-10-26 10:01")
        self.assertEqual(code, 1)
        code, text, _ = self.fill("--index", "1", "--paper", "--price", "1194.20", now="2026-10-26 10:01")
        self.assertEqual(code, 0)
        self.assertIn("Paper stop resting: " + STOP_PLAIN, text)
        st = self.state_doc()
        self.assertEqual([(r["status"], r["role"]) for r in st["orders"]],
                         [("approved", "entry"), ("approved", "stop"), ("placed", "entry"), ("filled", "entry"),
                          ("placed", "stop")])
        appr, plc = st["orders"][0], st["orders"][2]
        self.assertEqual((appr["reply"], appr["kind"], appr["max_loss"], appr["date"]), ("yes", "entry", 767.69, TODAY))
        self.assertEqual(appr["at"], "2026-10-26T10:00:00+05:30")
        self.assertEqual((plc["broker_order_id"], plc["max_loss"], plc["kind"], plc["paper"]),
                         ("paper", 767.69, "entry", True))
        self.assertEqual(st["orders"][4]["kind"], "stop")
        pf = self.portfolio()
        self.assertEqual((pf["cash"], pf["positions"][0]["shares"]), (25959.6, 62))

        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: placed", text)
        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertIn("2 placed today", text)
        self.assertIn("max loss ₹767.69", text)

        # india-risk now counts it: a second RELIANCE buy today is a duplicate, and ₹767.69 is used
        res = check.check(reel(), {"budget": 100000}, state=st, now=check.parse_now("2026-10-26 10:30"))
        self.assertIn("already placed today", " ".join(res["results"][0]["reasons"]))
        self.assertEqual((res["today"]["entries_placed"], res["today"]["loss_at_risk"]), (1, 767.69))

        # the paper stop fills, or the price gaps through its limit
        code, _, err = self.fill("--index", "1", "--leg", "stop", "--paper", "--price", "1176.00")
        self.assertEqual(code, 1)
        self.assertIn("below the stop's limit 1177.60", err)
        code, _, err = self.fill("--index", "1", "--leg", "stop", "--paper", "--price", "1190.00")
        self.assertIn("has not triggered", err)
        code, text, _ = self.fill("--index", "1", "--leg", "stop", "--paper", "--price", "1183.50")
        self.assertEqual(code, 0)
        self.assertEqual(self.portfolio(), {"cash": 99336.6, "positions": []})

    def test_flatten_by_flat_by_paper(self):
        self.run_risk()
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.fill("--index", "1", "--paper", "--price", "1194.20", now="2026-10-26 10:01")
        code, _, err = self.flatten()
        self.assertEqual(code, 1)
        self.assertIn("flatten needs the last price", err)
        code, text, _ = self.flatten("--price", "1199.40")
        self.assertEqual(code, 0)
        self.assertIn("SELL 62 RELIANCE on NSE, intraday (MIS), limit 1193.40, good for today (flat by 15:00 IST)", text)
        self.assertIn("replaced 2 earlier proposals", text)
        self.assertIn("₹50 + 18% GST", text)
        with open(self.orders, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["orders"][0]["role"], "exit")
        self.assertEqual(ticket.tool_input(doc["orders"][0]),
                         dict(ENTRY_CALL, transaction_type="SELL", price=1193.4, tag="india-flat"))
        late = "2026-10-26 15:00"
        self.assertEqual(self.run_risk(now=late), 0)       # after the 14:30 entry cutoff: an exit still passes
        with open(self.risk, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["results"][0]["kind"], "exit")
        code, text, _ = run(self.args("show", now=late))
        self.assertEqual(code, 0)
        self.assertIn("READY", text)
        self.assertIn("FIRST cancel the open stop order: " + STOP_PLAIN + " (a paper order)", text)
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes sell 62 RELIANCE", now=late))[0], 0)
        code, text, _ = self.fill("--index", "1", "--paper", "--price", "1199.00", now="2026-10-26 15:01")
        self.assertEqual(code, 0)
        self.assertEqual(self.portfolio(), {"cash": 100297.6, "positions": []})
        statuses = [(r["status"], r.get("role")) for r in self.state_doc()["orders"]][-3:]
        self.assertEqual(statuses, [("cancelled", "stop"), ("placed", "exit"), ("filled", "exit")])
        self.assertEqual(ticket.open_stops(self.state_doc(), "RELIANCE", TODAY), [])
        code, _, err = self.flatten("--price", "1199.40")
        self.assertEqual(code, 1)
        self.assertIn("nothing to flatten", err)

    def test_the_live_flow(self):
        self.live()
        self.assertEqual(self.run_risk(), 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("LIVE ACCOUNT, REAL MONEY", text)
        self.assertIn("Allow once places it", text)
        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes 62 RELIANCE"))
        self.assertEqual(code, 0)
        self.assertEqual(first_json(text), ENTRY_CALL)
        self.assertIn("Do NOT place the stop yet", text)
        code, _, err = self.fill("--index", "1", "--paper", "--price", "1194.20")
        self.assertIn("the config is live", err)
        code, _, err = run(self.args("result", "--index", "1", "--leg", "stop", "--status", "placed",
                                     "--order-id", "S1"))
        self.assertEqual(code, 1)
        self.assertIn("the stop goes in only after the entry fill is confirmed", err)
        code, text, _ = run(self.args("result", "--index", "1", "--leg", "entry", "--status", "placed",
                                      "--order-id", "E1", "--filled-qty", "62", "--filled-price", "1194.20",
                                      "--portfolio", self.pf, now="2026-10-26 10:01"))
        self.assertEqual(code, 0)
        self.assertIn("Confirm the fill with get_orders", text)
        self.assertEqual(first_json(text), STOP_CALL)
        code, _, _ = run(self.args("result", "--index", "1", "--leg", "stop", "--status", "placed",
                                   "--order-id", "S1", now="2026-10-26 10:02"))
        self.assertEqual(code, 0)
        st = self.state_doc()
        self.assertEqual([(r["status"], r["role"], r.get("broker_order_id")) for r in st["orders"]],
                         [("approved", "entry", None), ("approved", "stop", None), ("placed", "entry", "E1"),
                          ("filled", "entry", None), ("placed", "stop", "S1")])
        self.assertEqual(st["orders"][2]["max_loss"], 767.69)

        late = "2026-10-26 15:00"
        code, text, _ = self.flatten("--qty", "62", "--price", "1199.40", now=late)
        self.assertIn("FIRST cancel the open SL order (Kite order id S1)", text)
        self.run_risk(now=late)
        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes", now=late))
        self.assertIn("cancel_order with variety regular and order_id S1", text)
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed", "--order-id", "X1", now=late))
        self.assertEqual(code, 1)
        self.assertIn("pass --stop-cancelled", err)
        code, _, _ = run(self.args("result", "--index", "1", "--status", "placed", "--order-id", "X1",
                                   "--stop-cancelled", "--filled-qty", "62", "--filled-price", "1199.10",
                                   "--portfolio", self.pf, now=late))
        self.assertEqual(code, 0)
        self.assertEqual(ticket.open_stops(self.state_doc(), "RELIANCE", TODAY), [])
        self.assertEqual(self.portfolio()["positions"], [])

    def test_a_partial_fill_rewrites_the_stop(self):
        self.live()
        self.run_risk()
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        run(self.args("result", "--index", "1", "--status", "placed", "--order-id", "E1"))
        code, text, _ = self.fill("--index", "1", "--qty", "40", "--price", "1194.50", now="2026-10-26 10:02")
        self.assertEqual(code, 0)
        self.assertIn("The entry filled 40 of 62 shares", text)
        self.assertIn("may still be open at Kite", text)
        with open(self.orders, encoding="utf-8") as fh:
            self.assertEqual([o["quantity"] for o in json.load(fh)["orders"]], [62, 40])
        code, text, _ = run(self.args("show", "--index", "1", now="2026-10-26 10:03"))
        self.assertIn("was not in the last risk check", text)
        self.run_risk(now="2026-10-26 10:03")
        code, text, _ = run(self.args("show", "--index", "1", now="2026-10-26 10:03"))
        self.assertIn("READY: india-risk PASS for this order", text)
        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes 40", now="2026-10-26 10:03"))
        self.assertEqual(code, 0)
        self.assertEqual(first_json(text), dict(STOP_CALL, quantity=40))

    def test_flatten_with_the_broker_qty(self):
        code, text, _ = run(["flatten", "--symbol", "reliance.ns", "--qty", "62", "--price", "1199.40",
                             "--orders", self.orders, "--portfolio", self.none, "--plan", self.none,
                             "--config", self.cfg, "--state", self.none, "--now", NOW])
        self.assertEqual(code, 0)
        self.assertNotIn("Check it against get_positions", text)
        for q in ("0", "62.5"):
            self.assertEqual(run(["flatten", "--symbol", "RELIANCE", "--qty", q, "--price", "1199.4",
                                  "--orders", self.orders])[0], 1)

    def test_an_edited_order_is_not_ready(self):
        self.run_risk()
        with open(self.orders, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["orders"][0]["quantity"] = 63
        with open(self.orders, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("was not in the last risk check", text)

    def test_yesterdays_check_does_not_count(self):
        self.run_risk(now="2026-10-23 10:00")
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("not today", text)

    def test_blocked_orders_are_never_ready(self):
        self.assertEqual(self.run_risk(now="2026-10-26 09:20"), 1)
        code, text, _ = run(self.args("show", "--index", "1", now="2026-10-26 09:20"))
        self.assertIn("BLOCKED by india-risk: window: no new entries before 09:30", text)
        code, _, _ = run(self.args("approve", "--index", "1", "--reply", "yes", now="2026-10-26 09:20"))
        self.assertEqual(code, 1)

    def test_result_needs_an_approval_and_decline_is_logged(self):
        self.live()
        self.run_risk()
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed"))
        self.assertEqual(code, 1)
        self.assertIn("no yes recorded", err)
        code, text, _ = run(self.args("decline", "--index", "1", "--reply", "no thanks"))
        self.assertEqual(code, 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: declined", text)
        self.assertEqual([r["status"] for r in self.state_doc()["orders"]], ["declined", "declined"])

    def test_fill_sell_relieves_the_oldest_lot(self):
        pf = {"cash": 0, "positions": [{"ticker": "ITC", "shares": 30, "lots": [
            {"id": "ITC-1", "date": "2026-10-23", "shares": 20, "cost": 4000.0},
            {"id": "ITC-2", "date": "2026-10-26", "shares": 10, "cost": 2100.0}]}]}
        with open(self.pf, "w", encoding="utf-8") as fh:
            json.dump(pf, fh)
        code, text, _ = run(["fill", "--symbol", "ITC", "--side", "sell", "--qty", "25", "--price", "200",
                             "--date", TODAY, "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 0)
        pf = self.portfolio()
        self.assertEqual(pf["cash"], 5000.0)
        lots = pf["positions"][0]["lots"]
        self.assertEqual([l["id"] for l in lots], ["ITC-2"])
        self.assertEqual((lots[0]["shares"], lots[0]["cost"]), (5, 1050.0))
        self.assertEqual(self.state_doc()["orders"][-1]["status"], "filled")
        code, _, err = run(["fill", "--symbol", "ITC", "--side", "sell", "--qty", "50", "--price", "200",
                            "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
