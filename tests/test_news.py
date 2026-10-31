import datetime
import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

news = load("india-news", "news")
# Real data fetched on 2026-10-31 (Saturday): Yahoo Finance RSS for INFY.NS, TCS.NS and RELIANCE.NS (the
# RELIANCE feed came back empty, as it often does), NSE corporate announcements for RELIANCE, INFY and
# HDFCBANK for 1-31 Oct 2026, and the INFY.NS 5-minute chart of Friday 30 Oct. chart-RELIANCE.json is the
# reel's chart as at 10:00 IST on Monday 26 Oct, rebuilt from the real 5-minute bars (see its "_note").
DIR = os.path.join(FIX, "india-news")
NOW = "2026-10-31 12:00"
UTC = datetime.timezone.utc


def raw(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = news.main(args)
    return code, out.getvalue(), err.getvalue()


def nse(sym):
    return news.parse_nse(raw("nse-%s.json" % sym), sym)


def by_id(items, seq):
    return next(i for i in items if i["id"] == "nse:%s" % seq)


class Feeds(unittest.TestCase):
    def test_yahoo_rss(self):
        items = news.parse_rss(raw("yahoo-INFY.xml"), "INFY")
        self.assertEqual(len(items), 15)
        first = items[0]
        self.assertEqual(first["title"], "Asian Equities Traded in the US as American Depositary Receipts Fall "
                                         "Sharply in Friday Trading")
        self.assertEqual(first["time"], datetime.datetime(2026, 10, 30, 14, 40, 16, tzinfo=UTC))
        self.assertTrue(first["link"].startswith("https://finance.yahoo.com/"))
        self.assertTrue(first["id"].startswith("rss:"))

    def test_an_empty_yahoo_feed_is_normal(self):
        self.assertEqual(news.parse_rss(raw("yahoo-RELIANCE.xml"), "RELIANCE"), [])

    def test_nse_announcements(self):
        items = nse("RELIANCE")
        self.assertEqual(len(items), 17)
        first = items[0]
        self.assertEqual((first["id"], first["source"], first["kind"], first["form"], first["title"]),
                         ("nse:106837413", "NSE", "filing", "Investor Presentation", "Investor Presentation"))
        self.assertEqual(first["company"], "Reliance Industries Limited")
        self.assertEqual(first["time"].isoformat(), "2026-10-30T14:17:12+00:00")   # 19:47:12 IST
        self.assertTrue(first["link"].startswith("https://nsearchives.nseindia.com/corporate/"))
        self.assertEqual(by_id(items, 106827783)["title"],
                         "General Updates: Initial Public Offer of Jio Platforms Limited")
        self.assertTrue(by_id(items, 106837394)["important"])                       # outcome: the results
        self.assertFalse(by_id(items, 106820709)["important"])                      # newspaper clipping
        self.assertEqual(news.parse_nse(b"[]", "ZZZZ"), [])                          # NSE's answer for no symbol
        with self.assertRaises(news.NewsError):
            news.parse_nse(b"<html>Access Denied</html>", "RELIANCE")

    def test_the_exchange_boilerplate_is_trimmed(self):
        titles = [i["title"] for i in nse("INFY")]
        self.assertIn("Updates: Earnings Call Transcript", titles)
        self.assertIn("Updates: Investor Conference", titles)
        self.assertIn("Record Date: Record date for the purpose of Dividend is 03-Nov-2026.", titles)

    def test_times(self):
        self.assertEqual(news.parse_time("Fri, 30 Oct 2026 14:40:16 +0000").hour, 14)
        self.assertEqual(news.parse_time("2026-10-30T19:47:12+05:30").hour, 14)
        self.assertEqual(news.parse_time("30-Oct-2026 19:47:12"),
                         datetime.datetime(2026, 10, 30, 14, 17, 12, tzinfo=UTC))   # NSE times are IST
        self.assertIsNone(news.parse_time("soon"))

    def test_parse_now_is_india_time(self):
        self.assertEqual(news.parse_now("2026-10-26 10:00"), datetime.datetime(2026, 10, 26, 4, 30, tzinfo=UTC))
        self.assertEqual(news.parse_now("2026-10-26 10:00 IST"), news.parse_now("2026-10-26T04:30:00Z"))
        self.assertEqual(news.to_ist(news.parse_now("09:30")).strftime("%H:%M"), "09:30")
        with self.assertRaises(news.NewsError):
            news.parse_now("tomorrow")

    def test_symbols_and_urls(self):
        self.assertEqual(news.yahoo_symbol("reliance.ns"), "RELIANCE.NS")
        self.assertEqual(news.yahoo_symbol("M&M"), "M%26M.NS")
        self.assertEqual(news.YAHOO % news.yahoo_symbol("INFY"),
                         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=INFY.NS&region=US&lang=en-US")
        self.assertEqual(news.nse_url("M&M", news.parse_now("2026-10-26 10:00"), 48),
                         "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=M%26M"
                         "&from_date=12-10-2026&to_date=26-10-2026")
        self.assertIn("from_date=21-07-2026", news.nse_url("INFY", news.parse_now("2026-10-26 10:00"), 24 * 97))
        with self.assertRaises(news.NewsError):
            news.norm_symbol("BRK.B")

    def test_ist_is_a_fixed_offset(self):
        for month in (1, 7, 11):
            utc = datetime.datetime(2026, month, 15, 3, 45, tzinfo=UTC)
            self.assertEqual(news.to_ist(utc).strftime("%H:%M"), "09:15")

    def test_money(self):
        self.assertEqual(news.money(1193.6), "₹1,193.60")
        self.assertEqual(news.money(1234567.5), "₹12,34,567.50")
        self.assertEqual(news.money(1190), "₹1,190")


class Results(unittest.TestCase):
    def test_dates_in_announcement_text(self):
        found = [d.isoformat() for _, d in news.find_dates(
            "held on Friday, October 30, 2026; record date 03-Nov-2026; on 30th October, 2026; 30/10/2026; "
            "quarter ended September 30, 2026")]
        self.assertEqual(found, ["2026-10-30", "2026-11-03", "2026-10-30", "2026-10-30", "2026-09-30"])

    def test_a_results_board_meeting(self):
        rel = nse("RELIANCE")
        self.assertEqual(news.results_meeting(by_id(rel, 106830931)), datetime.date(2026, 10, 30))
        self.assertIsNone(news.results_meeting(by_id(rel, 106837394)))      # the outcome, not an intimation
        self.assertIsNone(news.results_meeting(by_id(rel, 106823937)))      # credit rating
        infy = nse("INFY")
        dates = {news.results_meeting(i) for i in infy} - {None}
        self.assertEqual(dates, {datetime.date(2026, 10, 23)})              # "to Announce ... Results on Oct 23"

    def test_the_earliest_announcement_wins(self):
        items = nse("RELIANCE")
        now = news.parse_now("2026-10-28 10:00")
        got = news.results_dates(items, now, {"RELIANCE": "2026-10-30"})
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["date"], got[0]["results_day"], got[0]["announced"]),
                         ("2026-10-30", True, "2026-10-27 19:17 IST"))
        self.assertEqual(news.results_dates(items, news.parse_now("2026-10-27 10:00"), {}), [])   # not yet out
        self.assertEqual(news.results_dates(items, news.parse_now("2026-10-31 10:00"), {}), [])   # passed


