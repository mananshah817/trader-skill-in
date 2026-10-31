import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

results = load("india-analyst", "results")
# Real NSE data fetched on 2026-10-31: the results lists, the event calendars,
# the announcements of the results days, and the XBRL filings trimmed to their
# non-dimensional profit and loss facts (balance sheet, segments, notes cut).
DIR = os.path.join(FIX, "india-analyst")
D = datetime.date


def read(name, mode="r"):
    path = os.path.join(DIR, name)
    if mode == "rb":
        with open(path, "rb") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = results.main(args)
    return code, out.getvalue(), err.getvalue()


NO_FILES = ["--watchlist", "/nonexistent/w.json", "--plan", "/nonexistent/p.json",
            "--portfolio", "/nonexistent/f.json"]


class Xbrl(unittest.TestCase):
    def test_one_quarter_from_an_integrated_filing(self):
        p = results.parse_xbrl(read("INTEGRATED_FILING_INDAS_1750537_30102026093254_WEB.xml", "rb"))
        self.assertEqual((p["start"], p["end"]), ("2026-07-01", "2026-09-30"))
        self.assertEqual(p["kind"], "company")
        v = p["values"]
        self.assertEqual(v["revenue"], 3328610000000)      # ₹3,32,861 crore
        self.assertEqual(v["net_profit"], 255480000000)    # owners' share, not 29,550 total
        self.assertEqual(p["concepts"]["net_profit"], "ProfitOrLossAttributableToOwnersOfParent")
        self.assertEqual(v["eps_diluted"], 18.88)
        self.assertEqual(p["meta"]["NatureOfReportStandaloneConsolidated"], "Consolidated")
        self.assertEqual(p["meta"]["DateOfBoardMeetingWhenFinancialResultsWereApproved"], "2026-10-30")

    def test_old_filings_label_the_year_to_date_with_the_quarter_dates(self):
        # INDAS_117297: "FourD" carries the nine months but the quarter's dates
        p = results.parse_xbrl(read("INDAS_117297_1348248_16012025081520.xml", "rb"))
        self.assertEqual(p["end"], "2024-12-31")
        self.assertEqual(p["values"]["revenue"], 2438650000000)  # not 71,55,630 (nine months)

    def test_a_bank(self):
        p = results.parse_xbrl(read("INTEGRATED_FILING_BANKING_1740913_17102026054812_WEB.xml", "rb"))
        self.assertEqual(p["kind"], "bank")
        d = results.derive(p)
        self.assertIsNone(d["revenue"])
        self.assertEqual(d["top_line_name"], "total income")
        self.assertEqual(d["nii"], 951316300000 - 506674300000)
        self.assertEqual(d["operating_label"], "operating profit before provisions")
        self.assertIsNone(d["gross_profit"])

    def test_not_xbrl(self):
        with self.assertRaises(results.ResultsError):
            results.parse_xbrl(b"<html>Access Denied</html")


class Reliance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        f = results.Fetcher(offline=DIR)
        cls.r, cls.info = results.company(f, "RELIANCE", quarters=5, today=D(2026, 10, 31))

    def test_latest_quarter(self):
        q = self.r["rows"][0]
        self.assertEqual(self.r["basis"], "consolidated")
        self.assertEqual(q["end"], "2026-09-30")
        self.assertEqual(q["fiscal"], "Q2 FY27")
        self.assertEqual(q["revenue_yoy"], 28.6)      # vs ₹2,58,898 crore
        self.assertEqual(q["net_profit_yoy"], 40.6)   # vs ₹18,165 crore
        self.assertEqual(q["eps_yoy"], 40.7)          # vs ₹13.42
        self.assertEqual(q["operating_margin"], 17.0)  # EBITDA (derived) ₹56,616 crore
        self.assertEqual(q["operating_profit"], 566160000000)
        self.assertEqual(q["gross_margin"], 32.2)
        self.assertEqual(q["net_margin"], 7.7)

    def test_five_quarters_from_both_lists(self):
        self.assertEqual([r["fiscal"] for r in self.r["rows"]],
                         ["Q2 FY27", "Q1 FY27", "Q4 FY26", "Q3 FY26", "Q2 FY26"])
        # the year-ago quarters of the last two come from the older list
        self.assertEqual(self.r["rows"][-1]["prior_end"], "2024-09-30")

    def test_bonus_issue_blanks_eps_growth(self):
        q2fy26 = self.r["rows"][-1]
        self.assertTrue(q2fy26["shares_changed"])     # 1:1 bonus, October 2024
        self.assertIsNone(q2fy26["eps_yoy"])
        self.assertEqual(q2fy26["net_profit_yoy"], 9.7)
        r = dict(self.r, rows=[q2fy26])
        self.assertTrue(any("bonus issue" in l for l in results.changes(r)))

    def test_trailing_four_quarters(self):
        t = self.r["ttm"]
        self.assertEqual(t["top_line"], 12128280000000)
        self.assertEqual(t["net_profit"], 821100000000)
        self.assertEqual(t["eps_diluted"], 60.68)

    def test_what_changed_in_words(self):
        lines = results.changes(self.r)
        self.assertIn("revenue from operations ₹3,32,861 crore (+28.6% vs a year earlier)", lines[0])
        self.assertIn("diluted EPS ₹18.88 (+40.7%)", lines[0])
        self.assertTrue(any(l.startswith("EBITDA (derived) ₹56,616 crore, 17.0% of revenue "
                                         "(+1.8 points") for l in lines))

    def test_latest_results_documents(self):
        lf = self.info["latest"]
        self.assertEqual(lf["board_meeting"], "2026-10-30")
        self.assertTrue(lf["xbrl"].endswith("INTEGRATED_FILING_INDAS_1750537_30102026093254_WEB.xml"))
        whats = [d["what"] for d in self.info["documents"]]
        self.assertIn("Press Release", whats)
        self.assertIn("Outcome of Board Meeting", whats)
        self.assertEqual(self.info["notes"], [])


