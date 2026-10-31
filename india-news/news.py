#!/usr/bin/env python3
"""News monitor for india-news. Standard library only.

Watches headlines and NSE corporate announcements for the stocks you watch or
hold and prints only what is new since the last check, newest first, with how
far each stock has moved today, a flag on anything that touches your symbols,
and a RESULTS DAY flag when a board meeting on financial results falls on the
day of your trade.

    python news.py                                    # watchlist + plan + portfolio symbols
    python news.py --tickers RELIANCE INFY HDFCBANK
    python news.py --tickers RELIANCE --all --hours 72  # everything in the last 3 days, seen or not
    python news.py --no-nse                           # headlines only
    python news.py --no-moves                         # skip the price line per symbol
    python news.py --trade-date 2026-10-30            # the day you plan to trade (default: the plan's date, else today)

(python3 on macOS/Linux.)

Your symbols, when --tickers is not given: every hit in trader/watchlist.json
(india-scanner), the symbol in trader/plan.json (india-exit) and the positions
in trader/portfolio.json. Symbols are NSE trading symbols (RELIANCE, M&M);
RELIANCE.NS is accepted.

Sources, one request each per symbol:
  - Yahoo Finance headline RSS for <SYMBOL>.NS:
    https://feeds.finance.yahoo.com/rss/2.0/headline?s=RELIANCE.NS&region=US&lang=en-US
    Often EMPTY for Indian stocks (RELIANCE.NS was empty on 2026-10-31 while
    INFY.NS had 15 items): that is normal, the tool says so, it is not an error.
  - NSE corporate announcements (the company's own filings to the exchange:
    results, board meetings, dividends, acquisitions, resignations...):
    https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=RELIANCE&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY
    with a browser User-Agent and Referer https://www.nseindia.com/. NSE's
    Akamai front can answer 403 at any time: the tool then visits the NSE home
    page once for cookies and retries once, and if that fails too it says NSE
    refused and to try again in a minute. It never loops.
  - The move: Yahoo Finance's chart endpoint (unofficial, can lag), the last
    price against yesterday's close:
    https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m

Times print in India time (IST, UTC+5:30). State: trader/state/news.json
remembers what you have seen (30 days), so an item shows once. Offline:
--offline DIR reads yahoo-<SYMBOL>.xml, chart-<SYMBOL>.json and
nse-<SYMBOL>.json from DIR. The tests use it.

Headlines are leads, not facts: open the story or the filing before acting,
and treat nothing here as advice.
"""

import argparse
import datetime as _dt
import email.utils
import gzip
import hashlib
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

YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=US&lang=en-US"
CHART = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
NSE_ANN = ("https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=%s"
           "&from_date=%s&to_date=%s")
NSE_HOME = "https://www.nseindia.com/"
NSE_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
TOOL_UA = "Mozilla/5.0 (compatible; india-trader-skill/1.0)"
NSE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/130.0 Safari/537.36")
NSE_HEADERS = {"User-Agent": NSE_UA, "Referer": "https://www.nseindia.com/",
               "Accept": "application/json, text/plain, */*", "Accept-Encoding": "gzip"}
IST = _dt.timedelta(hours=5, minutes=30)
UTC = _dt.timezone.utc
KEEP_DAYS = 30
MOVE_PCT = 3.0       # a stock this far from yesterday's close is flagged as moving
LOOKBACK_DAYS = 14   # NSE announcements fetched at least this far back, to find results dates
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
                                          "oct", "nov", "dec"))}