class Tagging(unittest.TestCase):
    def test_touches_and_tags(self):
        items = news.parse_rss(raw("yahoo-TCS.xml"), "TCS")
        watch = {"TCS": "Tata Consultancy", "INFY": "Infosys"}
        news.tag(items, watch)
        by = {i["title"][:24]: i for i in items}
        self.assertEqual(by["Why Infosys Can Shrug Of"]["touches"], ["INFY", "TCS"])
        self.assertEqual(by["TCS shares rise as AI re"]["touches"], ["TCS"])
        self.assertEqual(by["Global Customer Technica"]["touches"], ["INFY", "TCS"])

    def test_nse_filing_tags(self):
        rel = news.tag(nse("RELIANCE"), {"RELIANCE": None})
        self.assertIn("credit rating", by_id(rel, 106823937)["tags"])
        self.assertIn("fund raising", by_id(rel, 106827783)["tags"])                # the Jio Platforms IPO
        self.assertNotIn("risk", by_id(rel, 106833561)["tags"])                    # a "strike off" is not a strike
        self.assertEqual(by_id(rel, 106830931)["tags"][:3], ["results date", "results", "board meeting"])
        self.assertIn("board outcome", by_id(rel, 106837394)["tags"])
        hdfc = news.tag(nse("HDFCBANK"), {"HDFCBANK": None})
        md = [i for i in hdfc if "Change in Managing Director" in i["summary"]][0]
        self.assertTrue(md["important"])
        self.assertIn("people", md["tags"])
        infy = news.tag(nse("INFY"), {"INFY": None})
        self.assertIn("dividend", [i for i in infy if i["form"] == "Record Date"][0]["tags"])

    def test_dedupe_merges_the_same_story(self):
        a = news.parse_rss(raw("yahoo-INFY.xml"), "INFY")
        b = news.parse_rss(raw("yahoo-INFY.xml"), "TCS")
        items = news.dedupe(news.tag(a + b, {"INFY": None, "TCS": None}))
        self.assertEqual(len(items), 15)
        self.assertEqual(items[0]["touches"], ["INFY", "TCS"])
        rel = news.dedupe(news.tag(nse("RELIANCE"), {"RELIANCE": None}))
        self.assertEqual(len([i for i in rel if i["form"] == "Outcome of Board Meeting"]), 1)   # two PDFs, one story

    def test_the_same_filing_name_from_two_companies_stays_two(self):
        a = dict(nse("RELIANCE")[0])
        b = dict(a, id="nse:x", ticker="INFY", link="https://nsearchives.nseindia.com/other.pdf")
        self.assertEqual(len(news.dedupe(news.tag([a, b], {"RELIANCE": None, "INFY": None}))), 2)

    def test_day_trading_tags(self):
        items = [{"id": "x", "source": "Yahoo Finance", "kind": "headline", "ticker": "SUNPHARMA",
                  "title": "Trading halted in XYZ after USFDA warning letter; Nifty futures fall as rupee slides",
                  "summary": "", "link": "", "time": None}]
        tags = news.tag(items, {"SUNPHARMA": None})[0]["tags"]
        for t in ("halt", "regulatory", "macro"):
            self.assertIn(t, tags)


