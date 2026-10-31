import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

insider = load("india-insider", "insider")
# Real NSE data fetched on 2026-10-31: bulk.csv and block.csv (30-Oct-2026), the
# range CSVs for 26 to 30 Oct, the PIT filings list for 29 and 30 Oct, nine of
# its XML disclosures, NATCOPHARM's October list and four rows of NSE's older
# insider list for RELIANCE. Nothing is synthetic.
DIR = os.path.join(FIX, "india-insider")
NO_FILES = ["--watchlist", "/nonexistent/w.json", "--plan", "/nonexistent/p.json",
            "--portfolio", "/nonexistent/f.json"]


def text(name):
    with open(os.path.join(DIR, name), encoding="utf-8-sig") as fh:
        return fh.read()


def raw(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = insider.main(args)
    return code, out.getvalue(), err.getvalue()


class Deals(unittest.TestCase):
    def test_latest_bulk_file(self):
        deals = insider.parse_deals(text("bulk.csv"), "bulk")
        self.assertEqual(len(deals), 88)
        first = deals[0]
        self.assertEqual((first["date"], first["symbol"], first["side"]), ("2026-10-30", "AEROFLEX", "BUY"))
        self.assertEqual(first["client"], "QE SECURITIES LLP")
        self.assertEqual((first["qty"], first["price"]), (670611, 540.12))

    def test_range_csv_has_a_bom_padded_headers_and_indian_commas(self):
        deals = insider.parse_deals(text("bulk-range-26-10-2026-30-10-2026.csv"), "bulk")
        self.assertEqual(len(deals), 89 + 70 + 57 + 71 + 88)  # 26 to 30 Oct
        apollo = deals[0]
        self.assertEqual(apollo["symbol"], "APOLLO")
        self.assertEqual(apollo["qty"], 2641960)      # "26,41,960"
        self.assertTrue(apollo["round_trip"])         # Jump Trading bought and sold
        self.assertEqual({d["date"] for d in deals},
                         {"2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"})

    def test_a_block_deal_in_both_lists_is_one_trade(self):
        f = insider.Fetcher(offline=DIR)
        deals = insider.load_deals(f, ("bulk", "block"))
        tencent = [d for d in deals if d["client"] == "TENCENT CLOUD EUROPE B.V."]
        self.assertEqual(len(tencent), 1)
        t = tencent[0]
        self.assertEqual((t["kind"], t["side"], t["qty"], t["price"]), ("block", "SELL", 54347662, 180.65))
        self.assertTrue(t["also_bulk"])
        self.assertEqual(insider.value_text(t["value"]), "₹981.79 crore")
        buys = [d for d in deals if d["symbol"] == "SWIGGY" and d["kind"] == "block" and d["side"] == "BUY"]
        self.assertEqual(sum(d["qty"] for d in buys), t["qty"])   # many buyers, one seller

    def test_filters(self):
        f = insider.Fetcher(offline=DIR)
        deals = insider.load_deals(f, ("bulk", "block"))
        big = insider.filter_deals(deals, min_crore=200)
        self.assertEqual(len(big), 7)
        mf = insider.filter_deals(deals, client="mutual fund", side="buy")
        self.assertTrue(mf and all("MUTUAL FUND" in d["client"] and d["side"] == "BUY" for d in mf))
        self.assertEqual({d["symbol"] for d in insider.filter_deals(deals, {"SWIGGY"})}, {"SWIGGY"})

    def test_render_flags_your_stocks(self):
        d = insider.parse_deals(text("block.csv"), "block")[1]
        line = insider.render_deal(d, watch={"SWIGGY"})
        self.assertIn("SOCIETE GENERALE", line)
        self.assertIn("2,37,40,000 @ ₹180.65 = ₹428.86 crore", line)
        self.assertIn("<- SWIGGY is on your list", line)

    def test_cli_range(self):
        code, out, err = run(["deals", "--offline", DIR, "--days", "5", "--today", "2026-10-30",
                              "--symbol", "SWIGGY"] + NO_FILES)
        self.assertEqual(code, 0, err)
        self.assertIn("26-Oct-2026 to 30-Oct-2026", out)
        self.assertIn("TENCENT CLOUD EUROPE", out)
        self.assertIn("Not financial advice", out)
        self.assertIn("after the close", out)
        self.assertNotIn(chr(0x2014), out)

    def test_bad_columns(self):
        with self.assertRaises(insider.InsiderError):
            insider.parse_deals("a,b,c\n1,2,3\n", "bulk")


class PitFilings(unittest.TestCase):
    def test_the_list_is_metadata(self):
        data = json.loads(text("pit-29-10-2026-30-10-2026.json"))
        filings = insider.parse_pit_list(data)
        self.assertEqual(len(filings), 22)
        f = filings[0]
        self.assertEqual((f["symbol"], f["app_id"], f["broadcast"]),
                         ("AURUM", "4103", "30-Oct-2026 19:22:30"))
        self.assertEqual(f["regulation"], "Regulation 7 (2)")
        self.assertTrue(f["xml"].endswith("IT_12987_WebXMLFile_20261030_192230003.xml"))
        self.assertEqual(sum(1 for f in filings if f["regulation"] == "Regulation 7 (3)"), 1)  # TITAN

    def test_a_promoter_market_purchase(self):
        rep = insider.parse_pit_xml(raw("IT_17571_WebXMLFile_20261029_155944284.xml"),
                                    "29-Oct-2026 15:59:44")
        self.assertEqual(rep["meta"]["Symbol"], "WESTLIFE")
        self.assertEqual(len(rep["rows"]), 2)
        big = rep["rows"][1]
        self.assertEqual(big["person"], "Horizon Impex Private Limited")
        self.assertEqual(big["category"], "Promoter")
        self.assertEqual((big["qty"], big["value"]), (1065000, 638994184))
        self.assertEqual((big["type"], big["mode"]), ("Buy", "Market Purchase"))
        self.assertTrue(big["market"])
        self.assertEqual((big["from"], big["to"], big["intimated"]),
                         ("2026-10-27", "2026-10-28", "2026-10-29"))
        self.assertEqual((big["before_pct"], big["after_pct"]), (30.49, 31.18))  # filed as 0.3049
        self.assertEqual(big["days_to_disclose"], 1)
        self.assertFalse(big["slow"])
        line = insider.describe(big)
        self.assertIn("promoter Horizon Impex Private Limited: market purchase of 10,65,000 shares, "
                      "₹63.90 crore", line)
        self.assertIn("(30.49% -> 31.18%)", line)

    def test_allotments_are_not_market_trades(self):
        rep = insider.parse_pit_xml(raw("IT_1299_WebXMLFile_20261029_171729430.xml"))
        self.assertEqual(len(rep["rows"]), 25)
        self.assertFalse(any(r["market"] for r in rep["rows"]))
        self.assertIn("allotment (not a market trade)", insider.describe(rep["rows"][0]))
        promoters = insider.filter_rows(rep["rows"], ["promoter"])
        self.assertEqual(len(promoters), 11)
        self.assertEqual(insider.filter_rows(rep["rows"], ["kmp"])[0]["person"], "CHEKURI VENKAT RAMESH")
        self.assertEqual(insider.filter_rows(rep["rows"], market_only=True), [])

    def test_sales_pledges_adrs_and_bad_values(self):
        sale = insider.parse_pit_xml(raw("IT_14283_WebXMLFile_20261030_170021758.xml"))["rows"][0]
        self.assertEqual((sale["category"], sale["type"], sale["mode"]), ("Promoter", "Sell", "Market Sale"))
        self.assertEqual(len(insider.filter_rows([sale], side="sell")), 1)
        dmart = insider.parse_pit_xml(raw("IT_14711_WebXMLFile_20261030_175859666.xml"))["rows"]
        self.assertEqual(len(dmart), 15)
        self.assertIn("pledge invoke, invocation of pledge (not a market trade)", insider.describe(dmart[0]))
        adr = insider.parse_pit_xml(raw("IT_180_WebXMLFile_20261029_120656926.xml"))["rows"][0]
        self.assertEqual(adr["instrument"], "ADRs")
        typo = insider.parse_pit_xml(raw("IT_20276_WebXMLFile_20261029_161105248.xml"))["rows"][0]
        self.assertTrue(typo["value_looks_wrong"])   # ₹20 for 1,26,000 shares
        self.assertIn("looks wrong", insider.describe(typo))

    def test_slow_disclosures(self):
        rows = insider.parse_pit_archive(json.loads(text("pit-archive-RELIANCE.json")))
        self.assertEqual(len(rows), 4)
        late = rows[2]  # traded 02-Feb-2026, on NSE 09-Feb-2026
        self.assertEqual(late["days_to_disclose"], 5)
        self.assertTrue(late["slow"])
        self.assertEqual(rows[0]["category"], "Other")
        self.assertEqual(insider.trading_days_after("2026-10-30", "2026-10-31"), 0)  # a Saturday
        self.assertEqual(insider.trading_days_after("2026-10-01", "2026-10-05"), 1)  # 2 Oct holiday

    def test_cli_category_reads_the_xml(self):
        code, out, err = run(["insider", "--offline", DIR, "--today", "2026-10-30", "--days", "2",
                              "--symbol", "WESTLIFE", "--symbol", "SAKAR", "--category", "promoter",
                              "--market-only", "--tickers", "SAKAR"] + NO_FILES)
        self.assertEqual(code, 0, err)
        self.assertIn("2 filings (details read from each XML)", out)
        self.assertTrue(out.index("SAKAR") < out.index("WESTLIFE"))   # your stocks first
        self.assertIn("<- SAKAR is on your list", out)
        self.assertIn("market sale of 45,000 shares", out)
        self.assertIn("within 2 trading days", out)
        self.assertIn("Not financial advice", out)

    def test_cli_one_symbol_uses_the_symbol_query(self):
        code, out, err = run(["insider", "--offline", DIR, "--today", "2026-10-31", "--days", "31",
                              "--symbol", "NATCOPHARM.NS", "--read"] + NO_FILES)
        self.assertEqual(code, 0, err)
        self.assertIn("NATCO PHARMA LIMITED", out)
        self.assertIn("VENKAIAH CHOWDARY NANNAPANENI", out)

    def test_cli_archive_and_read(self):
        code, out, _ = run(["insider", "--offline", DIR, "--symbol", "RELIANCE", "--archive"] + NO_FILES)
        self.assertEqual(code, 0)
        self.assertIn("stops in February 2026", out)
        code, out, _ = run(["read", os.path.join(DIR, "IT_233_WebXMLFile_20261029_172100958.xml"), "--json"])
        self.assertEqual(code, 0)
        rep = json.loads(out)
        self.assertEqual(rep["meta"]["DisclosureUnderRegulation"], "Regulation 7 (3)")
        self.assertEqual(rep["rows"][0]["person"], "Titan Employee Stock Option Trust")
        code, _, err = run(["insider", "--offline", DIR, "--archive"] + NO_FILES)
        self.assertEqual(code, 2)
        self.assertIn("needs --symbol", err)


class Check(unittest.TestCase):
    def test_alerts_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "state", "insider.json")
            base = ["check", "--offline", DIR, "--today", "2026-10-30", "--days", "2",
                    "--state", state] + NO_FILES
            code, out, err = run(base + ["--tickers", "SWIGGY"])
            self.assertEqual(code, 0, err)
            self.assertIn("NEW bulk/block deals (98)", out)
            self.assertIn("NEW insider disclosures (22)", out)
            first = out.split("NEW bulk/block deals")[1].splitlines()[1]
            self.assertIn("SWIGGY is on your list", first)
            with open(state, encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertEqual(len(saved["seen"]), 98 + 22)
            self.assertIn("pit|4103|IT_12987_WebXMLFile_20261030_192230003.xml", saved["seen"])
            code, out, _ = run(base)
            self.assertEqual(code, 1)
            self.assertIn("Nothing new since", out)

    def test_mine_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "insider.json")
            code, out, err = run(["check", "--offline", DIR, "--today", "2026-10-30", "--days", "2",
                                  "--state", state, "--mine", "--tickers", "WESTLIFE", "--read"]
                                 + NO_FILES)
            self.assertEqual(code, 0, err)
            self.assertNotIn("bulk/block", out)
            self.assertIn("NEW insider disclosures (1)", out)
            self.assertIn("market purchase of 10,65,000 shares", out)

    def test_dry_run_keeps_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "insider.json")
            run(["check", "--offline", DIR, "--today", "2026-10-30", "--days", "2", "--state", state,
                 "--dry-run"] + NO_FILES)
            self.assertFalse(os.path.exists(state))

    def test_old_entries_are_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.json")
            import datetime
            insider.save_state(path, {"seen": {"a": "2026-08-01", "b": "2026-10-30"}},
                               datetime.date(2026, 10, 31))
            self.assertEqual(sorted(insider.load_state(path)["seen"]), ["b"])

    def test_mine_without_a_list_is_an_error(self):
        code, _, err = run(["deals", "--offline", DIR, "--mine"] + NO_FILES)
        self.assertEqual(code, 2)
        self.assertIn("--mine", err)