# Tags for headlines and NSE filings alike (case-insensitive).
KEYWORDS = [
    ("results", r"financial results?|\bresults?\b|\bearnings\b|\bEPS\b|\bguidance\b|\bprofit\b|\bQ[1-4]\b|"
                r"limited review"),
    ("board meeting", r"board meeting|meeting of (?:the )?board|board of directors[^.]{0,40}(?:meet|schedul)"),
    ("board outcome", r"outcome of (?:the )?board meeting"),
    ("dividend", r"\bdividend|record date"),
    ("bonus", r"\bbonus\b"),
    ("split", r"sub-?division|stock split|\bsplit\b"),
    ("buyback", r"buy-?back"),
    ("credit rating", r"credit rating|\bCRISIL\b|\bICRA\b|CARE Ratings|India Ratings|rating (?:upgrade|downgrade|reaffirm)"),
    ("acquisition", r"\bacqui(?:re|res|red|ring|sition)\b|\bmerger\b|amalgamation|\btakeover\b|substantial acq|"
                    r"\bSAST\b|open offer|scheme of arrangement|demerger"),
    ("people", r"\bresign|\bappoint|cessation|change in (?:director|management|key managerial)|\bCEO\b|\bCFO\b|"
               r"managing director|\bKMP\b|steps down"),
    ("pledge", r"\bpledge|encumbrance"),
    ("trading window", r"trading window"),
    ("fund raising", r"\bQIP\b|qualified institutional placement|rights issue|preferential (?:issue|allotment)|"
                     r"\bIPO\b|initial public offer|non-convertible debentures|\bNCDs?\b|offer for sale|\bOFS\b|"
                     r"block deal|allotment of securities"),
    ("clarification", r"news verification|clarification|rumou?r verification"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|price target|target price|\boutperform\b|\bunderperform\b|"
                r"\bbrokerage\b"),
    ("legal", r"lawsuit|\bsued\b|\bprobe\b|investigation|show cause|\bpenalty\b|\bcourt\b|tribunal|\bNCLT\b|"
              r"litigation|orders? passed"),
    ("risk", r"\bdefault(?:s|ed)?\b|insolvency|\bCIRP\b|bankrupt|fraud|delist|cyber|\bhack|breach|data leak|"
             r"dark web|outage|plant shut|\bstrike\b(?! off)"),
    ("halt", r"\bhalt(?:s|ed)?\b|suspension of trading|trading suspended|trading (?:pause|suspen)|"
             r"upper circuit|lower circuit"),
    ("regulatory", r"\bRBI\b|USFDA|\bFDA\b|\bCDSCO\b|warning letter|import alert|regulatory (?:approval|action)|"
                   r"licen[cs]e (?:revo|cancel|suspen)"),
    ("macro", r"repo rate|\bMPC\b|\bCPI\b|inflation|\bGDP\b|union budget|\bFIIs?\b|\bFPIs?\b|\bDIIs?\b|\bNifty\b|"
              r"\bSensex\b|\brupee\b|\bcrude\b|\btariffs?\b|\bFed\b|\bFOMC\b"),
]
# An NSE filing that carries one of these is marked "!": results, a results board meeting, a top
# management or auditor change, default / insolvency / fraud, delisting, a promoter pledge, a buyback,
# bonus or split, a cyber incident, a trading suspension.
IMPORTANT = re.compile(
    r"financial results?|board meeting intimation|outcome of (?:the )?board meeting|\bresign|cessation|"
    r"change in (?:management|key managerial|auditor)|managing director|chief executive|chief financial|"
    r"statutory auditor|\bdefault(?:s|ed)?\b|insolvency|\bCIRP\b|bankrupt|fraud|delist|pledge|encumbrance|"
    r"buy-?back|\bbonus\b|sub-?division|stock split|cyber|ransomware|suspension of trading", re.I)
