#!/usr/bin/env python3
"""Insider and big-investor tracker for india-insider. Standard library only.

Reads two kinds of public NSE disclosure:

  1. Bulk and block deals: who bought or sold a big block of a stock today
     (a bulk deal is one client trading more than 0.5% of a company's shares
     in a day; a block deal is a single large trade in the block-deal window).
  2. Insider trading disclosures under SEBI's Prohibition of Insider Trading
     (PIT) Regulations 2015: promoters, directors, key managerial personnel
     and designated persons reporting their own trades in the company.

    python insider.py deals                          # the latest day's bulk and block deals
    python insider.py deals --days 5 --symbol SWIGGY
    python insider.py deals --client "mutual fund" --kind block
    python insider.py insider                        # PIT filings of the last 3 days
    python insider.py insider --symbol NATCOPHARM --read
    python insider.py insider --category promoter --side buy --days 2
    python insider.py insider --symbol RELIANCE --archive   # NSE's older list, to Feb 2026
    python insider.py read IT_1299_WebXMLFile_20261029_171729430.xml
    python insider.py check --mine                   # only what is new since the last check

(python3 on macOS/Linux.)

Where the data comes from (NSE, free, no key; a browser User-Agent and
Referer, one retry after priming cookies, then a plain error):
  https://nsearchives.nseindia.com/content/equities/bulk.csv     latest day
  https://nsearchives.nseindia.com/content/equities/block.csv    latest day
  /api/historicalOR/bulk-block-short-deals?optionType=bulk_deals|block_deals
        &from=DD-MM-YYYY&to=DD-MM-YYYY&csv=true    a range (the JSON form of the
        same API stops at 70 rows a day, the CSV form does not)
  /api/corporates-pit-gg?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY[&symbol=X]
        PIT filings: one row per filing (company, time, regulation) with an XML
        file; the person, category, shares, value, buy or sell, mode and dates
        are inside the XML (--read)
  /api/corporates-pit?index=equities&symbol=X
        NSE's older insider list with the details in each row; it stops in
        February 2026 (--archive)

What this is, and is not:
  - Every row is a PUBLIC disclosure, and it is LATE. Bulk and block deals are
    published after the close of the day they happen. A PIT disclosure is due
    from the person to the company within 2 trading days of the trade, and from
    the company to the exchange within 2 trading days after that (reg 7(2)).
    Reading them fast is not inside information. There is none here.
  - Only "Market Purchase" and "Market Sale" are trades in the market.
    Allotments, ESOPs, gifts, inheritance, off-market transfers and pledges
    are not someone buying or selling at the screen price.

State: `check` remembers what it has shown in trader/state/insider.json, so
each deal and each filing alerts once. Offline: --offline DIR reads bulk.csv,
block.csv, bulk-range-FROM-TO.csv, block-range-FROM-TO.csv,
pit-FROM-TO.json (pit-SYMBOL-FROM-TO.json), pit-archive-SYMBOL.json and each
XML by its file name. The tests use it.
"""

import argparse
import csv
import datetime as _dt
import gzip
import http.cookiejar
import io
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
ARCHIVES = "https://nsearchives.nseindia.com"
LATEST_URL = ARCHIVES + "/content/equities/%s.csv"
RANGE_URL = (NSE + "/api/historicalOR/bulk-block-short-deals?optionType=%s_deals"
             "&from=%s&to=%s&csv=true")
PIT_URL = NSE + "/api/corporates-pit-gg?index=equities&from_date=%s&to_date=%s"
ARCHIVE_URL = NSE + "/api/corporates-pit?index=equities&symbol=%s"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
ACCEPT_JSON = "application/json, text/plain, */*"
IST = _dt.timedelta(hours=5, minutes=30)
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
SLOW_DAYS = 4          # 2 trading days for the person + 2 for the company
STATE_KEEP_DAYS = 30
MARKET_MODES = ("market purchase", "market sale")

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

DEALS_NOTE = ("Bulk and block deals are published by NSE after the close of the day they "
              "happen: the trade is done before anyone reads it. A big buyer is not a sign the "
              "price will rise. Not financial advice.")
PIT_NOTE = ("Insider disclosures are public and late: under SEBI's PIT Regulations 2015 "
            "(reg 7(2)) the person tells the company within 2 trading days of the trade and the "
            "company tells NSE within 2 more. Fast, not early; not inside information. "
            "Not financial advice.")