class Banks(unittest.TestCase):
    def test_hdfc_bank(self):
        f = results.Fetcher(offline=DIR)
        r, info = results.company(f, "HDFCBANK", quarters=1, today=D(2026, 10, 31))
        q = r["rows"][0]
        self.assertEqual(r["kind"], "bank")
        self.assertEqual(q["top_line"], 1293586900000)
        self.assertEqual(q["top_line_yoy"], 9.1)
        self.assertEqual(q["nii_yoy"], 10.5)
        self.assertEqual(q["eps_diluted"], 13.41)
        self.assertIsNone(q["gross_margin"])
        out = results.render("HDFCBANK", r, info)
        self.assertIn("total inc", out)
        self.assertIn("44,464", out)
        self.assertIn("Exceptional items of -₹837 crore", out)
        self.assertEqual(info["documents"][0]["when"], "17-Oct-2026 13:56:22")


class ResultsDates(unittest.TestCase):
    def test_next_results_inside_or_outside_the_trade(self):
        ev = results.results_events(read("events-M&M.json"))
        self.assertEqual(ev[-1]["date"], "2026-11-05")
        day = results.trade_window(ev, "2026-06-30", D(2026, 10, 31))
        self.assertFalse(day["inside"])
        self.assertIn("05-Nov-2026, after a day trade on 31-Oct-2026", day["lines"][0])
        hold = results.trade_window(ev, "2026-06-30", D(2026, 10, 31), D(2026, 11, 6))
        self.assertTrue(hold["inside"])
        self.assertIn("INSIDE THIS TRADE", hold["lines"][0])
        self.assertIn("during market hours", hold["lines"][0])
        meeting_day = results.trade_window(ev, "2026-06-30", D(2026, 11, 5))
        self.assertTrue(meeting_day["inside"])

    def test_no_meeting_yet_means_unknown_not_safe(self):
        # the reel's day: Reliance gave notice of its 30 Oct meeting on 27 Oct
        ev = [e for e in results.results_events(read("events-RELIANCE.json"))
              if e["date"] < "2026-10-30"]
        w = results.trade_window(ev, "2026-06-30", D(2026, 10, 26))
        self.assertIsNone(w["inside"])
        self.assertIn("due by 14-Nov-2026", w["lines"][0])
        self.assertIn("2 working days' notice", w["lines"][0])
        self.assertEqual(results.due_date("2026-03-31"), "2026-05-30")  # 60 days for the year

    def test_board_meetings_list_is_the_fallback_shape(self):
        ev = results.results_events(read("boardmeetings-M&M.json"))
        self.assertEqual([e["date"] for e in ev], ["2026-07-30", "2026-11-05"])
        self.assertEqual(ev[-1]["announced"], "24-Sep-2026 10:56:15")

    def test_just_reported(self):
        ev = results.results_events(read("events-RELIANCE.json"))
        w = results.trade_window(ev, "2026-09-30", D(2026, 11, 2))
        self.assertTrue(any("just before this trade" in l for l in w["lines"]))