RESULTS_WORD = re.compile(r"financial results?|\bresults\b", re.I)
RESULTS_ANCHOR = re.compile(r"board meeting|meeting of (?:the )?board|board of directors|"
                            r"announce[^.]{0,60}?results", re.I)
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|" \
       r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_FORMS = [
    (re.compile(_MON + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", re.I), "mdy"),
    (re.compile(r"(\d{1,2})(?:st|nd|rd|th)?[\s-]+(?:day of\s+)?" + _MON + r"\.?[\s,-]+(\d{4})", re.I), "dmy"),
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b"), "num"),
]


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time, money, symbols

def to_ist(utc):
    """Aware or naive UTC -> naive India wall-clock time (UTC+5:30, no daylight saving)."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(UTC)
    return utc.replace(tzinfo=None) + IST


def ist_to_utc(local):
    """Naive India time -> aware UTC."""
    return (local - IST).replace(tzinfo=UTC)


def parse_now(text):
    """--now: an India time ("2026-10-26 10:00", "10:00" for today, a trailing " IST" is fine) or an
    absolute one (a trailing Z or an offset). -> aware UTC."""
    if not text:
        return _dt.datetime.now(UTC).replace(microsecond=0)
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    if re.match(r"^\d{1,2}:\d{2}$", t):
        t = "%s %s" % (to_ist(_dt.datetime.now(UTC)).date().isoformat(), t)
    try:
        d = _dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        raise NewsError("--now wants an India time like \"2026-10-26 10:00\" (IST) or ...Z (UTC)")
    if d.tzinfo is None:
        return ist_to_utc(d)
    return d.astimezone(UTC)


def money(x):
    """Rupees with Indian digit grouping: ₹1,00,000, ₹1,193.60; .00 dropped."""
    x = float(x)
    sign = "-" if x < 0 else ""
    rupees, p = divmod(int(round(abs(x) * 100)), 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return "%s₹%s%s" % (sign, s, (".%02d" % p) if p else "")


def norm_symbol(text):
    """'reliance.ns' -> 'RELIANCE'. A .BO suffix is dropped too: this tool reads NSE's filings."""
    s = str(text or "").strip().upper()
    if s.endswith((".NS", ".BO")):
        s = s[:-3]
    if not SYMBOL.match(s):
        raise NewsError("%r is not an NSE symbol (like RELIANCE, M&M, BAJAJ-AUTO)" % text)
    return s


def yahoo_symbol(sym):
    """RELIANCE -> RELIANCE.NS, URL-quoted (M&M -> M%26M.NS)."""
    return urllib.parse.quote(norm_symbol(sym) + ".NS", safe="")


# ---------------------------------------------------------------- inputs

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def _sym(v):
    try:
        return norm_symbol(v)
    except NewsError:
        return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """{symbol: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = _sym(h.get("symbol") or h.get("ticker"))
        if sym:
            out[sym] = h.get("name")
    pl = load_json(plan) or {}
    sym = _sym(pl.get("symbol"))
    if sym:
        out.setdefault(sym, pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = _sym(p.get("tradingsymbol") or p.get("symbol") or p.get("ticker"))
        if sym:
            out[sym] = p.get("name") or out.get(sym)
    return out


def trade_dates(plan_path, watch, today, cli=None):
    """{symbol: YYYY-MM-DD}: --trade-date for all, else the plan's date for the plan's symbol, else today."""
    out = {t: (cli or today) for t in watch}
    if not cli:
        pl = load_json(plan_path) or {}
        sym = _sym(pl.get("symbol"))
        if sym in out and re.match(r"^\d{4}-\d{2}-\d{2}$", str(pl.get("date") or "")):
            out[sym] = str(pl["date"])
    return out


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    """One polite client. Yahoo gets the tool's User-Agent; NSE gets a browser User-Agent, a Referer and a
    cookie jar, with one retry after a visit to the NSE home page."""

    def __init__(self, offline=None, pause=0.5):
        self.offline = offline
        self.pause = pause
        self._last = 0.0
        self.urlopen = urllib.request.urlopen                       # tests swap these two
        self.nse_opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _wait(self):
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()

    @staticmethod
    def _body(resp):
        data = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data

    def get(self, url, name, nse=False):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        if nse:
            return self._nse(url)
        self._wait()
        req = urllib.request.Request(url, headers={"User-Agent": TOOL_UA, "Accept-Encoding": "gzip"})
        try:
            with self.urlopen(req, timeout=30) as resp:
                return self._body(resp)
        except urllib.error.HTTPError as exc:
            exc.close()
            raise NewsError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise NewsError("could not reach %s: %s" % (url, exc.reason))

    def _nse_once(self, url):
        self._wait()
        req = urllib.request.Request(url, headers=NSE_HEADERS)
        try:
            with self.nse_opener.open(req, timeout=30) as resp:
                data = self._body(resp)
        except urllib.error.HTTPError as exc:
            exc.close()
            return None, "HTTP %s" % exc.code
        except urllib.error.URLError as exc:
            return None, "could not reach www.nseindia.com: %s" % exc.reason
        if data.lstrip()[:1] not in (b"[", b"{"):
            return None, "an answer that is not JSON"
        return data, None

    def _nse(self, url):
        data, why = self._nse_once(url)
        if data is not None:
            return data
        self._wait()                         # prime the cookie jar: NSE's home page, once
        try:
            home = dict(NSE_HEADERS, Accept="text/html,application/xhtml+xml,*/*")
            with self.nse_opener.open(urllib.request.Request(NSE_HOME, headers=home), timeout=30) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            exc.close()                      # it often answers 403 but still sets the cookies
        except Exception:
            pass
        data, why2 = self._nse_once(url)
        if data is not None:
            return data
        raise NewsError("NSE refused the request (%s, then %s after fetching its home page for cookies): "
                        "try again in a minute" % (why, why2))


def nse_url(sym, now, hours):
    """The announcements for one symbol, from max(hours, LOOKBACK_DAYS) before now to today, India dates."""
    local = to_ist(now)
    back = max(_dt.timedelta(hours=hours), _dt.timedelta(days=LOOKBACK_DAYS))
    return NSE_ANN % (urllib.parse.quote(norm_symbol(sym), safe=""), (local - back).strftime("%d-%m-%Y"),
                      local.strftime("%d-%m-%Y"))


# ---------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_time(value):
    """RFC 822 (RSS), ISO 8601, or NSE's "30-Oct-2026 19:47:12" (India time) -> aware UTC, or None."""
    value = (value or "").strip()
    if not value:
        return None
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})(?: (\d{1,2}):(\d{2})(?::(\d{2}))?)?$", value)
    if m and m.group(2).lower() in MONTHS:
        local = _dt.datetime(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)),
                             int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0))
        return ist_to_utc(local)
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC)