class Helpers(unittest.TestCase):
    def test_money_and_symbols(self):
        self.assertEqual(insider.value_text(2027042), "₹20,27,042")
        self.assertEqual(insider.value_text(23945600000), "₹2,394.56 crore")
        self.assertEqual(insider.money(1234567.5), "₹12,34,567.50")
        self.assertEqual(insider.norm_symbol("m&m"), "M&M")
        self.assertEqual(insider.nse_date("30-OCT-2026"), "2026-10-30")
        self.assertEqual(insider.nse_date("29-Oct-2026 17:17:29"), "2026-10-29")
        with self.assertRaises(insider.InsiderError):
            insider.norm_symbol("$$$")

    def test_your_symbols_from_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            wl = os.path.join(tmp, "watchlist.json")
            with open(wl, "w", encoding="utf-8") as fh:
                json.dump({"created_by": "india-scanner", "hits": [{"symbol": "SWIGGY"}]}, fh)
            pf = os.path.join(tmp, "portfolio.json")
            with open(pf, "w", encoding="utf-8") as fh:
                json.dump({"positions": [{"symbol": "RELIANCE.NS", "shares": 10}]}, fh)
            self.assertEqual(insider.watch_from_files(pf, wl, "/nonexistent.json"),
                             {"SWIGGY", "RELIANCE"})


if __name__ == "__main__":
    unittest.main()