class Helpers(unittest.TestCase):
    def test_fiscal_labels(self):
        self.assertEqual(results.fiscal_label("2026-09-30", "2026-04-01"), "Q2 FY27")
        self.assertEqual(results.fiscal_label("2026-03-31"), "Q4 FY26")
        self.assertEqual(results.fiscal_label("2025-12-31"), "Q3 FY26")

    def test_rupees_in_crore_with_indian_grouping(self):
        self.assertEqual(results.crore(3328610000000), "3,32,861")
        self.assertEqual(results.rs_crore(-8370600000), "-₹837 crore")
        self.assertEqual(results.money(100000), "₹1,00,000")
        self.assertEqual(results.money(1234567.5), "₹12,34,567.50")

    def test_symbols(self):
        self.assertEqual(results.norm_symbol("reliance.ns"), "RELIANCE")
        self.assertEqual(results.norm_symbol("M&M"), "M&M")
        self.assertEqual(results.q("M&M"), "M%26M")
        with self.assertRaises(results.ResultsError):
            results.norm_symbol("RELIANCE.BO")
        with self.assertRaises(results.ResultsError):
            results.norm_symbol("not a symbol")

    def test_a_revision_wins(self):
        rows = [{"period_end": "2026-09-30", "basis": "consolidated", "filed": "30-Oct-2026 21:32:55",
                 "xbrl": "a"},
                {"period_end": "2026-09-30", "basis": "consolidated", "filed": "02-Nov-2026 10:00:00",
                 "xbrl": "b"},
                {"period_end": "2026-09-30", "basis": "standalone", "filed": "03-Nov-2026 10:00:00",
                 "xbrl": "c"}]
        self.assertEqual(results.choose_basis(rows), "consolidated")
        self.assertEqual(results.pick(rows, "consolidated")["2026-09-30"]["xbrl"], "b")
        self.assertEqual(results.choose_basis(rows[2:]), "standalone")


class Cli(unittest.TestCase):
    def test_report_and_metrics_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "trader", "metrics.json")
            code, out, err = run(["RELIANCE", "HDFCBANK", "--offline", DIR, "--today", "2026-10-31",
                                  "--quarters", "1", "--metrics", path] + NO_FILES)
            self.assertEqual(code, 0, err)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        rows = {r["symbol"]: r for r in data["companies"]}
        self.assertEqual(sorted(rows), ["HDFCBANK", "RELIANCE"])
        self.assertEqual(rows["RELIANCE"]["eps_ttm"], 60.68)
        self.assertEqual(rows["HDFCBANK"]["top_line"], "total income")
        self.assertIn("RELIANCE INDUSTRIES LIMITED (RELIANCE). Consolidated", out)
        self.assertIn("Not financial advice", out)
        self.assertIn("wrote", out)
        self.assertNotIn(chr(0x2014), out)

    def test_hold_until_flags_results(self):
        code, out, err = run(["RELIANCE", "--offline", DIR, "--today", "2026-10-26",
                              "--hold-until", "2026-10-30", "--quarters", "1"] + NO_FILES)
        self.assertEqual(code, 0, err)
        self.assertIn("INSIDE THIS TRADE: the results board meeting on 30-Oct-2026", out)

    def test_json_output(self):
        code, out, err = run(["RELIANCE", "--offline", DIR, "--json", "--quarters", "2",
                              "--today", "2026-10-31"] + NO_FILES)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(len(data[0]["result"]["rows"]), 2)
        self.assertEqual(data[0]["result"]["rows"][0]["net_profit_concept"],
                         "ProfitOrLossAttributableToOwnersOfParent")

    def test_unknown_symbol_and_no_symbols(self):
        code, _, err = run(["ZZZZ", "--offline", DIR] + NO_FILES)
        self.assertEqual(code, 2)
        self.assertIn("ZZZZ", err)
        code, _, err = run(["--offline", DIR] + NO_FILES)
        self.assertEqual(code, 2)
        self.assertIn("no symbols", err)

    def test_symbols_from_the_watchlist_and_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            wl = os.path.join(tmp, "watchlist.json")
            with open(wl, "w", encoding="utf-8") as fh:
                json.dump({"created_by": "india-scanner", "hits": [{"symbol": "RELIANCE"}]}, fh)
            plan = os.path.join(tmp, "plan.json")
            with open(plan, "w", encoding="utf-8") as fh:
                json.dump({"created_by": "india-exit", "symbol": "HDFCBANK.NS"}, fh)
            self.assertEqual(results.watch_from_files("/nonexistent.json", wl, plan),
                             ["RELIANCE", "HDFCBANK"])


if __name__ == "__main__":
    unittest.main()