def parse_rss(data, ticker):
    """Yahoo headline RSS -> items. An empty channel is a normal answer (no items)."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("RSS for %s is not valid XML: %s" % (ticker, exc))
    out = []
    for it in root.iter("item"):
        title = _text(it, "title")
        link = _text(it, "link")
        guid = _text(it, "guid") or link
        out.append({"id": "rss:" + guid, "source": "Yahoo Finance", "kind": "headline",
                    "ticker": ticker, "title": title, "summary": _text(it, "description"),
                    "link": link, "time": parse_time(_text(it, "pubDate"))})
    return out


def _clean(text, company):
    t = re.sub(r"\s+", " ", text or "").strip()
    if company:
        t = re.sub(r"^%s\s+has\s+(?:informed|submitted to)\s+the\s+Exchange,?\s*(?:about|regarding|that)?\s*"
                   % re.escape(company), "", t, flags=re.I)
    m = re.match(r"^['\"]\s*(.*?)\s*['\"]\.?$", t)       # 'Infosys Announces ...'. -> Infosys Announces ...
    return (m.group(1) if m else t).strip()


def _short(text, n=160):
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "..."


def parse_nse(data, ticker):
    """NSE corporate announcements JSON (a list) -> items."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except ValueError as exc:
        raise NewsError("NSE announcements for %s are not valid JSON: %s" % (ticker, exc))
    rows = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        raise NewsError("NSE announcements for %s came back in an unexpected shape" % ticker)
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        desc = re.sub(r"\s+", " ", str(r.get("desc") or "")).strip() or "Announcement"
        company = str(r.get("sm_name") or "").strip()
        raw = re.sub(r"\s+", " ", str(r.get("attchmntText") or "")).strip()
        text = _clean(raw, company)
        title = desc if (not text or text.lower().rstrip(".") == desc.lower()) else "%s: %s" % (desc, _short(text))
        when = parse_time(r.get("an_dt") or r.get("sort_date") or "")
        sid = str(r.get("seq_id") or "") or hashlib.sha1(
            ("%s|%s|%s" % (r.get("an_dt"), desc, r.get("attchmntFile"))).encode("utf-8")).hexdigest()[:16]
        out.append({"id": "nse:" + sid, "source": "NSE", "kind": "filing", "form": desc,
                    "ticker": ticker, "company": company, "title": title, "summary": raw,
                    "important": bool(IMPORTANT.search(desc + " " + raw)),
                    "link": r.get("attchmntFile") or NSE_PAGE, "time": when})
    return out


