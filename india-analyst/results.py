#!/usr/bin/env python3
"""Quarterly results reader for india-analyst. Standard library only.

Reads the quarterly results a company filed with NSE (the XBRL file behind
each results filing) and lays out what changed, quarter by quarter: revenue
(or total income for a bank), net interest income for lenders, EBITDA or
operating profit, net profit, diluted EPS and margins, each against the same
quarter a year earlier, with the last four quarters added up. It also finds
the latest results announcement (outcome of the board meeting, press
release) and the next results board meeting, and says whether it falls
inside the trade you are planning.

    python results.py RELIANCE
    python results.py HDFCBANK --quarters 8
    python results.py RELIANCE --standalone
    python results.py M&M --hold-until 2026-11-06
    python results.py RELIANCE HDFCBANK --metrics trader/metrics.json
    python results.py RELIANCE --json

(python3 on macOS/Linux.)

Sources (NSE, free, no key; browser User-Agent and Referer, one retry after
priming cookies, then a plain error):
  /api/integrated-filing-results?index=equities&type=Integrated%20Filing-%20Financials&symbol=X
        results filed since early 2025 ("Integrated Filing - Financials"):
        one row per quarter and basis (Consolidated / Standalone) with its XBRL
  /api/corporates-financial-results?index=equities&period=Quarterly&symbol=X
        the older results list (to the quarter ended December 2024), XBRL too
  nsearchives.nseindia.com/corporate/xbrl/...xml
        the filing itself (SEBI's in-capmkt taxonomy). Each file holds one
        quarter (and the year to date): the year-ago quarter is its own file.
  /api/event-calendar?index=equities&symbol=X        results board meetings
  /api/corporate-board-meetings?index=equities&symbol=X   (fallback)
  /api/corporate-announcements?index=equities&symbol=X&from_date=..&to_date=..
        the outcome of the board meeting and the press release PDFs

How the numbers are built:
  - Consolidated is preferred (the group, what the EPS is based on); when the
    newest quarter has no consolidated filing, standalone is used. Every
    quarter in a table is the same basis. --standalone forces standalone.
  - A quarter is the filing's three-month period (context "OneD"). When a
    period was filed twice (a revision), the latest filing wins.
  - Net profit is the profit attributable to the owners of the company when
    the filing has it (consolidated), else the profit for the period.
  - EBITDA is DERIVED: revenue from operations minus expenses other than
    finance costs and depreciation. Gross margin is DERIVED: revenue minus
    materials, purchases of stock in trade and the change in inventories.
  - Banks and lenders: no revenue line or gross margin. Total income is the
    top line, net interest income is interest earned minus interest expended
    (banks) or minus finance costs (NBFCs), and a bank's operating profit is
    its "operating profit before provisions" as filed.
  - EPS growth is left blank when the paid-up share capital changed against
    a year earlier (a bonus issue or a split makes old EPS not comparable).

Offline: --offline DIR reads integrated-X.json, legacy-X.json, events-X.json,
boardmeetings-X.json, announcements-X-DD-MM-YYYY.json and each XBRL by its
file name from DIR. The tests use it.

Numbers only: this tool never says whether a stock is a buy.
"""

import argparse
import datetime as _dt
import gzip
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

NSE = "https://www.nseindia.com"
INTEGRATED_URL = (NSE + "/api/integrated-filing-results?index=equities"
                  "&type=Integrated%20Filing-%20Financials&symbol={sym}")
LEGACY_URL = NSE + "/api/corporates-financial-results?index=equities&period=Quarterly&symbol={sym}"
EVENTS_URL = NSE + "/api/event-calendar?index=equities&symbol={sym}"
BOARD_URL = NSE + "/api/corporate-board-meetings?index=equities&symbol={sym}"
ANN_URL = (NSE + "/api/corporate-announcements?index=equities&symbol={sym}"
           "&from_date={a}&to_date={b}")
QUOTE_URL = NSE + "/get-quotes/equity?symbol={sym}"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
ACCEPT_JSON = "application/json, text/plain, */*"
IST = _dt.timedelta(hours=5, minutes=30)
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
XI = "{http://www.xbrl.org/2003/instance}"