class FakeResp(object):
    def __init__(self, body):
        self.body, self.headers = body, {}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeNse(object):
    """Stands in for the cookie-jar opener: answers each request with the next (code, body)."""

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def open(self, req, timeout=None):
        self.calls.append((req.full_url, req.get_header("User-agent"), req.get_header("Referer"),
                           req.get_header("Accept")))
        code, body = self.answers.pop(0)
        if code != 200:
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(b""))
        return FakeResp(body)


class NseFetch(unittest.TestCase):
    URL = news.nse_url("RELIANCE", news.parse_now("2026-10-26 10:00"), 48)

    def fetcher(self, answers):
        f = news.Fetcher(pause=0)
        f.nse_opener = FakeNse(answers)
        return f

    def test_browser_headers(self):
        f = self.fetcher([(200, raw("nse-RELIANCE.json"))])
        self.assertEqual(len(news.parse_nse(f.get(self.URL, "x", nse=True), "RELIANCE")), 17)
        url, ua, ref, accept = f.nse_opener.calls[0]
        self.assertEqual(url, self.URL)
        self.assertEqual(ua, "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/130.0 Safari/537.36")
        self.assertEqual((ref, accept), ("https://www.nseindia.com/", "application/json, text/plain, */*"))

    def test_one_retry_after_priming_the_cookies(self):
        f = self.fetcher([(403, b""), (403, b""), (200, b"[]")])     # the home page itself often says 403
        self.assertEqual(f.get(self.URL, "x", nse=True), b"[]")
        self.assertEqual([c[0] for c in f.nse_opener.calls], [self.URL, "https://www.nseindia.com/", self.URL])

    def test_an_html_answer_counts_as_a_refusal(self):
        f = self.fetcher([(200, b"<HTML><HEAD><TITLE>Access Denied</TITLE>"), (200, b"<html>"), (200, b"[]")])
        self.assertEqual(f.get(self.URL, "x", nse=True), b"[]")

    def test_then_a_plain_error_and_no_loop(self):
        f = self.fetcher([(403, b""), (200, b"<html>"), (403, b"")])
        with self.assertRaises(news.NewsError) as cm:
            f.get(self.URL, "x", nse=True)
        self.assertIn("NSE refused the request (HTTP 403, then HTTP 403", str(cm.exception))
        self.assertIn("try again in a minute", str(cm.exception))
        self.assertEqual(len(f.nse_opener.calls), 3)


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state", "news.json")
        self.none = os.path.join(self.tmp.name, "none.json")
        self.base = ["--offline", DIR, "--state", self.state, "--now", NOW,
                     "--portfolio", self.none, "--watchlist", self.none, "--plan", self.none]

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, **kw):
        a = list(self.base)
        for k, v in kw.items():
            a[a.index("--" + k) + 1] = v
        return a

    def test_new_items_once_newest_first(self):
        code, out, err = run(["--tickers", "INFY", "HDFCBANK", "--hours", "48", "--json"] + self.base)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        times = [i["time"] for i in data["items"]]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual(len(data["items"]), 7)  # 3 INFY headlines, 3 INFY and 1 HDFCBANK NSE filings
        self.assertEqual({i["source"] for i in data["items"]}, {"Yahoo Finance", "NSE"})
        code, out, _ = run(["--tickers", "INFY", "HDFCBANK", "--hours", "48"] + self.base)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)

    def test_the_reel_reliance_morning(self):
        # Monday 26 Oct 2026, RELIANCE broke its opening range at 10:00 IST: the check at 10:00
        code, out, err = run(["--tickers", "RELIANCE", "--dry-run"] + self.args(now="2026-10-26 10:00"))
        self.assertEqual(code, 0, err)
        self.assertIn("  2026-10-24 21:48 IST  RELIANCE    NSE           General Updates: Initial Public Offer "
                      "of Jio Platforms Limited", out)
        self.assertNotIn("2026-10-27", out)          # announced after the pinned 10:00 clock
        self.assertIn("note: Yahoo Finance has no headlines for RELIANCE.NS (its feed is often empty for Indian "
                      "stocks: normal, not an error)", out)
        self.assertIn("No results date in NSE announcements of the last 14 days for RELIANCE", out)
        self.assertIn("RELIANCE   ₹1,193.60 +0.30% vs yesterday's close ₹1,190 at 10:00 IST  \n", out)
        self.assertIn("Checked 2026-10-26 10:00 IST", out)
        self.assertIn("Not financial advice", out)
        self.assertNotIn(chr(0x2014), out)

    def test_results_day_inside_the_trade(self):
        code, out, err = run(["--tickers", "RELIANCE", "--hours", "24", "--dry-run", "--no-moves",
                              "--trade-date", "2026-10-30"] + self.args(now="2026-10-28 10:00"))
        self.assertEqual(code, 0, err)
        self.assertIn("RESULTS DAY  RELIANCE: financial results due Fri 2026-10-30, the day of your trade (NSE "
                      "announcement 2026-10-27 19:17 IST). Results can come out during the session, and the "
                      "price can jump past any stop.", out)
        self.assertIn("! 2026-10-27 19:17 IST  RELIANCE    NSE           Analysts/Institutional Investor Meet/Con. "
                      "Call Updates: The meeting of the Board of Directors", out)
        # the trade date from the plan
        plan = os.path.join(self.tmp.name, "plan.json")
        with open(plan, "w", encoding="utf-8") as fh:
            json.dump({"created_by": "india-exit", "symbol": "RELIANCE", "date": "2026-10-30"}, fh)
        code, out, err = run(["--dry-run", "--no-moves", "--json"] + self.args(now="2026-10-29 09:00", plan=plan))
        res = json.loads(out)["results"]
        self.assertEqual([(r["ticker"], r["date"], r["results_day"]) for r in res],
                         [("RELIANCE", "2026-10-30", True)])
        # a trade today, results tomorrow: said, not flagged
        code, out, err = run(["--tickers", "RELIANCE", "--dry-run", "--no-moves"] + self.args(now="2026-10-29 09:00"))
        self.assertIn("results      RELIANCE: financial results due Fri 2026-10-30 (tomorrow; NSE announcement "
                      "2026-10-27 19:17 IST).", out)
        self.assertNotIn("RESULTS DAY", out)

    def test_tickers_from_watchlist_and_plan(self):
        wl = os.path.join(self.tmp.name, "watchlist.json")
        with open(wl, "w", encoding="utf-8") as fh:
            json.dump({"created_by": "india-scanner", "hits": [{"symbol": "INFY", "setups": ["orb"]}]}, fh)
        plan = os.path.join(self.tmp.name, "plan.json")
        with open(plan, "w", encoding="utf-8") as fh:
            json.dump({"created_by": "india-exit", "symbol": "RELIANCE.NS"}, fh)
        self.assertEqual(sorted(news.watch_from_files("/nonexistent.json", wl, plan)), ["INFY", "RELIANCE"])
        code, out, err = run(["--json", "--dry-run", "--hours", "24"] + self.args(watchlist=wl))
        self.assertEqual(code, 0, err)
        tickers = {t for i in json.loads(out)["items"] for t in i["touches"]}
        self.assertEqual(tickers, {"INFY"})
        self.assertFalse(os.path.exists(self.state))

    def test_no_tickers_or_a_bad_one_is_an_error(self):
        code, _, err = run(self.base)
        self.assertEqual(code, 2)
        self.assertIn("no symbols", err)
        code, _, err = run(["--tickers", "BRK.B"] + self.base)
        self.assertEqual(code, 2)
        self.assertIn("not an NSE symbol", err)

    def test_moves(self):
        m = news.parse_chart(raw("chart-INFY.json"), "INFY")
        self.assertEqual((m["price"], m["prev_close"], m["pct"]), (1185.0, 1146.5, 3.36))
        self.assertEqual(news.to_ist(m["time"]).strftime("%H:%M"), "15:28")
        line = news.render_moves([m])[0]
        self.assertEqual(line, "INFY       ₹1,185 +3.36% vs yesterday's close ₹1,146.50 at 15:28 IST  MOVING")
        quiet = dict(m, pct=1.2)
        self.assertNotIn("MOVING", news.render_moves([quiet])[0])
        self.assertIn("MOVING", news.render_moves([quiet], move_pct=1.0)[0])
        with self.assertRaises(news.NewsError):
            news.parse_chart(b'{"chart": {"result": null}}', "ZZZZ")
        # no chart file offline: no move line, no error note
        code, out, _ = run(["--tickers", "TCS", "--hours", "48", "--no-nse", "--dry-run"] + self.base)
        self.assertNotIn("vs yesterday's close", out)
        self.assertNotIn("note:", out)
        self.assertNotIn("No results date", out)                                  # --no-nse: no NSE, no results line

    def test_the_company_name_from_nse_finds_it_in_headlines(self):
        # nse-INFY.json says "Infosys Limited": a TCS headline that names Infosys (and not "(INFY)") touches INFY
        code, out, err = run(["--tickers", "INFY", "TCS", "--json", "--dry-run", "--hours", "400"] + self.base)
        items = json.loads(out)["items"]
        report = [i for i in items if i["title"].startswith("Global Customer Technical Support")]
        self.assertNotIn("(INFY)", report[0]["title"] + report[0]["summary"])
        self.assertEqual(report[0]["touches"], ["INFY", "TCS"])
        self.assertEqual(news.short_name("Infosys Limited"), "Infosys")
        self.assertEqual(news.short_name("HDFC Bank Limited"), "HDFC Bank")

    def test_state_forgets_after_30_days(self):
        state = {"seen": {"old": "2026-08-01T00:00:00+00:00"}}
        now = news.parse_now(NOW)
        news.remember([], state, now)
        self.assertNotIn("old", state["seen"])
        self.assertEqual(state["last_check"], now.isoformat())


if __name__ == "__main__":
    unittest.main()