def find_dates(text):
    """[(position, date)] for every date written like "October 30, 2026", "30th October, 2026",
    "03-Nov-2026" or "30-10-2026" (day first)."""
    found = []
    for rx, form in DATE_FORMS:
        for m in rx.finditer(text):
            try:
                if form == "mdy":
                    d = _dt.date(int(m.group(3)), MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
                elif form == "dmy":
                    d = _dt.date(int(m.group(3)), MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
                else:
                    d = _dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except (ValueError, KeyError):
                continue
            found.append((m.start(), d))
    return sorted(found)


def results_meeting(item):
    """The date of a board meeting on financial results that this NSE announcement intimates, or None.
    The outcome of a meeting (the results themselves) is not an intimation."""
    if item.get("kind") != "filing" or item.get("time") is None:
        return None
    desc, text = item.get("form") or "", item.get("summary") or ""
    if "outcome" in desc.lower() or not RESULTS_WORD.search(desc + " " + text):
        return None
    announced = to_ist(item["time"]).date()
    dates = find_dates(text)
    spans = [(m.start(), m.end()) for m in RESULTS_ANCHOR.finditer(text)]
    if "board meeting intimation" in desc.lower():
        spans.append((0, len(text)))
    for start, end in spans:
        for pos, d in dates:
            if start <= pos <= end + 150 and d >= announced:
                return d
    return None


def results_dates(items, now, trade, lookback=LOOKBACK_DAYS):
    """[{ticker, date, results_day, announced, title, link, id}] for results board meetings from today on,
    announced in the last `lookback` days (and not after the pinned clock); earliest announcement wins."""
    today = to_ist(now).date()
    cutoff = now - _dt.timedelta(days=lookback)
    best = {}
    for it in sorted((i for i in items if i.get("kind") == "filing" and i.get("time")),
                     key=lambda i: i["time"]):
        if it["time"] < cutoff or it["time"] > now + _dt.timedelta(minutes=10):
            continue
        d = results_meeting(it)
        if d is None or d < today:
            continue
        key = (it["ticker"], d)
        if key not in best:
            best[key] = {"ticker": it["ticker"], "date": d.isoformat(),
                         "results_day": d.isoformat() == trade.get(it["ticker"]),
                         "trade_date": trade.get(it["ticker"]),
                         "announced": to_ist(it["time"]).strftime("%Y-%m-%d %H:%M IST"),
                         "title": it["title"], "link": it["link"], "id": it["id"]}
    return sorted(best.values(), key=lambda r: (r["date"], r["ticker"]))


def parse_chart(data, ticker):
    """Yahoo chart JSON -> {"ticker", "price", "prev_close", "pct", "time"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable" % ticker)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        raise NewsError("chart for %s has no price" % ticker)
    when = meta.get("regularMarketTime")
    return {"ticker": ticker, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2),
            "time": _dt.datetime.fromtimestamp(when, UTC) if when else None}


def collect_moves(fetcher, watch, errors=None):
    errors = [] if errors is None else errors
    out = []
    for t in sorted(watch):
        name = "chart-%s.json" % t
        try:
            try:
                data = fetcher.get(CHART % ("query2", yahoo_symbol(t)), name)
            except NewsError as exc:
                if fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
                    raise
                data = fetcher.get(CHART % ("query1", yahoo_symbol(t)), name)
            out.append(parse_chart(data, t))
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (t, exc))
    return out


def short_name(company):
    """'Infosys Limited' -> 'Infosys', for spotting the company in headlines."""
    n = re.sub(r"\s+(?:Limited|Ltd\.?)$", "", (company or "").strip(), flags=re.I)
    return n if len(n) >= 4 else None


def mention_patterns(watch):
    pats = {}
    for t, name in watch.items():
        e = re.escape(t)
        alts = [r"\(%s\)" % e, r"\b(?:NSE|BSE)\s*:\s*%s\b" % e, r"\b%s\.NS\b" % e]
        if name and len(name) >= 4:
            alts.append(r"\b%s\b" % re.escape(name).replace(r"\ ", r"[\s-]"))
        pats[t] = re.compile("|".join(alts))
    return pats


def tag(items, watch):
    """Which of your symbols each item touches, and why it matters."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        touches = {it["ticker"]} if it["ticker"] in watch else set()
        for t, rx in pats.items():
            if rx.search(hay):
                touches.add(t)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
        if it["kind"] == "filing" and results_meeting(it) and "results date" not in it["tags"]:
            it["tags"].insert(0, "results date")
    return items


def dedupe(items):
    """One item per story: same id, or same link, or same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"] if it["link"] != NSE_PAGE else "",
                "%s|%s|%s" % (it["ticker"] if it["kind"] == "filing" else "",
                              re.sub(r"\W+", " ", it["title"].lower()).strip(),
                              it["time"].date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the check

def collect(fetcher, watch, now, nse=True, hours=48, errors=None, fetched=None):
    """Headlines and NSE announcements for every symbol. `fetched` collects the symbols whose NSE
    announcements were read (an empty list from NSE counts)."""
    errors = [] if errors is None else errors
    fetched = [] if fetched is None else fetched
    items = []
    for t in sorted(watch):
        try:
            got = parse_rss(fetcher.get(YAHOO % yahoo_symbol(t), "yahoo-%s.xml" % t), t)
            if not got:
                errors.append("Yahoo Finance has no headlines for %s.NS (its feed is often empty for Indian "
                              "stocks: normal, not an error)" % t)
            items += got
        except NewsError as exc:
            errors.append("headlines for %s: %s" % (t, exc))
    if nse:
        for t in sorted(watch):
            try:
                items += parse_nse(fetcher.get(nse_url(t, now, hours), "nse-%s.json" % t, nse=True), t)
                fetched.append(t)
            except NewsError as exc:
                errors.append("NSE announcements for %s: %s" % (t, exc))
    return items


def select(items, state, now, hours=48, show_all=False):
    """Items within the window; only unseen ones unless show_all."""
    seen = state.setdefault("seen", {})
    cutoff = now - _dt.timedelta(hours=hours)
    ahead = now + _dt.timedelta(minutes=10)  # a pinned --now: nothing from after it
    out = []
    for it in items:
        if it["time"] is not None and (it["time"] < cutoff or it["time"] > ahead):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        when = (" at %s IST" % to_ist(m["time"]).strftime("%H:%M")) if m.get("time") else ""
        out.append("%-10s %s %+.2f%% vs yesterday's close %s%s  %s" % (
            m["ticker"], money(m["price"]), m["pct"], money(m["prev_close"]), when, flag))
    return out


def render_results(results, nse_read, now):
    out = []
    today = to_ist(now).date()
    for r in results:
        d = _dt.date.fromisoformat(r["date"])
        day = d.strftime("%a %Y-%m-%d")
        if r["results_day"]:
            out.append("RESULTS DAY  %s: financial results due %s, the day of your trade (NSE announcement %s). "
                       "Results can come out during the session, and the price can jump past any stop."
                       % (r["ticker"], day, r["announced"]))
        else:
            n = (d - today).days
            out.append("results      %s: financial results due %s (%s; NSE announcement %s)." % (
                r["ticker"], day, "today" if n == 0 else "tomorrow" if n == 1 else "in %d days" % n,
                r["announced"]))
    quiet = [t for t in sorted(nse_read) if t not in {r["ticker"] for r in results}]
    if quiet:
        out.append("No results date in NSE announcements of the last %d days for %s (not every company "
                   "writes it there: the board meetings list on nseindia.com has them all)." % (
                       LOOKBACK_DAYS, ", ".join(quiet)))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT, results=None, nse_read=()):
    out = render_moves(moves or [], move_pct)
    out += render_results(results or [], nse_read, now)
    if out:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M IST") if it["time"] else "time unknown"
        mark = "!" if it.get("important") else " "
        who = ",".join(it["touches"]) or it["ticker"]
        out.append("%s %s  %-11s %-13s %s" % (mark, when, who, it["source"], it["title"]))
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        extra.append(it["link"])
        out.append("      " + "  ".join(extra))
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s. Headlines are leads, not facts; open the story or the filing before acting. "
               "Not financial advice." % to_ist(now).strftime("%Y-%m-%d %H:%M IST"))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", "--symbols", nargs="*", help="default: watchlist + plan + portfolio")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-nse", action="store_true", help="headlines only, no NSE announcements")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per symbol")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--trade-date", help="the day you trade, YYYY-MM-DD (default: the plan's date, else today)")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help="pretend it is this time (no offset = India time, IST)")
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.tickers:
            watch = {norm_symbol(t): None for t in args.tickers}
            known = watch_from_files(args.portfolio, args.watchlist, args.plan)
            watch.update({t: known[t] for t in watch if known.get(t)})
        else:
            watch = watch_from_files(args.portfolio, args.watchlist, args.plan)
        now = parse_now(args.now)
        if args.trade_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", args.trade_date):
            raise NewsError("--trade-date wants YYYY-MM-DD")
    except NewsError as exc:
        print("news: %s" % exc, file=sys.stderr)
        return 2
    if not watch:
        print("news: no symbols. Pass --tickers, or run /india-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    state = load_json(args.state) or {}
    errors, nse_read = [], []
    fetcher = Fetcher(offline=args.offline)
    items = collect(fetcher, watch, now, nse=not args.no_nse, hours=args.hours, errors=errors, fetched=nse_read)
    for t in watch:                       # the company's own name from NSE, for spotting it in headlines
        if not watch[t]:
            watch[t] = next((short_name(i.get("company")) for i in items
                             if i["ticker"] == t and i.get("company")), None)
    items = dedupe(tag(items, watch))
    trade = trade_dates(args.plan, watch, to_ist(now).date().isoformat(), args.trade_date)
    results = results_dates(items, now, trade)
    for r in results:
        for it in items:
            if it["id"] == r["id"] and r["results_day"]:
                it["important"] = True
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({"checked": now.isoformat(), "moves": mv, "results": results,
                          "items": _json_ready(shown), "notes": errors}, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct, results, nse_read))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