NSE_HOLIDAYS = {
    "2026-01-15": "Municipal Corporation Election (Maharashtra)",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
CALENDAR_YEARS = (2026,)

# first concept found wins
CONCEPTS = {
    "revenue": ["RevenueFromOperations"],
    "total_income": ["Income", "TotalIncome"],
    "other_income": ["OtherIncome"],
    "expenses": ["Expenses", "ExpenditureExcludingProvisionsAndContingencies"],
    "materials": ["CostOfMaterialsConsumed"],
    "purchases": ["PurchasesOfStockInTrade"],
    "inventory_change": ["ChangesInInventoriesOfFinishedGoodsWorkInProgressAndStockInTrade"],
    "finance_costs": ["FinanceCosts"],
    "depreciation": ["DepreciationDepletionAndAmortisationExpense",
                     "DepreciationAndAmortisationExpense"],
    "exceptional": ["ExceptionalItemsBeforeTax", "ExceptionalItems"],
    "pbt": ["ProfitBeforeTax", "ProfitLossFromOrdinaryActivitiesBeforeTax"],
    "net_profit": ["ProfitOrLossAttributableToOwnersOfParent",
                   "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates",
                   "ProfitLossForPeriod", "ProfitLossForThePeriod"],
    "eps_diluted": ["DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
                    "DilutedEarningsPerShareAfterExtraordinaryItems",
                    "DilutedEarningsLossPerShareFromContinuingOperations",
                    "DilutedEarningsPerShareBeforeExtraordinaryItems"],
    "eps_basic": ["BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
                  "BasicEarningsPerShareAfterExtraordinaryItems",
                  "BasicEarningsLossPerShareFromContinuingOperations"],
    "interest_earned": ["InterestEarned"],
    "interest_expended": ["InterestExpended"],
    "bank_operating_profit": ["OperatingProfitBeforeProvisionAndContingencies"],
    "provisions": ["ProvisionsOtherThanTaxAndContingencies", "ImpairmentOnFinancialInstruments"],
    "paid_up": ["PaidUpValueOfEquityShareCapital"],
    "face_value": ["FaceValueOfEquityShareCapital"],
}
META = ("Symbol", "NameOfTheCompany", "NameOfBank", "NatureOfReportStandaloneConsolidated",
        "LevelOfRounding", "ReportingQuarter", "DateOfStartOfFinancialYear",
        "DateOfEndOfFinancialYear", "DateOfBoardMeetingWhenFinancialResultsWereApproved",
        "WhetherResultsAreAuditedOrUnaudited", "StartTimeOfBoardMeeting", "EndTimeOfBoardMeeting")
NONZERO = ("net_profit", "eps_diluted", "eps_basic", "total_income")
RESULT_DOCS = ("Outcome of Board Meeting", "Financial Result Updates", "Press Release",
               "Investor Presentation")


class ResultsError(Exception):
    pass


# ---------------------------------------------------------------- time and calendar

def to_ist(utc):
    return utc.replace(tzinfo=None) + IST


def ist_to_utc(local):
    return local.replace(tzinfo=None) - IST


def today_ist():
    return to_ist(_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)).date()


def _date(s):
    return _dt.date.fromisoformat(s)