class InsiderError(Exception):
    pass


# ---------------------------------------------------------------- time and calendar

def to_ist(utc):
    return utc.replace(tzinfo=None) + IST


def ist_to_utc(local):
    return local.replace(tzinfo=None) - IST


def now_ist():
    return to_ist(_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)).replace(microsecond=0)


def nse_date(text):
    """'30-OCT-2026', '29-Oct-2026 17:17:29' or '2026-10-27' -> '2026-10-27' (or None)."""
    t = (text or "").strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", t)
    if m:
        return m.group(1)
    m = re.match(r"^(\d{1,2}-[A-Za-z]{3}-\d{4})", t)
    if not m:
        return None
    try:
        return _dt.datetime.strptime(m.group(1), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def dmy(d):
    return d.strftime("%d-%m-%Y")


def pretty(iso):
    return _dt.date.fromisoformat(iso).strftime("%d-%b-%Y") if iso else "?"


def is_trading_day(d):
    return d.weekday() < 5 and d.isoformat() not in NSE_HOLIDAYS


def trading_days_after(start_iso, end_iso):
    """Trading days after start, up to and including end (0 when the same day)."""
    if not start_iso or not end_iso:
        return None
    a, b = _dt.date.fromisoformat(start_iso), _dt.date.fromisoformat(end_iso)
    if b <= a:
        return 0
    n, d = 0, a
    while d < b:
        d += _dt.timedelta(days=1)
        if is_trading_day(d):
            n += 1
    return n


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


def value_text(v):
    """A rupee value: "₹428.86 crore" from one crore up, else "₹20,27,042"."""
    if v is None:
        return "value not given"
    if abs(v) >= 1e7:
        c = abs(v) / 1e7
        whole, frac = divmod(int(round(c * 100)), 100)
        return "%s₹%s.%02d crore" % ("-" if v < 0 else "", _group_digits(whole), frac)
    return money(round(v))


def qty_text(n):
    return "-" if n is None else _group_digits(n)


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
            pass  # the home page itself may answer 403; the cookies are what matter

    def get(self, url, name, check=None, accept=ACCEPT_JSON):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise InsiderError("offline: no file %s" % path)
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
                    raise InsiderError("NSE has nothing at %s (HTTP 404)" % url)
                last = "HTTP %s" % exc.code
            except (urllib.error.URLError, OSError) as exc:
                last = str(getattr(exc, "reason", exc))
            except ValueError as exc:
                last = str(exc)
        raise InsiderError("NSE refused or failed (%s) for %s. Its bot shield does this at "
                           "times: try again in a minute." % (last, url))

    def json(self, url, name):
        def check(data):
            json.loads(data.decode("utf-8-sig"))
        return json.loads(self.get(url, name, check).decode("utf-8-sig"))

    def text(self, url, name):
        def check(data):
            head = data[:300].lower()
            if b"<html" in head or b"<!doctype" in head:
                raise ValueError("an HTML page instead of CSV")
        return self.get(url, name, check, accept="text/csv, text/plain, */*").decode("utf-8-sig")

    def xml(self, url_or_path):
        if os.path.isfile(url_or_path):
            with open(url_or_path, "rb") as fh:
                return fh.read()

        def check(data):
            if b"<" not in data[:200]:
                raise ValueError("not XML")
        name = os.path.basename(urllib.parse.urlparse(url_or_path).path)
        return self.get(url_or_path, name, check, accept="application/xml, text/xml, */*")


def norm_symbol(text):
    s = (text or "").strip().upper()
    if s.endswith(".NS"):
        s = s[:-3]
    if not SYMBOL.match(s):
        raise InsiderError("%r is not an NSE symbol (like RELIANCE, M&M, BAJAJ-AUTO)" % text)
    return s


# ---------------------------------------------------------------- bulk and block deals

HEADERS = {"date": "date", "symbol": "symbol", "securityname": "name", "clientname": "client",
           "buy/sell": "side", "quantitytraded": "qty", "tradeprice/wght.avg.price": "price",
           "remarks": "remarks"}


def _int(text):
    try:
        return int(float(str(text).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _float(text):
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_deals(text, kind):
    """bulk.csv / block.csv, or the range CSV (BOM, padded headers, Indian commas)."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        return []
    cols = [HEADERS.get(re.sub(r"\s+", "", h).lower()) for h in rows[0]]
    if "symbol" not in cols or "qty" not in cols:
        raise InsiderError("the %s deals file has unexpected columns: %s" % (kind, rows[0]))
    out = []
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        d = {c: (r[i].strip() if i < len(r) else "") for i, c in enumerate(cols) if c}
        qty, price = _int(d.get("qty")), _float(d.get("price"))
        out.append({"kind": kind, "date": nse_date(d.get("date")), "symbol": d.get("symbol", "").upper(),
                    "name": d.get("name"), "client": d.get("client"),
                    "side": (d.get("side") or "").upper(), "qty": qty, "price": price,
                    "value": qty * price if qty is not None and price is not None else None,
                    "remarks": None if d.get("remarks") in (None, "", "-") else d.get("remarks")})
    mark_round_trips(out)
    return out


def mark_round_trips(deals):
    """A client that bought AND sold the same stock on the same day: churn, not a position."""
    sides = {}
    for d in deals:
        sides.setdefault((d["kind"], d["date"], d["symbol"], d["client"]), set()).add(d["side"])
    for d in deals:
        d["round_trip"] = sides[(d["kind"], d["date"], d["symbol"], d["client"])] >= {"BUY", "SELL"}
    return deals


def merge_block_and_bulk(deals):
    """A block deal that is also over 0.5% of the company is in both lists: one trade.
    Keep the block row, mark it, drop the bulk copy."""
    def same(d):
        return (d["date"], d["symbol"], d["client"], d["side"], d["qty"])
    blocks = {same(d) for d in deals if d["kind"] == "block"}
    out = []
    for d in deals:
        if d["kind"] == "bulk" and same(d) in blocks:
            continue
        d["also_bulk"] = d["kind"] == "block" and same(d) in {
            same(b) for b in deals if b["kind"] == "bulk"}
        out.append(d)
    return out


def load_deals(fetcher, kinds, start=None, end=None):
    """The latest day's files (no dates), or the range CSV."""
    out = []
    for kind in kinds:
        if start is None:
            text = fetcher.text(LATEST_URL % kind, "%s.csv" % kind)
        else:
            text = fetcher.text(RANGE_URL % (kind, dmy(start), dmy(end)),
                                "%s-range-%s-%s.csv" % (kind, dmy(start), dmy(end)))
        out.extend(parse_deals(text, kind))
    out = merge_block_and_bulk(out)
    out.sort(key=lambda d: (d["date"] or "", d["value"] or 0), reverse=True)
    return out


def filter_deals(deals, symbols=None, client=None, side=None, min_crore=None):
    out = []
    for d in deals:
        if symbols and d["symbol"] not in symbols:
            continue
        if client and client.lower() not in (d["client"] or "").lower():
            continue
        if side and d["side"] != side.upper():
            continue
        if min_crore is not None and (d["value"] or 0) < min_crore * 1e7:
            continue
        out.append(d)
    return out


def deal_key(d):
    return "%s|%s|%s|%s|%s|%s|%s" % (d["kind"], d["date"], d["symbol"], d["client"], d["side"],
                                     d["qty"], d["price"])


def render_deal(d, watch=()):
    line = "%s  %-5s %-12s %-4s %-40s %12s @ %s = %s" % (
        pretty(d["date"]), d["kind"].upper(), d["symbol"], d["side"], (d["client"] or "")[:40],
        qty_text(d["qty"]), money(d["price"]) if d["price"] is not None else "?",
        value_text(d["value"]))
    if d.get("round_trip"):
        line += "  (bought and sold the same day: churn, not a position)"
    if d.get("also_bulk"):
        line += "  (also a bulk deal: one trade, over 0.5% of the company)"
    if d.get("remarks"):
        line += "  [%s]" % d["remarks"]
    if d["symbol"] in watch:
        line += "  <- %s is on your list" % d["symbol"]
    return line


# ---------------------------------------------------------------- PIT disclosures

def parse_pit_list(data):
    """corporates-pit-gg rows -> filings (metadata only; the details are in each XML)."""
    rows = data.get("data", []) if isinstance(data, dict) else (data or [])
    out = []
    for r in rows:
        xml = r.get("xmlFileName") or r.get("xbrl")
        out.append({"app_id": str(r.get("appId") or ""), "symbol": (r.get("symbol") or "").upper(),
                    "company": (r.get("companyName") or "").strip(),
                    "broadcast": (r.get("broadcastDateTime") or "").strip(),
                    "broadcast_iso": nse_date(r.get("broadcastDateTime")),
                    "regulation": (r.get("regulation") or "").strip(),
                    "submission": r.get("typeOfSubmission"), "revision": r.get("revisionRemark"),
                    "prev_app_id": r.get("prevAppId"), "xml": xml, "ixbrl": r.get("ixbrl")})
    return out


def pit_key(f):
    return "pit|%s|%s" % (f["app_id"], os.path.basename(f["xml"] or ""))


def _clean(text):
    return re.sub(r"\s+", " ", (text or "").strip())


def disclosure(fields, meta=None, broadcast=None):
    """One person's transaction, from XML facts or an old-list row (already mapped)."""
    meta = meta or {}
    d = dict(fields)
    d.setdefault("symbol", meta.get("Symbol"))
    d.setdefault("company", meta.get("NameOfTheCompany"))
    d.setdefault("regulation", meta.get("DisclosureUnderRegulation"))
    d.setdefault("filed", meta.get("DateOfFiling"))
    d["broadcast"] = broadcast
    qty, value = d.get("qty"), d.get("value")
    d["price"] = round(value / qty, 2) if qty and value is not None else None
    d["value_looks_wrong"] = bool(d["price"] is not None and qty >= 100 and d["price"] < 1)
    d["market"] = (d.get("mode") or "").strip().lower() in MARKET_MODES
    when = nse_date(broadcast) if broadcast else nse_date(d.get("filed"))
    d["days_to_disclose"] = trading_days_after(d.get("to") or d.get("from"), when)
    d["slow"] = bool(d["days_to_disclose"] is not None and d["days_to_disclose"] > SLOW_DAYS)
    return d


def _pct(text):
    """XBRL "pure" values are fractions: 0.3049 is 30.49%."""
    v = _float(text)
    return None if v is None else round(v * 100, 4)


def parse_pit_xml(data, broadcast=None):
    """A PIT filing's XML (in-bse-co taxonomy) -> {"meta": ..., "rows": [one per person]}."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise InsiderError("the disclosure file is not valid XML: %s" % exc)
    by_ctx = {}
    for el in root:
        cid = el.get("contextRef")
        if cid is None:
            continue
        by_ctx.setdefault(cid, {})[el.tag.rsplit("}", 1)[-1]] = (el.text or "").strip()
    meta = by_ctx.pop("MainI", {})

    def order(cid):
        m = re.search(r"(\d+)$", cid)
        return (int(m.group(1)) if m else 0, cid)
    rows = []
    for cid in sorted(by_ctx, key=order):
        f = by_ctx[cid]
        if "NameOfThePerson" not in f and "SecuritiesAcquiredOrDisposedNumberOfSecurity" not in f:
            continue
        inst = f.get("TypeOfInstrument")
        if inst and inst.lower().startswith("any other") and f.get("TypeOfInstrumentOthers"):
            inst = f["TypeOfInstrumentOthers"]
        rows.append(disclosure({
            "person": _clean(f.get("NameOfThePerson")), "category": _clean(f.get("CategoryOfPerson")),
            "instrument": _clean(inst),
            "qty": _int(f.get("SecuritiesAcquiredOrDisposedNumberOfSecurity")),
            "value": _float(f.get("SecuritiesAcquiredOrDisposedValueOfSecurity")),
            "type": _clean(f.get("SecuritiesAcquiredOrDisposedTransactionType")),
            "mode": _clean(f.get("ModeOfAcquisitionOrDisposal")),
            "from": nse_date(f.get("DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyFromDate")),
            "to": nse_date(f.get("DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyToDate")),
            "intimated": nse_date(f.get("DateOfIntimationToCompany")),
            "exchange": _clean(f.get("ExchangeOnWhichTheTradeWasExecuted")),
            "before": _int(f.get("SecuritiesHeldPriorToAcquisitionOrDisposalNumberOfSecurity")),
            "before_pct": _pct(f.get("SecuritiesHeldPriorToAcquisitionOrDisposalPercentageOfShareholding")),
            "after": _int(f.get("SecuritiesHeldPostAcquistionOrDisposalNumberOfSecurity")),
            "after_pct": _pct(f.get("SecuritiesHeldPostAcquistionOrDisposalPercentageOfShareholding")),
            "note": _clean(f.get("DetailsOfChangeInHoldingOfSecuritiesOfPromotersEmployeeOrDirector"
                                 "OfAListedCompanyAndOtherSuchPersonsTextBlock"))[:300] or None,
        }, meta, broadcast))
    return {"meta": meta, "rows": rows}


def parse_pit_archive(data):
    """NSE's older /api/corporates-pit rows (details in the row) -> disclosures."""
    rows = data.get("data", []) if isinstance(data, dict) else (data or [])
    out = []
    for r in rows:
        broadcast = (r.get("date") or "").strip()
        out.append(disclosure({
            "symbol": (r.get("symbol") or "").upper(), "company": r.get("company"),
            "person": _clean(r.get("acqName")), "category": _clean(r.get("personCategory")),
            "instrument": _clean(r.get("secType")), "qty": _int(r.get("secAcq")),
            "value": _float(r.get("secVal")), "type": _clean(r.get("tdpTransactionType")),
            "mode": _clean(r.get("acqMode")), "from": nse_date(r.get("acqfromDt")),
            "to": nse_date(r.get("acqtoDt")), "intimated": nse_date(r.get("intimDt")),
            "exchange": _clean(r.get("exchange")), "before": _int(r.get("befAcqSharesNo")),
            "before_pct": _float(r.get("befAcqSharesPer")), "after": _int(r.get("afterAcqSharesNo")),
            "after_pct": _float(r.get("afterAcqSharesPer")),
            "regulation": "Regulation %s" % r.get("anex") if r.get("anex") else None,
            "xml": r.get("xbrl"), "note": None,
        }, broadcast=broadcast))
    return out


def filter_rows(rows, categories=None, side=None, market_only=False):
    out = []
    for d in rows:
        if categories and not any(c.lower() in (d.get("category") or "").lower() for c in categories):
            continue
        if side and not (d.get("type") or "").lower().startswith(side.lower()):
            continue
        if market_only and not d.get("market"):
            continue
        out.append(d)
    return out


def describe(d):
    """One person's transaction in words."""
    what = (d.get("type") or "?").lower()
    mode = d.get("mode") or ""
    if d.get("market"):
        act = mode.lower()
    elif mode:
        act = "%s, %s (not a market trade)" % (what, mode.lower())
    else:
        act = what
    inst = d.get("instrument") or "securities"
    if inst.lower() in ("equity", "equity shares"):
        inst = "shares"
    s = "%s %s: %s of %s %s, %s" % ((d.get("category") or "?").lower(), d.get("person") or "?",
                                    act, qty_text(d.get("qty")), inst, value_text(d.get("value")))
    if d.get("price") is not None and not d.get("value_looks_wrong"):
        s += " (about %s %s)" % (money(d["price"]), "a share" if inst == "shares" else "each")
    if d.get("value_looks_wrong"):
        s += " (the filed value looks wrong for this many shares: check the filing)"
    if d.get("from") and d.get("to") and d["from"] != d["to"]:
        s += ", traded %s to %s" % (pretty(d["from"]), pretty(d["to"]))
    elif d.get("to") or d.get("from"):
        s += ", traded %s" % pretty(d.get("to") or d.get("from"))
    if d.get("before") is not None and d.get("after") is not None:
        s += "; holding %s -> %s" % (qty_text(d["before"]), qty_text(d["after"]))
        if d.get("after_pct"):
            s += " (%.2f%% -> %.2f%%)" % (d.get("before_pct") or 0, d["after_pct"])
    if d.get("days_to_disclose") is not None:
        s += "; on NSE %d trading day%s later" % (d["days_to_disclose"],
                                                  "" if d["days_to_disclose"] == 1 else "s")
        if d.get("slow"):
            s += " (SLOW: over %d)" % SLOW_DAYS
    return s


def render_filing(f, rows=None, watch=()):
    out = ["%s IST  %-12s %s, %s (%s)%s" % (
        f["broadcast"], f["symbol"], f["company"], f["regulation"] or "?",
        (f.get("submission") or "?").lower(),
        "  <- %s is on your list" % f["symbol"] if f["symbol"] in watch else "")]
    out.append("    %s" % (f.get("ixbrl") or f.get("xml")))
    for d in rows or []:
        out.append("    - " + describe(d))
    return "\n".join(out)


def pit_url(start, end, symbol=None):
    url = PIT_URL % (dmy(start), dmy(end))
    if symbol:
        url += "&symbol=" + urllib.parse.quote(symbol, safe="")
        return url, "pit-%s-%s-%s.json" % (symbol, dmy(start), dmy(end))
    return url, "pit-%s-%s.json" % (dmy(start), dmy(end))


def read_filings(fetcher, filings, limit):
    """{filing key: rows} for up to limit filings; errors become notes."""
    details, notes = {}, []
    for f in filings[:limit]:
        if not f.get("xml"):
            continue
        try:
            details[pit_key(f)] = parse_pit_xml(fetcher.xml(f["xml"]), f["broadcast"])["rows"]
        except InsiderError as exc:
            notes.append("%s %s: could not read the disclosure (%s)" % (f["symbol"], f["broadcast"], exc))
    if len(filings) > limit:
        notes.append("%d more filings not read (--limit %d): narrow with --symbol or --days."
                     % (len(filings) - limit, limit))
    return details, notes


# ---------------------------------------------------------------- your symbols and state

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """The scanner's hits, the planned trade and your holdings, as NSE symbols."""
    out = set()

    def add(s):
        try:
            out.add(norm_symbol(s))
        except InsiderError:
            pass
    for h in (load_json(watchlist) or {}).get("hits", []):
        add(h.get("symbol") or h.get("ticker"))
    add((load_json(plan) or {}).get("symbol"))
    for p in (load_json(portfolio) or {}).get("positions", []):
        add(p.get("symbol") or p.get("ticker") or p.get("tradingsymbol"))
    return out


def watchlist(args):
    tickers = set()
    for t in args.tickers or []:
        tickers.add(norm_symbol(t))
    tickers |= watch_from_files(args.portfolio, args.watchlist, args.plan)
    return tickers


def load_state(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
        if isinstance(state.get("seen"), list):  # tolerate a hand-made list
            state["seen"] = {k: None for k in state["seen"]}
        return state
    return {"seen": {}, "last_check": None}


def save_state(path, state, today):
    cutoff = (today - _dt.timedelta(days=STATE_KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in state["seen"].items() if not v or v >= cutoff}
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")


# ---------------------------------------------------------------- CLI

def _parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--symbol", action="append", help="NSE symbol; repeat for more")
        p.add_argument("--today", metavar="YYYY-MM-DD", help="for reproducible runs (India date)")
        p.add_argument("--offline", metavar="DIR")
        p.add_argument("--json", action="store_true")
        p.add_argument("--tickers", nargs="*", help="flag rows in these symbols")
        p.add_argument("--watchlist", default="trader/watchlist.json")
        p.add_argument("--plan", default="trader/plan.json")
        p.add_argument("--portfolio", default="trader/portfolio.json")

    p = sub.add_parser("deals", help="bulk and block deals")
    common(p)
    p.add_argument("--days", type=int, default=1,
                   help="calendar days back including today (1 = NSE's latest day's files)")
    p.add_argument("--kind", choices=("bulk", "block", "both"), default="both")
    p.add_argument("--client", help="part of the client's name, e.g. 'mutual fund'")
    p.add_argument("--side", choices=("buy", "sell"))
    p.add_argument("--min-crore", type=float, help="only deals worth at least this many crore")
    p.add_argument("--mine", action="store_true", help="only your watchlist, plan and portfolio")
    p.add_argument("--limit", type=int, default=60)

    p = sub.add_parser("insider", help="SEBI PIT insider trading disclosures")
    common(p)
    p.add_argument("--days", type=int, default=3, help="calendar days back including today")
    p.add_argument("--read", action="store_true", help="read each filing's XML for the details")
    p.add_argument("--category", action="append",
                   help="promoter, director, KMP, designated...; reads the XML; repeat for more")
    p.add_argument("--side", choices=("buy", "sell"), help="reads the XML")
    p.add_argument("--market-only", action="store_true",
                   help="only market purchases and sales (reads the XML)")
    p.add_argument("--archive", action="store_true",
                   help="NSE's older detailed list (needs --symbol; stops in February 2026)")
    p.add_argument("--mine", action="store_true")
    p.add_argument("--limit", type=int, default=25, help="most filings to read (default 25)")

    p = sub.add_parser("read", help="read one PIT disclosure XML (a file or its URL)")
    p.add_argument("xml")
    p.add_argument("--broadcast", help="when NSE published it, e.g. '29-Oct-2026 17:17:29'")
    p.add_argument("--offline", metavar="DIR")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("check", help="what is new since the last check (alerts once)")
    common(p)
    p.add_argument("--days", type=int, default=3, help="PIT filings window (default 3 days)")
    p.add_argument("--kind", choices=("bulk", "block", "pit", "all"), action="append")
    p.add_argument("--mine", action="store_true", help="only your watchlist, plan and portfolio")
    p.add_argument("--category", action="append", help="PIT category filter (reads the XML)")
    p.add_argument("--read", action="store_true", help="read new PIT filings' XML")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--state", default="trader/state/insider.json")
    p.add_argument("--dry-run", action="store_true", help="do not update the state file")
    return ap


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = _parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    try:
        return _run(args)
    except (InsiderError, OSError, ValueError) as exc:
        print("insider: %s" % exc, file=sys.stderr)
        return 2


def _today(args):
    if getattr(args, "today", None):
        try:
            return _dt.date.fromisoformat(args.today)
        except ValueError:
            raise InsiderError("--today wants a date like 2026-10-31")
    return now_ist().date()


def _symbols(args):
    return {norm_symbol(s) for s in (getattr(args, "symbol", None) or [])}


def _run(args):
    if args.cmd == "read":
        fetcher = Fetcher(offline=args.offline)
        rep = parse_pit_xml(fetcher.xml(args.xml), args.broadcast)
        if args.json:
            print(json.dumps(rep, indent=2, ensure_ascii=False))
        else:
            m = rep["meta"]
            print("%s (%s), %s, filed %s." % (m.get("NameOfTheCompany") or "?", m.get("Symbol") or "?",
                                             m.get("DisclosureUnderRegulation") or "?",
                                             m.get("DateOfFiling") or "?"))
            for d in rep["rows"]:
                print("  - " + describe(d))
            print(PIT_NOTE)
        return 0 if rep["rows"] else 1

    today = _today(args)
    fetcher = Fetcher(offline=args.offline)
    watch = watchlist(args)
    symbols = _symbols(args)
    if getattr(args, "mine", False):
        if not watch:
            raise InsiderError("--mine but no symbols on trader/watchlist.json, plan.json or "
                               "portfolio.json (or --tickers)")
        symbols = symbols | watch if symbols else set(watch)
    if getattr(args, "days", 1) < 1:
        raise InsiderError("--days must be 1 or more")

    if args.cmd == "deals":
        kinds = ("bulk", "block") if args.kind == "both" else (args.kind,)
        if args.days == 1:
            deals = load_deals(fetcher, kinds)
        else:
            deals = load_deals(fetcher, kinds, today - _dt.timedelta(days=args.days - 1), today)
        picked = filter_deals(deals, symbols, args.client, args.side, args.min_crore)
        shown = picked[:args.limit]
        if args.json:
            print(json.dumps({"deals": shown, "total": len(picked)}, indent=2, ensure_ascii=False))
            return 0 if picked else 1
        days = sorted({d["date"] for d in deals if d["date"]})
        span = (pretty(days[-1]) if len(days) == 1 else
                "%s to %s" % (pretty(days[0]), pretty(days[-1])) if days else "no days")
        print("NSE %s deals, %s: %d shown of %d matching." % (
            " and ".join(kinds), span, len(shown), len(picked)))
        for d in sorted(shown, key=lambda d: d["symbol"] not in watch):
            print(render_deal(d, watch))
        if len(picked) > len(shown):
            print("... %d more (--limit, or narrow with --symbol, --client, --min-crore)."
                  % (len(picked) - len(shown)))
        print(DEALS_NOTE)
        return 0 if picked else 1

    if args.cmd == "insider":
        if args.archive:
            if not symbols:
                raise InsiderError("--archive needs --symbol")
            rows = []
            for s in sorted(symbols):
                rows += parse_pit_archive(fetcher.json(ARCHIVE_URL % urllib.parse.quote(s, safe=""),
                                                       "pit-archive-%s.json" % s))
            rows = filter_rows(rows, args.category, args.side, args.market_only)
            if args.json:
                print(json.dumps(rows, indent=2, ensure_ascii=False))
                return 0 if rows else 1
            print("NSE's older insider list (it stops in February 2026), %d rows:" % len(rows))
            for d in rows[:args.limit]:
                print("%s IST  %-12s - %s" % (d["broadcast"], d["symbol"], describe(d)))
            print(PIT_NOTE)
            return 0 if rows else 1
        start = today - _dt.timedelta(days=args.days - 1)
        filings = []
        if symbols and len(symbols) == 1:
            url, name = pit_url(start, today, next(iter(symbols)))
            filings = parse_pit_list(fetcher.json(url, name))
        else:
            url, name = pit_url(start, today)
            filings = [f for f in parse_pit_list(fetcher.json(url, name))
                       if not symbols or f["symbol"] in symbols]
        need_read = args.read or args.category or args.side or args.market_only
        details, notes = ({}, [])
        if need_read:
            details, notes = read_filings(fetcher, filings, args.limit)
        shown = []
        for f in filings:
            rows = details.get(pit_key(f))
            if need_read:
                if rows is None:
                    continue
                rows = filter_rows(rows, args.category, args.side, args.market_only)
                if not rows:
                    continue
            shown.append((f, rows))
        if args.json:
            print(json.dumps([dict(f, rows=r) for f, r in shown], indent=2, ensure_ascii=False))
            return 0 if shown else 1
        print("SEBI PIT disclosures on NSE, %s to %s: %d filing%s%s." % (
            pretty(start.isoformat()), pretty(today.isoformat()), len(shown),
            "" if len(shown) == 1 else "s", " (details read from each XML)" if need_read else
            " (add --read for who, what and how much)"))
        for f, rows in sorted(shown, key=lambda x: x[0]["symbol"] not in watch):
            print(render_filing(f, rows, watch))
        for n in notes:
            print("NOTE: " + n)
        print(PIT_NOTE)
        return 0 if shown else 1

    # check
    kinds = set(args.kind or ["all"])
    if "all" in kinds:
        kinds = {"bulk", "block", "pit"}
    state = load_state(args.state)
    seen = state.get("seen", {})
    fresh_deals, fresh_pit, notes = [], [], []
    deal_kinds = [k for k in ("bulk", "block") if k in kinds]
    if deal_kinds:
        try:
            deals = filter_deals(load_deals(fetcher, deal_kinds), symbols)
            fresh_deals = [d for d in deals if deal_key(d) not in seen]
        except InsiderError as exc:
            notes.append("deals: %s" % exc)
    details = {}
    if "pit" in kinds:
        try:
            start = today - _dt.timedelta(days=args.days - 1)
            url, name = pit_url(start, today)
            filings = [f for f in parse_pit_list(fetcher.json(url, name))
                       if not symbols or f["symbol"] in symbols]
            fresh_pit = [f for f in filings if pit_key(f) not in seen]
            if args.read or args.category:
                details, more = read_filings(fetcher, fresh_pit, args.limit)
                notes += more
                if args.category:
                    keep = []
                    for f in fresh_pit:
                        rows = filter_rows(details.get(pit_key(f)) or [], args.category)
                        if rows:
                            details[pit_key(f)] = rows
                            keep.append(f)
                    fresh_pit = keep
        except InsiderError as exc:
            notes.append("insider filings: %s" % exc)
    if args.json:
        print(json.dumps({"deals": fresh_deals,
                          "filings": [dict(f, rows=details.get(pit_key(f))) for f in fresh_pit],
                          "notes": notes}, indent=2, ensure_ascii=False))
    else:
        if not fresh_deals and not fresh_pit:
            print("Nothing new since %s." % (state.get("last_check") or "the last check"))
        def mine(sym):
            return sym not in watch  # your symbols first
        if fresh_deals:
            print("NEW bulk/block deals (%d):" % len(fresh_deals))
            for d in sorted(fresh_deals, key=lambda d: mine(d["symbol"])):
                print("  " + render_deal(d, watch))
        if fresh_pit:
            print("NEW insider disclosures (%d):" % len(fresh_pit))
            for f in sorted(fresh_pit, key=lambda f: mine(f["symbol"])):
                print("  " + render_filing(f, details.get(pit_key(f)), watch).replace("\n", "\n  "))
        for n in notes:
            print("NOTE: " + n)
        if fresh_deals:
            print(DEALS_NOTE)
        if fresh_pit:
            print(PIT_NOTE)
    if not args.dry_run:
        for d in fresh_deals:
            seen[deal_key(d)] = today.isoformat()
        for f in fresh_pit:
            seen[pit_key(f)] = today.isoformat()
        state["seen"] = seen
        state["last_check"] = now_ist().strftime("%Y-%m-%d %H:%M IST")
        save_state(args.state, state, today)
    return 0 if fresh_deals or fresh_pit else 1


if __name__ == "__main__":
    sys.exit(main())