def nse_date(text):
    """'30-SEP-2026', '31-Dec-2024' or '30-Oct-2026 21:32:55' -> '2026-09-30' (or None)."""
    m = re.match(r"^\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", text or "")
    if not m:
        return None
    try:
        return _dt.datetime.strptime(m.group(1), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def dmy(d):
    return d.strftime("%d-%m-%Y")


def pretty(iso):
    """'2026-11-05' -> '05-Nov-2026' (the way NSE writes dates)."""
    return _date(iso).strftime("%d-%b-%Y") if iso else "?"


def is_trading_day(d):
    return d.weekday() < 5 and d.isoformat() not in NSE_HOLIDAYS


def next_trading_day(d):
    d = d + _dt.timedelta(days=1)
    while not is_trading_day(d):
        d += _dt.timedelta(days=1)
    return d


def prev_trading_day(d):
    d = d - _dt.timedelta(days=1)
    while not is_trading_day(d):
        d -= _dt.timedelta(days=1)
    return d


def month_end(year, month):
    nxt = _dt.date(year + (month == 12), month % 12 + 1, 1)
    return nxt - _dt.timedelta(days=1)


def add_months_end(iso, months):
    d = _date(iso)
    m = d.month - 1 + months
    return month_end(d.year + m // 12, m % 12 + 1)


# ---------------------------------------------------------------- money

def _group_digits(n):
    """1234567 -> "12,34,567" (Indian grouping: thousands, then lakhs and crores)."""
    s = str(int(n))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def money(x):
    """100000 -> "₹1,00,000", 46.875 -> "₹46.88", -500 -> "-₹500"."""
    p = int(round(abs(float(x)) * 100 + 1e-9))
    r, ps = divmod(p, 100)
    s = "₹" + _group_digits(r) + ("" if ps == 0 else ".%02d" % ps)
    return ("-" + s) if float(x) < 0 and p else s


def crore(v):
    """Rupees -> whole crore with Indian grouping: 3328610000000 -> "3,32,861"."""
    if v is None:
        return "-"
    c = int(round(abs(v) / 1e7))
    return ("-" if v < 0 and c else "") + _group_digits(c)


def rs_crore(v):
    if v is None:
        return "n/a"
    c = crore(v)
    return ("-₹%s crore" % c[1:]) if c.startswith("-") else "₹%s crore" % c


# ---------------------------------------------------------------- NSE fetching

class Fetcher(object):
    """NSE with a browser User-Agent and Referer, a cookie jar, and exactly one
    retry after priming cookies on the home page. Offline: files from a folder."""

    def __init__(self, offline=None, pause=0.35, timeout=30):
        self.offline, self.pause, self.timeout = offline, pause, timeout
        self._last = 0.0
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def _wait(self):
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()

    def _open(self, url, accept):
        self._wait()
        req = urllib.request.Request(url, headers={
            "User-Agent": BROWSER_UA, "Referer": NSE + "/", "Accept": accept,
            "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip"})
        with self.opener.open(req, timeout=self.timeout) as resp:
            data = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
        return data

    def prime(self):
        try:
            self._open(NSE + "/", "text/html,application/xhtml+xml,*/*")
        except (urllib.error.URLError, OSError, ValueError):
            pass  # NSE's home page itself may answer 403; the cookies are what matter

    def get(self, url, name, check=None, accept=ACCEPT_JSON):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise ResultsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                data = fh.read()
            if check:
                check(data)
            return data
        last = None
        for attempt in (1, 2):
            if attempt == 2:
                self.prime()
            try:
                data = self._open(url, accept)
                if check:
                    check(data)
                return data
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise ResultsError("NSE has nothing at %s (HTTP 404)" % url)
                last = "HTTP %s" % exc.code
            except (urllib.error.URLError, OSError) as exc:
                last = str(getattr(exc, "reason", exc))
            except ValueError as exc:
                last = str(exc)
        raise ResultsError("NSE refused or failed (%s) for %s. Its bot shield does this at "
                           "times: try again in a minute." % (last, url))

    def json(self, url, name):
        def check(data):
            json.loads(data.decode("utf-8-sig"))
        return json.loads(self.get(url, name, check).decode("utf-8-sig"))

    def xml(self, url):
        def check(data):
            if b"<" not in data[:200]:
                raise ValueError("not XML")
        return self.get(url, os.path.basename(urllib.parse.urlparse(url).path), check,
                        accept="application/xml, text/xml, */*")


def norm_symbol(text):
    s = (text or "").strip().upper()
    if s.endswith(".NS"):
        s = s[:-3]
    if s.endswith(".BO"):
        raise ResultsError("%s is a BSE symbol: this tool reads NSE filings, use the NSE "
                           "symbol" % text)
    if not SYMBOL.match(s):
        raise ResultsError("%r is not an NSE symbol (like RELIANCE, M&M, BAJAJ-AUTO)" % text)
    return s


def q(sym):
    return urllib.parse.quote(sym, safe="")


# ---------------------------------------------------------------- the results lists

def listing(integrated, legacy=None):
    """Rows from both NSE lists, normalised: one dict per filing."""
    out = []
    data = integrated.get("data", []) if isinstance(integrated, dict) else (integrated or [])
    for r in data:
        if "Financial" not in (r.get("type") or "Financials") or not r.get("xbrl"):
            continue
        basis = (r.get("consolidated") or "").lower()
        if basis not in ("consolidated", "standalone"):
            continue
        out.append({"period_end": nse_date(r.get("qe_Date")), "basis": basis,
                    "xbrl": r["xbrl"], "ixbrl": r.get("ixbrl"),
                    "filed": (r.get("broadcast_Date") or "").strip(),
                    "filed_iso": nse_date(r.get("broadcast_Date")),
                    "audited": r.get("audited"), "revision": r.get("type_Sub"),
                    "source": "integrated filing"})
    for r in legacy or []:
        if (r.get("period") or "Quarterly") != "Quarterly" or not r.get("xbrl"):
            continue
        if not str(r["xbrl"]).lower().endswith(".xml"):
            continue
        c = (r.get("consolidated") or "").lower()
        basis = "consolidated" if c == "consolidated" else "standalone"
        out.append({"period_end": nse_date(r.get("toDate")), "basis": basis,
                    "xbrl": r["xbrl"], "ixbrl": None,
                    "filed": (r.get("broadCastDate") or r.get("filingDate") or "").strip(),
                    "filed_iso": nse_date(r.get("broadCastDate") or r.get("filingDate")),
                    "audited": r.get("audited"), "revision": None,
                    "source": "financial results"})
    return [r for r in out if r["period_end"]]


def _filed_key(r):
    try:
        return _dt.datetime.strptime(r["filed"][:20].strip(), "%d-%b-%Y %H:%M:%S")
    except ValueError:
        try:
            return _dt.datetime.strptime(r["filed"][:17].strip(), "%d-%b-%Y %H:%M")
        except ValueError:
            return _dt.datetime.min


def choose_basis(rows, standalone=False):
    if standalone:
        return "standalone"
    if not rows:
        return "consolidated"
    newest = max(r["period_end"] for r in rows)
    has = {r["basis"] for r in rows if r["period_end"] == newest}
    return "consolidated" if "consolidated" in has else "standalone"


def pick(rows, basis):
    """{period_end: row} for one basis; a later filing (a revision) wins."""
    out = {}
    for r in rows:
        if r["basis"] != basis:
            continue
        cur = out.get(r["period_end"])
        if cur is None or _filed_key(r) > _filed_key(cur):
            out[r["period_end"]] = r
    return out


# ---------------------------------------------------------------- one XBRL filing

def _num(text):
    try:
        return float(str(text).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_xbrl(data):
    """The three-month figures in one NSE results XBRL file."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ResultsError("the results file is not valid XBRL: %s" % exc)
    periods = {}
    for c in root.iter(XI + "context"):
        if c.find(".//" + XI + "scenario") is not None or c.find(".//" + XI + "segment") is not None:
            continue
        s, e = c.find(".//" + XI + "startDate"), c.find(".//" + XI + "endDate")
        if s is None or e is None or not s.text or not e.text:
            continue
        try:
            periods[c.get("id")] = (s.text.strip(), e.text.strip())
        except ValueError:
            continue
    quarter = []
    for cid, (s, e) in periods.items():
        try:
            days = (_date(e) - _date(s)).days
        except ValueError:
            continue
        if 80 <= days <= 100:
            quarter.append(cid)
    if not quarter:
        raise ResultsError("no three-month period in this filing")
    # NSE's files call the quarter "OneD"; older files also label the year to date
    # with the quarter's dates, so the name decides, not the dates.
    qid = "OneD" if "OneD" in quarter else quarter[0]
    facts, meta = {}, {}
    for el in root:
        cid = el.get("contextRef")
        if cid is None:
            continue
        tag = el.tag.rsplit("}", 1)[-1]
        text = (el.text or "").strip()
        if tag in META and text and (tag not in meta or cid == qid):
            meta[tag] = text
        if cid == qid:
            facts[tag] = text
    values, used = {}, {}
    for key, names in CONCEPTS.items():
        for n in names:
            v = _num(facts.get(n)) if n in facts else None
            if v is None:
                continue
            if key not in values:
                values[key], used[key] = v, n
            # some filings put 0 in the owners' share (or diluted EPS) and the real
            # figure in the next line: a zero does not win over a number
            if v != 0 or key not in NONZERO:
                values[key], used[key] = v, n
                break
    if "interest_expended" in values:
        kind = "bank"
    elif "interest_earned" in values:
        kind = "lender"
    else:
        kind = "company"
    start, end = periods[qid]
    return {"start": start, "end": end, "values": values, "concepts": used, "meta": meta,
            "kind": kind}


# ---------------------------------------------------------------- analysis

def fiscal_label(end, fy_start=None):
    """Indian financial year: the quarter ending 2026-09-30 is "Q2 FY27"."""
    e = _date(end)
    try:
        s = _date(fy_start) if fy_start else None
    except ValueError:
        s = None
    if s is None or s > e:
        s = _dt.date(e.year if e.month >= 4 else e.year - 1, 4, 1)
    months = (e.year - s.year) * 12 + e.month - s.month + 1
    qn = max(1, min(4, (months + 2) // 3))
    fy_end = s.year + (1 if s.month > 1 else 0)
    return "Q%d FY%02d" % (qn, fy_end % 100)


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round(100.0 * (a - b) / abs(b), 1)


def margin(part, whole):
    if part is None or not whole:
        return None
    return round(100.0 * part / whole, 1)


def derive(parsed):
    """One quarter's numbers (rupees) from a parsed filing."""
    v, kind = parsed["values"], parsed["kind"]
    g = v.get
    out = {"end": parsed["end"], "start": parsed["start"], "kind": kind,
           "fiscal": fiscal_label(parsed["end"], parsed["meta"].get("DateOfStartOfFinancialYear")),
           "revenue": None if kind == "bank" else g("revenue"),
           "total_income": g("total_income"), "other_income": g("other_income"),
           "expenses": g("expenses"), "pbt": g("pbt"), "exceptional": g("exceptional"),
           "net_profit": g("net_profit"), "paid_up": g("paid_up"), "face_value": g("face_value"),
           "net_profit_concept": parsed["concepts"].get("net_profit"),
           "eps_diluted": g("eps_diluted"), "eps_kind": "diluted",
           "nii": None, "operating_profit": None, "operating_label": None, "gross_profit": None}
    if out["eps_diluted"] is None and g("eps_basic") is not None:
        out["eps_diluted"], out["eps_kind"] = g("eps_basic"), "basic"
    out["top_line"] = out["revenue"] if out["revenue"] is not None else out["total_income"]
    out["top_line_name"] = "revenue" if out["revenue"] is not None else "total income"
    if kind == "bank":
        if g("interest_earned") is not None and g("interest_expended") is not None:
            out["nii"] = g("interest_earned") - g("interest_expended")
        out["operating_profit"] = g("bank_operating_profit")
        out["operating_label"] = "operating profit before provisions"
    elif kind == "lender":
        if g("interest_earned") is not None and g("finance_costs") is not None:
            out["nii"] = g("interest_earned") - g("finance_costs")
    else:
        if None not in (g("revenue"), g("expenses"), g("finance_costs"), g("depreciation")):
            out["operating_profit"] = g("revenue") - (g("expenses") - g("finance_costs")
                                                      - g("depreciation"))
            out["operating_label"] = "EBITDA (derived)"
        cogs = [g(k) for k in ("materials", "purchases", "inventory_change")]
        if g("revenue") and any(c for c in cogs[:2]):
            out["gross_profit"] = g("revenue") - sum(c or 0 for c in cogs)
    return out


def _yoy_match(by_end, end, days=365, slack=10):
    target = _date(end) - _dt.timedelta(days=days)
    best = None
    for e in by_end:
        gap = abs((_date(e) - target).days)
        if gap <= slack and (best is None or gap < best[0]):
            best = (gap, e)
    return by_end[best[1]] if best else None


def _same_shares(a, b):
    if not a or not b:
        return True  # cannot tell; do not invent a problem
    pa, pb = a.get("paid_up"), b.get("paid_up")
    fa, fb = a.get("face_value"), b.get("face_value")
    if fa and fb and abs(fa - fb) > 1e-9:
        return False
    if pa and pb and abs(pa - pb) / max(abs(pa), abs(pb)) > 0.02:
        return False
    return True


def analyse(quarters_data, quarters=5, name=None, basis="consolidated"):
    """Every number in the report, as a dict. quarters_data: derive() rows."""
    by_end = {r["end"]: r for r in quarters_data}
    if not by_end:
        raise ResultsError("no quarterly figures could be read from NSE's filings")
    ends = sorted(by_end, reverse=True)[:quarters]
    rows = []
    for end in ends:
        r = dict(by_end[end])
        prior = _yoy_match(by_end, end)
        r["has_prior"] = prior is not None
        r["prior_end"] = (prior or {}).get("end")
        r["prior_paid_up"] = (prior or {}).get("paid_up")
        r["shares_changed"] = not _same_shares(r, prior)
        r["top_line_yoy"] = pct(r["top_line"], (prior or {}).get("top_line"))
        r["revenue_yoy"] = pct(r["revenue"], (prior or {}).get("revenue"))
        r["nii_yoy"] = pct(r["nii"], (prior or {}).get("nii"))
        r["net_profit_yoy"] = pct(r["net_profit"], (prior or {}).get("net_profit"))
        r["eps_yoy"] = None if r["shares_changed"] else pct(r["eps_diluted"],
                                                            (prior or {}).get("eps_diluted"))
        r["gross_margin"] = margin(r["gross_profit"], r["revenue"])
        r["operating_margin"] = margin(r["operating_profit"], r["top_line"])
        r["net_margin"] = margin(r["net_profit"], r["top_line"])
        if r["eps_diluted"] is not None:
            r["eps_diluted"] = round(r["eps_diluted"], 2)
        rows.append(r)
    ttm = {}
    last4 = sorted(by_end)[-4:]
    if len(last4) == 4 and (_date(last4[-1]) - _date(last4[0])).days < 300:
        for key in ("top_line", "net_profit", "nii", "eps_diluted"):
            vals = [by_end[e].get(key) for e in last4]
            if None not in vals:
                ttm[key] = sum(vals)
        if "eps_diluted" in ttm:
            if all(_same_shares(by_end[last4[-1]], by_end[e]) for e in last4):
                ttm["eps_diluted"] = round(ttm["eps_diluted"], 2)
            else:
                del ttm["eps_diluted"]
                ttm["eps_note"] = "share capital changed inside the last four quarters"
        ttm["top_line_name"] = by_end[last4[-1]]["top_line_name"]
        ttm["from"], ttm["to"] = by_end[last4[0]]["start"], last4[-1]
    return {"entity": name, "basis": basis, "kind": rows[0]["kind"], "rows": rows, "ttm": ttm}


def changes(result):
    """Plain sentences: what changed in the latest quarter."""
    rows = result["rows"]
    if not rows:
        return []
    cur = rows[0]
    prev = rows[1] if len(rows) > 1 else None
    out = []

    def yoy(v):
        return "" if v is None else " (%+.1f%% vs a year earlier)" % v

    def short(v):
        return "" if v is None else " (%+.1f%%)" % v

    head = "Quarter ending %s (%s, %s)" % (cur["end"], cur["fiscal"], result["basis"])
    if cur["kind"] == "bank":
        line = "%s: total income %s%s" % (head, rs_crore(cur["total_income"]),
                                          yoy(cur["top_line_yoy"]))
    else:
        line = "%s: revenue from operations %s%s" % (head, rs_crore(cur["revenue"]),
                                                     yoy(cur["revenue_yoy"]))
    if cur["nii"] is not None:
        line += ", net interest income %s%s" % (rs_crore(cur["nii"]), short(cur["nii_yoy"]))
    line += ", net profit %s%s" % (rs_crore(cur["net_profit"]), short(cur["net_profit_yoy"]))
    if cur["eps_diluted"] is not None:
        line += ", %s EPS ₹%.2f%s" % (cur["eps_kind"], cur["eps_diluted"], short(cur["eps_yoy"]))
    out.append(line + ".")
    if not cur.get("has_prior"):
        out.append("No filing for the same quarter a year earlier was found, so there is no "
                   "year-on-year growth.")
    if cur["operating_margin"] is not None:
        s = "%s %s, %.1f%% of %s" % (cur["operating_label"][0].upper() + cur["operating_label"][1:],
                                    rs_crore(cur["operating_profit"]), cur["operating_margin"],
                                    cur["top_line_name"])
        if prev and prev.get("operating_margin") is not None:
            s += " (%+.1f points vs the quarter before)" % (cur["operating_margin"]
                                                            - prev["operating_margin"])
        out.append(s + ".")
    if cur["gross_margin"] is not None:
        out.append("Gross margin (derived: revenue minus materials, purchases and inventory "
                   "change) %.1f%%." % cur["gross_margin"])
    if cur["net_margin"] is not None:
        s = "Net margin %.1f%% of %s" % (cur["net_margin"], cur["top_line_name"])
        if prev and prev.get("net_margin") is not None:
            s += " (%+.1f points vs the quarter before)" % (cur["net_margin"] - prev["net_margin"])
        out.append(s + ".")
    if cur["kind"] in ("bank", "lender"):
        out.append("A lender: no gross margin or EBITDA. Read provisions and bad loans "
                   "(gross and net NPA) in the filing before calling the profit clean.")
    if cur.get("shares_changed"):
        out.append("Share capital changed against a year earlier (paid-up capital %s then, %s "
                   "now): a bonus issue, a split or new shares. EPS growth is not like for "
                   "like, so it is left blank." % (rs_crore(cur.get("prior_paid_up")),
                                                   rs_crore(cur.get("paid_up"))))
    if cur["exceptional"]:
        out.append("Exceptional items of %s are inside profit before tax: a one-off. Read the "
                   "notes in the filing before reading it as growth." % rs_crore(cur["exceptional"]))
    if cur["other_income"] and cur["pbt"] and cur["pbt"] > 0 \
            and cur["other_income"] / cur["pbt"] >= 0.25 and cur["kind"] == "company":
        out.append("Other income (%s) is %.0f%% of profit before tax: part of the profit does "
                   "not come from the business." % (rs_crore(cur["other_income"]),
                                                    100.0 * cur["other_income"] / cur["pbt"]))
    if prev and cur["top_line"] is not None and prev.get("top_line"):
        out.append("%s %+.1f%% vs the quarter before (many businesses are seasonal: compare "
                   "with a year earlier first)." % (cur["top_line_name"][0].upper()
                                                    + cur["top_line_name"][1:],
                                                    pct(cur["top_line"], prev["top_line"])))
    return out


# ---------------------------------------------------------------- results dates

def results_events(events):
    """Results board meetings from the event calendar (or board meetings list), oldest first."""
    out = []
    for r in events or []:
        purpose = r.get("purpose") or r.get("bm_purpose") or ""
        desc = r.get("bm_desc") or ""
        d = nse_date(r.get("date") or r.get("bm_date"))
        if not d:
            continue
        if "financial result" in purpose.lower() or (
                "result" in purpose.lower() and "financial" in desc.lower()):
            out.append({"date": d, "purpose": purpose.strip(), "desc": desc.strip(),
                        "announced": (r.get("bm_timestamp") or "").strip() or None})
    seen, uniq = set(), []
    for e in sorted(out, key=lambda e: e["date"]):
        if e["date"] not in seen:
            seen.add(e["date"])
            uniq.append(e)
    return uniq


def due_date(quarter_end):
    """SEBI LODR reg 33(3): 45 days after a quarter, 60 days after the year (March)."""
    d = _date(quarter_end)
    return (d + _dt.timedelta(days=60 if d.month == 3 else 45)).isoformat()


def trade_window(events, latest_end, today, hold_until=None):
    """Does a results release fall inside the trade [today, hold_until]?"""
    hold = hold_until or today
    if hold < today:
        raise ResultsError("--hold-until is before the trade date")
    past = [e for e in events if e["date"] < today.isoformat()]
    future = [e for e in events if e["date"] >= today.isoformat()]
    nxt = future[0] if future else None
    last = past[-1] if past else None
    out = {"trade_from": today.isoformat(), "trade_to": hold.isoformat(), "next": nxt,
           "last": last, "inside": False, "lines": []}
    span = ("a day trade on %s" % pretty(today.isoformat()) if hold == today
            else "a trade from %s to %s" % (pretty(today.isoformat()), pretty(hold.isoformat())))
    if nxt:
        when = pretty(nxt["date"])
        if nxt["date"] <= hold.isoformat():
            out["inside"] = True
            out["lines"].append(
                "INSIDE THIS TRADE: the results board meeting on %s falls inside %s. Indian "
                "companies often release results during market hours on the meeting day, not "
                "only after the close, and a stop does not protect against the jump." % (when, span))
        else:
            out["lines"].append(
                "Next results board meeting: %s, after %s. A day trade flat by 15:00 IST is "
                "not exposed to it; a position still held then is." % (when, span))
    else:
        nq = add_months_end(latest_end, 3).isoformat() if latest_end else None
        if nq and nq < today.isoformat():
            due = due_date(nq)
            out["inside"] = None
            out["lines"].append(
                "No results board meeting is on NSE's calendar yet, but results for the quarter "
                "ended %s are due by %s (SEBI: 45 days after a quarter, 60 after the year) and "
                "the company needs to give only 2 working days' notice. Not known for %s: check "
                "again before holding overnight." % (pretty(nq), pretty(due), span))
        elif nq:
            out["lines"].append(
                "No results board meeting is on NSE's calendar. The next quarter ends %s; its "
                "results are due by %s, so they are not inside %s." % (
                    pretty(nq), pretty(due_date(nq)), span))
    if last:
        lastd = _date(last["date"])
        if lastd >= prev_trading_day(today):
            out["lines"].append(
                "Results came out at the board meeting on %s, just before this trade: the price "
                "may still be reacting to them (gaps, wide ranges)." % pretty(last["date"]))
    return out


def latest_documents(announcements):
    """The results PDFs among one day's announcements (outcome, press release, ...)."""
    docs = []
    for a in announcements or []:
        desc = (a.get("desc") or "").strip()
        if desc in RESULT_DOCS or "financial result" in desc.lower():
            docs.append({"what": desc, "when": (a.get("an_dt") or "").strip(),
                         "url": a.get("attchmntFile")})
    return docs


# ---------------------------------------------------------------- output

def _p(v):
    return "-" if v is None else "%+.1f%%" % v


def _pm(v):
    return "-" if v is None else "%.1f%%" % v


def _eps(v):
    return "-" if v is None else "%.2f" % v


def render(sym, result, info):
    rows = result["rows"]
    bank = result["kind"] in ("bank", "lender")
    out = ["%s (%s). %s, ₹ crore except EPS (₹). Source: NSE results filings (XBRL)." % (
        result["entity"] or sym, sym, result["basis"].capitalize()), ""]
    if bank:
        top = "total inc" if rows[0]["top_line_name"] == "total income" else "revenue"
        out.append("%-11s %-8s %10s %8s %9s %8s %10s %10s %8s %7s %7s" % (
            "quarter end", "fiscal", top, ("inc" if top == "total inc" else "rev") + " yoy", "NII", "NII yoy", "op profit",
            "net profit", "net yoy", "EPS", "EPS yoy"))
        for r in rows:
            out.append("%-11s %-8s %10s %8s %9s %8s %10s %10s %8s %7s %7s" % (
                r["end"], r["fiscal"], crore(r["top_line"]), _p(r["top_line_yoy"]),
                crore(r["nii"]), _p(r["nii_yoy"]), crore(r["operating_profit"]),
                crore(r["net_profit"]), _p(r["net_profit_yoy"]), _eps(r["eps_diluted"]),
                _p(r["eps_yoy"])))
    else:
        out.append("%-11s %-8s %10s %8s %6s %7s %6s %10s %8s %7s %7s" % (
            "quarter end", "fiscal", "revenue", "rev yoy", "gross", "EBITDA", "net",
            "net profit", "net yoy", "EPS", "EPS yoy"))
        for r in rows:
            out.append("%-11s %-8s %10s %8s %6s %7s %6s %10s %8s %7s %7s" % (
                r["end"], r["fiscal"], crore(r["revenue"]), _p(r["revenue_yoy"]),
                _pm(r["gross_margin"]), _pm(r["operating_margin"]), _pm(r["net_margin"]),
                crore(r["net_profit"]), _p(r["net_profit_yoy"]), _eps(r["eps_diluted"]),
                _p(r["eps_yoy"])))
    t = result["ttm"]
    if t:
        bits = ["%s %s" % (t.get("top_line_name", "revenue"), rs_crore(t.get("top_line")))]
        if t.get("nii") is not None:
            bits.append("net interest income %s" % rs_crore(t["nii"]))
        bits.append("net profit %s" % rs_crore(t.get("net_profit")))
        bits.append("EPS %s" % ("₹%.2f" % t["eps_diluted"] if t.get("eps_diluted") is not None
                                else "- (%s)" % t.get("eps_note", "not available")))
        out.append("")
        out.append("Last 4 quarters (%s to %s): %s." % (t["from"], t["to"], ", ".join(bits)))
    out.append("")
    out.append("What changed:")
    for line in changes(result):
        out.append("  - " + line)
    if info.get("latest"):
        lf = info["latest"]
        out.append("")
        out.append("Latest results: %s, board meeting %s, on NSE %s IST (%s)." % (
            rows[0]["fiscal"], pretty(lf.get("board_meeting")) if lf.get("board_meeting") else "?",
            lf.get("filed") or "?", (lf.get("audited") or "").lower() or "audit status not given"))
        out.append("  XBRL: %s" % lf["xbrl"])
        if lf.get("ixbrl"):
            out.append("  readable: %s" % lf["ixbrl"])
        for d in info.get("documents", []):
            out.append("  %s, %s IST: %s" % (d["what"], d["when"], d["url"]))
    for n in info.get("notes", []):
        out.append("")
        out.append("NOTE: " + n)
    if info.get("window"):
        out.append("")
        for line in info["window"]["lines"]:
            out.append(line)
    if rows and rows[0].get("operating_label") == "EBITDA (derived)":
        out.append("")
        out.append("EBITDA and gross margin are derived from the filed lines (see --help); "
                   "net profit is the owners' share where the filing gives it.")
    return "\n".join(out) + "\n"


def metrics_row(sym, result):
    rows = result["rows"]
    cur = rows[0] if rows else {}
    t = result["ttm"]
    return {"symbol": sym, "name": result["entity"], "basis": result["basis"],
            "period_end": cur.get("end"), "fiscal": cur.get("fiscal"),
            "top_line": cur.get("top_line_name"),
            "top_line_ttm": t.get("top_line"), "net_profit_ttm": t.get("net_profit"),
            "eps_ttm": t.get("eps_diluted"),
            "top_line_growth_yoy": cur.get("top_line_yoy"),
            "net_profit_growth_yoy": cur.get("net_profit_yoy"),
            "eps_growth_yoy": cur.get("eps_yoy"),
            "operating_margin": cur.get("operating_margin"),
            "operating_measure": cur.get("operating_label"),
            "net_margin": cur.get("net_margin"),
            "source": "NSE results filings (XBRL)"}


# ---------------------------------------------------------------- one company

def company(fetcher, sym, quarters=5, standalone=False, today=None, hold_until=None,
            filings=True):
    today = today or today_ist()
    notes = []
    integrated = fetcher.json(INTEGRATED_URL.format(sym=q(sym)), "integrated-%s.json" % sym)
    rows = listing(integrated)
    basis = choose_basis(rows, standalone)
    chosen = pick(rows, basis)
    need = quarters + 4
    if len(chosen) < need:
        try:
            legacy = fetcher.json(LEGACY_URL.format(sym=q(sym)), "legacy-%s.json" % sym)
            rows = rows + listing({}, legacy if isinstance(legacy, list) else [])
            if not chosen:
                basis = choose_basis(rows, standalone)
            chosen = pick(rows, basis)
        except ResultsError as exc:
            notes.append("the older results list could not be read (%s): fewer quarters "
                         "and growth figures." % exc)
    if not chosen:
        raise ResultsError("NSE lists no %s quarterly results for %s (a new listing, an SME "
                           "or not an NSE company?)" % (basis, sym))
    ends = sorted(chosen, reverse=True)[:need]
    parsed, name, latest_meta = [], None, None
    for end in ends:
        lf = chosen[end]
        try:
            p = parse_xbrl(fetcher.xml(lf["xbrl"]))
        except ResultsError as exc:
            notes.append("the %s filing for the quarter ended %s could not be read (%s)." % (
                basis, end, exc))
            continue
        d = derive(p)
        if latest_meta is None:
            latest_meta = dict(lf, board_meeting=p["meta"].get(
                "DateOfBoardMeetingWhenFinancialResultsWereApproved"))
            name = p["meta"].get("NameOfTheCompany") or p["meta"].get("NameOfBank")
        parsed.append(d)
    result = analyse(parsed, quarters=quarters, name=name, basis=basis)
    other = "standalone" if basis == "consolidated" else "consolidated"
    if not standalone and basis == "standalone" and any(r["basis"] == "consolidated" for r in rows):
        notes.append("the newest quarter has no consolidated filing, so the table is standalone.")
    missing = [e for e in sorted(set(r["period_end"] for r in rows), reverse=True)[:quarters]
               if e not in chosen]
    if missing:
        notes.append("no %s filing for %s (only %s)." % (basis, ", ".join(missing), other))
    info = {"latest": latest_meta, "documents": [], "notes": notes, "window": None}
    if not filings:
        return result, info
    events = []
    try:
        events = results_events(fetcher.json(EVENTS_URL.format(sym=q(sym)), "events-%s.json" % sym))
    except ResultsError:
        try:
            events = results_events(fetcher.json(BOARD_URL.format(sym=q(sym)),
                                                 "boardmeetings-%s.json" % sym))
        except ResultsError as exc:
            notes.append("NSE's results calendar could not be read (%s): the next results date "
                         "is unknown, check nseindia.com before holding overnight." % exc)
    latest_end = result["rows"][0]["end"]
    info["window"] = trade_window(events, latest_end, today, hold_until)
    bm = (latest_meta or {}).get("board_meeting")
    newer = [e for e in events if (not bm or e["date"] > bm) and e["date"] < today.isoformat()]
    if newer and (_date(newer[-1]["date"]) - _date(latest_end)).days < 120:
        notes.append("the board met for results on %s, but NSE does not list that XBRL yet. "
                     "Read the outcome of the board meeting on NSE's announcements page: %s" % (
                         pretty(newer[-1]["date"]), QUOTE_URL.format(sym=q(sym))))
    if bm:
        try:
            b = _date(bm)
            ann = fetcher.json(ANN_URL.format(sym=q(sym), a=dmy(b), b=dmy(b + _dt.timedelta(days=1))),
                               "announcements-%s-%s.json" % (sym, dmy(b)))
            info["documents"] = latest_documents(ann if isinstance(ann, list) else [])
        except (ResultsError, ValueError):
            pass
    return result, info


# ---------------------------------------------------------------- inputs

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """The scanner's hits, the planned trade and your holdings, as NSE symbols."""
    out = []

    def add(s):
        if not s:
            return
        try:
            s = norm_symbol(s)
        except ResultsError:
            return
        if s not in out:
            out.append(s)
    for h in (load_json(watchlist) or {}).get("hits", []):
        add(h.get("symbol") or h.get("ticker"))
    add((load_json(plan) or {}).get("symbol"))
    for p in (load_json(portfolio) or {}).get("positions", []):
        add(p.get("symbol") or p.get("ticker") or p.get("tradingsymbol"))
    return out


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("symbols", nargs="*", help="NSE symbols, e.g. RELIANCE HDFCBANK "
                                               "(default: watchlist + plan + portfolio)")
    ap.add_argument("--quarters", type=int, default=5, help="quarters to show (default 5)")
    ap.add_argument("--standalone", action="store_true", help="standalone, not consolidated")
    ap.add_argument("--hold-until", metavar="YYYY-MM-DD",
                    help="last day of the trade (default: today, a day trade)")
    ap.add_argument("--today", metavar="YYYY-MM-DD", help="the trade date (default: today in India)")
    ap.add_argument("--no-filings", action="store_true",
                    help="skip the calendar and announcement lookups")
    ap.add_argument("--metrics", metavar="FILE", help="merge a row per symbol into this JSON")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        today = _date(args.today) if args.today else today_ist()
        hold = _date(args.hold_until) if args.hold_until else None
    except ValueError:
        print("results: dates are YYYY-MM-DD, like 2026-10-26", file=sys.stderr)
        return 2
    if args.quarters < 1:
        print("results: --quarters must be 1 or more", file=sys.stderr)
        return 2
    wanted = args.symbols or watch_from_files(args.portfolio, args.watchlist, args.plan)
    if not wanted:
        print("results: no symbols (give some, like RELIANCE, or run /india-scanner to fill "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    fetcher = Fetcher(offline=args.offline)
    results, rows, failed = [], [], 0
    for raw in wanted:
        try:
            sym = norm_symbol(raw)
            result, info = company(fetcher, sym, args.quarters, args.standalone, today, hold,
                                   filings=not args.no_filings)
        except ResultsError as exc:
            print("results: %s: %s" % (str(raw).upper(), exc), file=sys.stderr)
            failed += 1
            continue
        results.append({"symbol": sym, "result": result, "info": info,
                        "changes": changes(result)})
        rows.append(metrics_row(sym, result))
        if not args.json:
            sys.stdout.write(render(sym, result, info))
            sys.stdout.write("\n")
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    if args.metrics and rows:
        existing = {}
        if os.path.isfile(args.metrics):
            with open(args.metrics, encoding="utf-8") as fh:
                existing = {r["symbol"]: r for r in json.load(fh).get("companies", [])
                            if r.get("symbol")}
        for r in rows:
            existing[r["symbol"]] = r
        folder = os.path.dirname(args.metrics)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.metrics, "w", encoding="utf-8") as fh:
            json.dump({"as_of": today.isoformat(),
                       "companies": sorted(existing.values(), key=lambda r: r["symbol"])},
                      fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        if not args.json:
            print("wrote %s (%d companies)" % (args.metrics, len(existing)))
    if results and not args.json:
        print("Numbers as filed with NSE. Not financial advice: this reads filings and does "
              "arithmetic.")
    return 0 if results and not failed else (1 if results else 2)


if __name__ == "__main__":
    sys.exit(main())
