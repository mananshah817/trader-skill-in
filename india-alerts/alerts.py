#!/usr/bin/env python3
"""Price alerts and the NSE clock for india-alerts. Standard library only.

    python alerts.py clock                           # is NSE open? next open / close
    python alerts.py quote RELIANCE --write          # quotes -> trader/prices.json
    python alerts.py quote --holdings --write        # every symbol in your portfolio and plan
    python alerts.py add --from-plan                 # stop, target and flat-by from trader/plan.json
    python alerts.py add --symbol RELIANCE --stop 1183.6
    python alerts.py add --symbol RELIANCE --target 1203.6
    python alerts.py add --at 15:00 --label "flat before square-off" --symbol RELIANCE
    python alerts.py add --symbol INFY --pct 3
    python alerts.py add --opening-bell
    python alerts.py check                           # rings what hit, once; exit 1 if nothing rang
    python alerts.py check --now "2026-10-26 10:05" --offline tests/fixtures/india-alerts/reel-1005
    python alerts.py list
    python alerts.py reset reliance-plan-stop

(python3 on macOS/Linux.)

Rule types:
  above / target  the price is at or above "price"
  below / stop    the price is at or below "price"
  pct_move        the price moved "pct" percent or more from the previous close
                  (or from "ref" if you give one); "direction": "up" or "down"
  opening_bell    NSE opened today (rings once per trading day, at 09:15 IST,
                  or at the start of a special session)
  time            the India clock passed "at" (HH:MM, IST) on a trading day
                  (rings once per trading day)

A rule with "date" is only watched on that India date (rules from a plan are
for the plan's day only). An above / below alert rings ONCE and then stays
quiet until you reset it, unless the rule has "rearm": true (it re-arms when
the price crosses back). A pct_move alert rings at most once per day.

The target is NOT an order at the broker in this pack: only the stop-loss (SL)
order sits at Zerodha. When the target rings, the alert says to run
india-broker flatten (cancel the stop order, then sell).

Quotes (free, delayed, unofficial; for alerts only, never for timing an order):
  yahoo   https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS (falls
          back to query1 on a 429 or a 5xx; one retry with a pause on a 429;
          one request per symbol, half a second apart; the default). If the
          NSE (.NS) quote cannot be fetched at all, it tries the BSE listing
          (RELIANCE.BO) once and labels the price "yahoo-bse".
  bse     the BSE listing only (RELIANCE.BO), same endpoint.
  --offline DIR reads DIR/<source>-<SYMBOL>.json (or .json.gz) instead.
Stooq, the US pack's second source, has no Indian quotes and is gone.

NSE calendar: the 2026 trading holidays from NSE circular NSE/CMTR/71775 and
the Muhurat special session (NSE/CMTR/76941). NSE has not published 2027 yet:
for a later date weekdays count as trading days and the clock says so.
"""

import argparse
import datetime
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; india-trader-skill/1.0)"
PAUSE = 0.5    # seconds between requests: be polite to free endpoints
BACKOFF = 2.0  # seconds to wait before the one retry after a 429
YAHOO_HOSTS = ("query2.finance.yahoo.com", "query1.finance.yahoo.com")
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
IST = datetime.timedelta(hours=5, minutes=30)

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
SPECIAL_SESSIONS = {"2026-11-08": {"name": "Muhurat trading (Diwali)", "open": "14:20", "close": "15:30"}}
CALENDAR_YEARS = (2026,)
PRE_OPEN = datetime.time(9, 0)
OPEN = datetime.time(9, 15)
CAS = datetime.time(15, 15)      # F&O stocks: closing auction 15:15 to 15:35
CLOSE = datetime.time(15, 30)
SQUARE_OFF = ("Zerodha squares off MIS positions itself at 15:12 for F&O stocks and 15:25 for the others, "
              "charges ₹50 + GST for each order it squares off, and takes no new MIS orders after that.")


class AlertError(Exception):
    pass


# ---------------------------------------------------------------- money and symbols

def money(x):
    """Rupees with Indian digit grouping: ₹1,00,000, ₹12,34,567.50, ₹46.88; .00 dropped."""
    x = float(x)
    sign = "-" if x < 0 else ""
    paise = int(round(abs(x) * 100))
    rupees, p = divmod(paise, 100)
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
    """'reliance.ns' -> 'RELIANCE'; 'RELIANCE.BO' stays (it means the BSE listing)."""
    s = str(text or "").strip().upper()
    base = s[:-3] if s.endswith((".NS", ".BO")) else s
    if not SYMBOL.match(base):
        raise AlertError("%r is not an NSE symbol (like RELIANCE, M&M, BAJAJ-AUTO)" % text)
    return base + ".BO" if s.endswith(".BO") else base


# ---------------------------------------------------------------- time

def to_ist(utc):
    """Naive (or aware) UTC -> naive India wall-clock time. IST is UTC+5:30 all year."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(datetime.timezone.utc)
    return utc.replace(tzinfo=None) + IST


def ist_to_utc(local):
    return local - IST


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is India time; "10:00" alone is today."""
    if not text:
        return _utcnow()
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    if HHMM.match(t):
        t = "%s %s" % (to_ist(_utcnow()).date().isoformat(), t)
    t = t.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise AlertError("--now wants an India time like \"2026-10-26 10:00\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def _t(text):
    h, m = text.split(":")
    return datetime.time(int(h), int(m))


def trading_day(d):
    """(is_trading_day, reason). Weekends, the NSE holiday list and special sessions."""
    if d.isoformat() in SPECIAL_SESSIONS:
        return True, ""
    if d.weekday() >= 5:
        return False, "weekend"
    name = NSE_HOLIDAYS.get(d.isoformat())
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def session(d):
    """(open_time, close_time) in India time, or None if closed all day."""
    sp = SPECIAL_SESSIONS.get(d.isoformat())
    if sp:
        return _t(sp["open"]), _t(sp["close"])
    ok, _ = trading_day(d)
    if not ok:
        return None
    return OPEN, CLOSE


def calendar_note(year):
    return ("NSE has not published its %d holiday list in this tool yet: check nseindia.com before trading"
            % year)


def market_status(now_utc):
    local = to_ist(now_utc)
    d = local.date()
    ok, reason = trading_day(d)
    sp = SPECIAL_SESSIONS.get(d.isoformat())
    st = {"now_ist": local.strftime("%Y-%m-%d %H:%M"), "date": d.isoformat(),
          "weekday": local.strftime("%A"), "calendar_known": d.year in CALENDAR_YEARS,
          "open": False, "reason": reason, "special": sp["name"] if sp else None}
    s = session(d)
    if s:
        o, c = s
        st["opens"], st["closes"] = o.strftime("%H:%M"), c.strftime("%H:%M")
        now = local.time()
        if o <= now < c:
            st["open"] = True
            if sp:
                st["reason"] = "special session: " + sp["name"]
            elif now >= CAS:
                st["reason"] = "regular session (F&O stocks are in the closing auction: no stop-loss orders there)"
            else:
                st["reason"] = "regular session"
        elif now < o:
            st["reason"] = ("pre-open (09:00 to 09:15): orders queue, no trading yet"
                            if not sp and now >= PRE_OPEN else "before the open")
        else:
            st["reason"] = "after the close"
    # next open
    nd = d if (s and local.time() < s[0]) else d + datetime.timedelta(days=1)
    for _ in range(15):
        ns = session(nd)
        if ns:
            st["next_open"] = "%s %s" % (nd.isoformat(), ns[0].strftime("%H:%M"))
            if nd.isoformat() in SPECIAL_SESSIONS:
                st["next_open_special"] = SPECIAL_SESSIONS[nd.isoformat()]["name"]
            if nd.year not in CALENDAR_YEARS:
                st["next_open_calendar_known"] = False
            break
        nd += datetime.timedelta(days=1)
    notes = []
    for y in sorted({d.year, nd.year}):
        if y not in CALENDAR_YEARS:
            notes.append(calendar_note(y))
    st["calendar_notes"] = notes
    return st


# ---------------------------------------------------------------- quotes

def yahoo_symbol(sym, exchange="NSE"):
    """RELIANCE -> RELIANCE.NS (BSE: .BO), URL-quoted: M&M -> M%26M.NS."""
    base = sym[:-3] if sym.endswith((".NS", ".BO")) else sym
    suffix = ".BO" if (exchange == "BSE" or sym.endswith(".BO")) else ".NS"
    return urllib.parse.quote(base + suffix, safe="")


def yahoo_url(sym, host=YAHOO_HOSTS[0], exchange="NSE"):
    return "https://%s/v8/finance/chart/%s?range=1d&interval=1d" % (host, yahoo_symbol(sym.upper(), exchange))


def parse_yahoo(text, sym, exchange="NSE"):
    try:
        data = json.loads(text)
        res = data["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise AlertError("Yahoo returned something unexpected for %s" % sym)
    if not res:
        err = (data.get("chart") or {}).get("error") or {}
        raise AlertError("Yahoo has no data for %s: %s" % (sym, err.get("description", "unknown symbol")))
    m = res[0].get("meta", {})
    price = m.get("regularMarketPrice")
    if price is None:
        raise AlertError("Yahoo has no price for %s" % sym)
    t = m.get("regularMarketTime")
    when = (datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if t else "")
    q = (res[0].get("indicators", {}).get("quote") or [{}])[0]
    opens = [x for x in (q.get("open") or []) if x is not None]
    exch = "BSE" if (exchange == "BSE" or sym.endswith(".BO")) else "NSE"
    return {"symbol": sym.upper(), "price": float(price), "open": opens[0] if opens else None,
            "high": m.get("regularMarketDayHigh"), "low": m.get("regularMarketDayLow"),
            "prev_close": m.get("previousClose") or m.get("chartPreviousClose"), "time": when,
            "currency": m.get("currency") or "INR", "exchange": exch,
            "source": "yahoo" if exch == "NSE" else "yahoo-bse"}


_urlopen = urllib.request.urlopen   # tests swap these two
_sleep = time.sleep


def _get(url, timeout=20):
    """(HTTP status, body). A network failure is an AlertError."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with _urlopen(req, timeout=timeout) as r:
            return 200, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        finally:
            try:
                e.close()
            except Exception:
                pass
        return e.code, body
    except urllib.error.URLError as e:
        raise AlertError("could not reach %s: %s" % (url.split("/")[2], e.reason))


def fetch_yahoo(sym, timeout=20, exchange="NSE"):
    """query2 first. A 429 waits BACKOFF seconds and tries once more; a 429 or a 5xx
    after that moves on to query1. A 404 comes back as Yahoo's own error JSON."""
    last = None
    for host in YAHOO_HOSTS:
        url = yahoo_url(sym, host, exchange)
        code, text = _get(url, timeout)
        if code == 429:
            _sleep(BACKOFF)
            code, text = _get(url, timeout)
        if code == 200:
            return text
        if code == 404 and text.strip().startswith("{"):
            return text
        last = (url, code)
        if code != 429 and code < 500:
            break
    raise AlertError("%s answered HTTP %d" % (last[0].split("?")[0], last[1]))


def _offline_text(folder, source, sym):
    base = os.path.join(folder, "%s-%s.json" % (source, sym))
    if os.path.exists(base):
        with open(base, encoding="utf-8") as fh:
            return fh.read()
    if os.path.exists(base + ".gz"):
        with gzip.open(base + ".gz", "rt", encoding="utf-8") as fh:
            return fh.read()
    raise AlertError("no offline quote file %s" % base)


def _one(sym, exchange, offline, timeout=20):
    """Fetch (or read) and parse one quote. Fetch problems and parse problems are both AlertErrors;
    the first element says which: ("fetch", err) or ("data", err)."""
    base = sym[:-3] if sym.endswith(".BO") else sym
    try:
        if offline:
            text = _offline_text(offline, "bse" if exchange == "BSE" else "yahoo", base)
        else:
            text = fetch_yahoo(base, timeout, exchange)
    except AlertError as e:
        return None, ("fetch", e)
    try:
        q = parse_yahoo(text, base, exchange)
    except AlertError as e:
        return None, ("data", e)
    q["symbol"] = sym
    return q, None


def get_quotes(symbols, source="yahoo", offline=None, pause=PAUSE):
    """{SYM: quote} and [errors]. One request per symbol, with a pause between.
    source "yahoo": the NSE listing, then the BSE listing once if NSE could not be fetched.
    source "bse": the BSE listing only. A symbol written RELIANCE.BO always means BSE."""
    quotes, errors = {}, []
    n = 0
    for raw in symbols:
        try:
            sym = norm_symbol(raw)
        except AlertError as e:
            errors.append(str(e))
            continue
        exchange = "BSE" if (source == "bse" or sym.endswith(".BO")) else "NSE"
        if not offline and n and pause:
            _sleep(pause)
        n += 1
        q, err = _one(sym, exchange, offline)
        if q is None and exchange == "NSE" and err[0] == "fetch":
            if not offline and pause:
                _sleep(pause)
            q, err2 = _one(sym, "BSE", offline)
            if q is None:
                err = (err[0], AlertError("%s (BSE fallback: %s)" % (err[1], err2[1])))
        if q is None:
            errors.append(str(err[1]))
        else:
            quotes[sym] = q
    return quotes, errors


def write_prices(path, quotes, now_utc, source):
    old = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                old = json.load(fh)
        except (ValueError, OSError):
            old = {}
    prices = dict(old.get("prices", {}))
    detail = dict(old.get("detail", {}))
    stamp = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    for s, q in quotes.items():
        prices[s] = q["price"]
        detail[s] = {"prev_close": q.get("prev_close"), "time": q.get("time"), "source": q.get("source"),
                     "exchange": q.get("exchange", "NSE"), "currency": q.get("currency") or "INR",
                     "fetched": stamp}
    doc = {"as_of": stamp, "source": source, "currency": "INR",
           "note": ("Delayed quotes in rupees for alerts and sizing checks. Your broker's price is the one that "
                    "fills. detail.fetched is when each price was read; detail.exchange says NSE or BSE."),
           "prices": prices, "detail": detail}
    _write(path, doc)
    return doc


def _write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def _read(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise AlertError("%s is not valid JSON: %s" % (path, e))


# ---------------------------------------------------------------- rules

PRICE_TYPES = {"above", "below", "stop", "target", "pct_move", "opening_bell", "time"}
NO_QUOTE = {"opening_bell", "time"}


def _hhmm(text):
    m = HHMM.match(str(text or "").strip())
    if not m:
        return None
    return datetime.time(int(m.group(1)), int(m.group(2)))


def validate(rule):
    t = rule.get("type")
    if t not in PRICE_TYPES:
        raise AlertError("rule %s: unknown type %r" % (rule.get("id"), t))
    if t not in NO_QUOTE and not rule.get("symbol"):
        raise AlertError("rule %s needs a symbol" % rule.get("id"))
    if rule.get("symbol"):
        norm_symbol(rule["symbol"])
    if t in ("above", "below", "stop", "target") and not isinstance(rule.get("price"), (int, float)):
        raise AlertError("rule %s needs a numeric price" % rule.get("id"))
    if t == "pct_move" and not isinstance(rule.get("pct"), (int, float)):
        raise AlertError("rule %s needs a numeric pct" % rule.get("id"))
    if t == "time" and _hhmm(rule.get("at")) is None:
        raise AlertError("rule %s needs \"at\" as an India time like \"15:00\"" % rule.get("id"))
    if rule.get("date") and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(rule["date"])):
        raise AlertError("rule %s: date must be YYYY-MM-DD" % rule.get("id"))


def active(rule, today):
    """A rule with a "date" is watched on that India date only."""
    return not rule.get("date") or str(rule["date"]) == today


def _rang_today(stamp, today):
    """Did a rule that rang at this UTC stamp ring on this India date?"""
    if not stamp:
        return False
    try:
        utc = datetime.datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return stamp[:10] == today
    return to_ist(utc).date().isoformat() == today


def _closes_text(status):
    if status.get("special"):
        return "the special session ends at %s IST" % status["closes"]
    return ("continuous trading ends at %s IST (15:15 for F&O stocks, which go into the closing auction)"
            % status["closes"])


def evaluate(rules, quotes, status, state, now_utc):
    """Pure function. Returns (rings, new_state). Each ring: {id, symbol, message}."""
    state = json.loads(json.dumps(state or {}))
    rung = state.setdefault("rung", {})
    today = status["date"]
    local = to_ist(now_utc)
    stamp = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    rings = []
    for r in rules:
        validate(r)
        rid = r.get("id") or "%s-%s" % (r.get("symbol", "market"), r["type"])
        if not active(r, today):
            continue
        typ = {"stop": "below", "target": "above"}.get(r["type"], r["type"])
        if typ == "opening_bell":
            if status["open"] and not _rang_today(rung.get(rid), today):
                if status.get("special"):
                    msg = ("Opening bell: NSE special session, %s, is open (%s IST) until %s IST."
                           % (status["special"], status["now_ist"], status["closes"]))
                else:
                    msg = ("Opening bell: NSE is open (%s IST), %s."
                           % (status["now_ist"], _closes_text(status)))
                rings.append({"id": rid, "symbol": "", "message": msg})
                rung[rid] = stamp
            continue
        if typ == "time":
            at = _hhmm(r.get("at"))
            if status.get("opens") and local.time() >= at and not _rang_today(rung.get(rid), today):
                label = r.get("label") or "time alert"
                msg = "%s%s: it is %s IST (set for %s)" % (label[:1].upper(), label[1:],
                                                           local.strftime("%H:%M"), at.strftime("%H:%M"))
                msg += (", %s." % _closes_text(status)) if status["open"] else ", the market has closed."
                sym = str(r.get("symbol") or "").upper()
                if sym:
                    msg += (" If %s is still open, close it: india-broker flatten %s builds the sell ticket "
                            "(cancel the open stop order first), then india-risk and your yes. %s"
                            % (sym, sym, SQUARE_OFF))
                rings.append({"id": rid, "symbol": sym, "message": msg})
                rung[rid] = stamp
            continue
        sym = norm_symbol(r["symbol"])
        q = quotes.get(sym)
        if not q:
            continue
        p = q["price"]
        if typ in ("above", "below"):
            hit = p >= r["price"] if typ == "above" else p <= r["price"]
            if hit and rid not in rung:
                word = "at or above" if typ == "above" else "at or below"
                label = {"stop": "stop", "target": "target"}.get(r["type"], "alert level")
                msg = "%s %s %s: %s your %s %s%s." % (
                    sym, "is" if status["open"] else "last", money(p), word, label, money(r["price"]),
                    (" (" + r["note"] + ")") if r.get("note") else "")
                if r["type"] == "stop":
                    msg += (" The SL order at Zerodha should have triggered: check get_orders and record the fill "
                            "(ticket.py fill). An SL-limit can stay unfilled in a fast fall or a gap through its "
                            "limit price: if it is still open, india-broker flatten %s." % sym)
                elif r["type"] == "target":
                    msg += (" The target is not an order at the broker: to take it, india-broker flatten %s "
                            "(cancel the stop order, then sell), then india-risk and your yes." % sym)
                rings.append({"id": rid, "symbol": sym, "message": msg})
                rung[rid] = stamp
            elif not hit and rid in rung and r.get("rearm"):
                del rung[rid]
        elif typ == "pct_move":
            ref = r.get("ref") or q.get("prev_close") or q.get("open")
            if not ref:
                continue
            move = 100.0 * (p / float(ref) - 1)
            want = r.get("direction")
            hit = abs(move) >= r["pct"] and (not want or (want == "up") == (move > 0))
            if hit and not _rang_today(rung.get(rid), today):
                rings.append({"id": rid, "symbol": sym, "message": "%s moved %+.2f%% to %s (from %s)."
                              % (sym, move, money(p), money(ref))})
                rung[rid] = stamp
    state["last_check"] = stamp
    return rings, state


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x).rstrip("0").rstrip(".")


def rules_from_plan(plan, cfg_flat_by=None):
    """trader/plan.json -> a stop alert, a target alert and a flat-by time alert, for the plan's day."""
    if not isinstance(plan, dict) or not plan.get("symbol"):
        raise AlertError("the plan has no symbol (run india-exit first)")
    sym = norm_symbol(plan["symbol"])
    out = []
    for key, typ, word in (("stop", "stop", "plan stop"), ("target", "target", "plan target")):
        v = plan.get(key)
        if not isinstance(v, (int, float)) or v <= 0:
            raise AlertError("the plan has no %s (run india-exit)" % key)
        label = plan.get("%s_label" % key)
        out.append({"id": "%s-plan-%s" % (sym.lower(), key), "symbol": sym, "type": typ, "price": float(v),
                    "note": word + (": " + label if label else "")})
    flat = plan.get("flat_by") or cfg_flat_by or "15:00"
    if _hhmm(flat) is None:
        raise AlertError("the plan's flat_by %r is not a time like 15:00" % flat)
    out.append({"id": "%s-flat-by" % sym.lower(), "symbol": sym, "type": "time", "at": flat,
                "label": "flat before square-off"})
    if plan.get("date"):
        for r in out:
            r["date"] = str(plan["date"])
    return out


# ---------------------------------------------------------------- commands

def holdings(a):
    syms = []
    pf = _read(a.portfolio, {})
    for p in pf.get("positions", []) if isinstance(pf, dict) else []:
        syms.append(str(p.get("tradingsymbol") or p.get("symbol") or p.get("ticker") or "").upper())
    plan = _read(a.plan, {})
    if isinstance(plan, dict) and plan.get("symbol"):
        syms.append(str(plan["symbol"]).upper())
    out = []
    for s in syms:
        try:
            out.append(norm_symbol(s))
        except AlertError:
            pass
    return out


def _dedupe(items):
    out = []
    for s in items:
        if s and s not in out:
            out.append(s)
    return out


def _ist_stamp(stamp):
    try:
        utc = datetime.datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return stamp or ""
    return to_ist(utc).strftime("%Y-%m-%d %H:%M IST")


def cmd_clock(a):
    st = market_status(parse_now(a.now))
    if a.json:
        print(json.dumps(st, indent=2, ensure_ascii=False))
    else:
        print("India %s IST (%s): NSE %s, %s." % (st["now_ist"], st["weekday"],
                                                 "OPEN" if st["open"] else "closed", st["reason"]))
        if st.get("special"):
            print("Today's special session: %s, %s to %s IST. india-risk blocks new entries in a special "
                  "session unless allow_special_sessions is true." % (st["special"], st["opens"], st["closes"]))
        elif st.get("opens"):
            print("Today's session: pre-open 09:00 to 09:15, continuous trading %s to %s IST. F&O stocks stop "
                  "at 15:15 and go into the closing auction (15:15 to 15:35), where stop-loss orders are not "
                  "allowed." % (st["opens"], st["closes"]))
            print(SQUARE_OFF)
        if not st["open"] and st.get("next_open"):
            print("Next open: %s IST%s." % (st["next_open"], (" (special session: %s)" % st["next_open_special"])
                                            if st.get("next_open_special") else ""))
        for n in st["calendar_notes"]:
            print(n + ".")
    return 0 if st["open"] else 1


def cmd_quote(a):
    syms = _dedupe([norm_symbol(s) for s in a.symbols] + (holdings(a) if a.holdings else []))
    if not syms:
        raise AlertError("which symbols? alerts.py quote RELIANCE INFY, or --holdings")
    now = parse_now(a.now)
    quotes, errors = get_quotes(syms, a.source, a.offline)
    if a.write and quotes:
        write_prices(a.write, quotes, now, a.source)
    if a.json:
        print(json.dumps({"quotes": quotes, "errors": errors}, indent=2, ensure_ascii=False))
    else:
        for s in syms:
            q = quotes.get(s)
            if q:
                chg = ""
                if q.get("prev_close"):
                    chg = " (%+.2f%% vs prev close %s)" % (100.0 * (q["price"] / q["prev_close"] - 1),
                                                          money(q["prev_close"]))
                print("%-10s %s%s  %s  [%s]" % (s, money(q["price"]), chg, _ist_stamp(q.get("time")),
                                                q["source"]))
        for e in errors:
            print("error: " + e)
        if a.write and quotes:
            print("Wrote %d price(s) to %s." % (len(quotes), a.write))
        print("Quotes are free, delayed and unofficial: for alerts only. Your broker's price is the one that fills.")
    return 0 if quotes else 1


def cmd_check(a):
    doc = _read(a.rules, None)
    if not doc or not doc.get("rules"):
        raise AlertError("no rules in %s: add some with alerts.py add --from-plan" % a.rules)
    rules = doc["rules"]
    now = parse_now(a.now)
    status = market_status(now)
    watched = [r for r in rules if active(r, status["date"])]
    syms = _dedupe([norm_symbol(r["symbol"]) for r in watched
                    if r.get("type") not in NO_QUOTE and r.get("symbol")])
    quotes, errors = (get_quotes(syms, a.source, a.offline) if syms else ({}, []))
    state = _read(a.state, {})
    rings, state = evaluate(rules, quotes, status, state, now)
    if not a.no_write:
        _write(a.state, state)
        if quotes:
            write_prices(a.prices_out, quotes, now, a.source)
    if a.json:
        print(json.dumps({"market": status, "rings": rings, "errors": errors,
                          "quotes": {s: q["price"] for s, q in quotes.items()}}, indent=2, ensure_ascii=False))
    else:
        print("NSE %s (%s IST, %s)." % ("OPEN" if status["open"] else "closed", status["now_ist"],
                                        status["reason"]))
        for n in status["calendar_notes"]:
            print(n + ".")
        for r in rings:
            print("ALERT " + r["message"])
        if not rings:
            print("Nothing hit. %d rule(s) watched today%s." % (
                len(watched), (", " + ", ".join("%s %s" % (s, money(quotes[s]["price"])) for s in syms
                                                if s in quotes)) if quotes else ""))
        for e in errors:
            print("error: " + e)
        if rings:
            print("An alert is information, not an order. Only the stop-loss (SL) order sits at Zerodha; the "
                  "target is watched here, not placed. Check get_orders and get_positions and record any fill "
                  "(ticket.py fill). To close a position yourself: ticket.py flatten, then india-risk and your yes.")
    return 0 if rings else 1


def cmd_add(a):
    doc = _read(a.rules, {"rules": []})
    doc.setdefault("rules", [])
    if a.from_plan:
        plan = _read(a.plan, None)
        if plan is None:
            raise AlertError("no plan at %s (run india-exit first)" % a.plan)
        cfg = _read(a.config, {}) if a.config else {}
        new = rules_from_plan(plan, (cfg or {}).get("flat_by"))
        ids = {r["id"] for r in new}
        doc["rules"] = [r for r in doc["rules"] if r.get("id") not in ids] + new
        state = _read(a.state, {})
        if state.get("rung"):
            for i in ids:
                state["rung"].pop(i, None)
            _write(a.state, state)
        _write(a.rules, doc)
        for r in new:
            print("Added %s: %s" % (r["id"], json.dumps(r, ensure_ascii=False)))
        print("Run alerts.py check every few minutes while the trade is open (a scheduled task or /loop). "
              "Free quotes are delayed; the SL order at Zerodha is what actually stops you out, and the target "
              "is only watched here: when it rings, india-broker flatten. " + SQUARE_OFF)
        return 0
    if a.opening_bell:
        rule = {"id": a.id or "opening-bell", "type": "opening_bell"}
    elif a.at:
        if _hhmm(a.at) is None:
            raise AlertError("--at wants an India time like 15:00")
        rule = {"id": a.id or ("time-%s" % a.at.replace(":", "")), "type": "time", "at": a.at,
                "label": a.label or "time alert"}
        if a.symbol:
            rule["symbol"] = norm_symbol(a.symbol)
    else:
        if not a.symbol:
            raise AlertError("--symbol is required (or --from-plan, --at, --opening-bell)")
        sym = norm_symbol(a.symbol)
        if a.above is not None:
            rule = {"type": "above", "price": a.above}
        elif a.below is not None:
            rule = {"type": "below", "price": a.below}
        elif a.stop is not None:
            rule = {"type": "stop", "price": a.stop}
        elif a.target is not None:
            rule = {"type": "target", "price": a.target}
        elif a.pct is not None:
            rule = {"type": "pct_move", "pct": a.pct}
            if a.direction:
                rule["direction"] = a.direction
        else:
            raise AlertError("give one of --above, --below, --stop, --target or --pct")
        val = rule.get("price", rule.get("pct"))
        rule = dict({"id": a.id or ("%s-%s-%s" % (sym.lower(), rule["type"], _n(val)))}, symbol=sym, **rule)
        if a.rearm:
            rule["rearm"] = True
        if a.note:
            rule["note"] = a.note
    if a.date:
        rule["date"] = a.date
    validate(rule)
    if any(r.get("id") == rule["id"] for r in doc["rules"]):
        raise AlertError("a rule with id %s already exists" % rule["id"])
    doc["rules"].append(rule)
    _write(a.rules, doc)
    print("Added %s: %s" % (rule["id"], json.dumps(rule, ensure_ascii=False)))
    return 0


def cmd_list(a):
    doc = _read(a.rules, {"rules": []})
    state = _read(a.state, {})
    if not doc.get("rules"):
        print("No rules yet.")
        return 1
    for r in doc["rules"]:
        rid = r.get("id")
        rung = state.get("rung", {}).get(rid)
        print("%-24s %s%s" % (rid, json.dumps({k: v for k, v in r.items() if k != "id"}, ensure_ascii=False),
                              ("  (rang %s)" % _ist_stamp(rung)) if rung else ""))
    return 0


def cmd_reset(a):
    state = _read(a.state, {})
    if a.id not in state.get("rung", {}):
        print("%s has not rung (nothing to reset)." % a.id)
        return 1
    del state["rung"][a.id]
    _write(a.state, state)
    print("Re-armed %s." % a.id)
    return 0


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Price alerts and the NSE clock (india-alerts).")
    sub = ap.add_subparsers(dest="cmd")

    def net(p):
        p.add_argument("--source", default="yahoo", choices=["yahoo", "bse"],
                       help="yahoo: NSE (.NS) with a BSE (.BO) fallback; bse: BSE only")
        p.add_argument("--offline", metavar="DIR", help="read yahoo-<SYMBOL>.json / bse-<SYMBOL>.json, no network")
        p.add_argument("--now", help="pretend it is this time (no offset = India time, IST)")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("clock", help="is NSE open?")
    p.add_argument("--now")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("quote", help="latest delayed quotes")
    net(p)
    p.add_argument("symbols", nargs="*")
    p.add_argument("--holdings", action="store_true", help="add symbols from your portfolio and plan")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--write", nargs="?", const="trader/prices.json", metavar="PATH",
                   help="save to prices.json (default trader/prices.json)")
    p = sub.add_parser("check", help="check every rule, ring what hit")
    net(p)
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p.add_argument("--prices-out", default="trader/prices.json")
    p.add_argument("--no-write", action="store_true")
    p = sub.add_parser("add", help="add a rule")
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p.add_argument("--symbol")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--above", type=float)
    g.add_argument("--below", type=float)
    g.add_argument("--stop", type=float)
    g.add_argument("--target", type=float)
    g.add_argument("--pct", type=float)
    g.add_argument("--opening-bell", action="store_true")
    g.add_argument("--at", metavar="HH:MM", help="a time alert, India time (IST)")
    g.add_argument("--from-plan", action="store_true", help="stop, target and flat-by from the plan")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--config", default="trader/config.json", help="flat_by when the plan has none")
    p.add_argument("--label", help="what a time alert says, e.g. \"flat before square-off\"")
    p.add_argument("--date", help="watch this rule on this India date only (YYYY-MM-DD)")
    p.add_argument("--direction", choices=["up", "down"])
    p.add_argument("--rearm", action="store_true")
    p.add_argument("--note")
    p.add_argument("--id")
    p = sub.add_parser("list", help="list the rules")
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p = sub.add_parser("reset", help="re-arm a rule that rang")
    p.add_argument("id")
    p.add_argument("--state", default="trader/state/alerts.json")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"clock": cmd_clock, "quote": cmd_quote, "check": cmd_check, "add": cmd_add,
                "list": cmd_list, "reset": cmd_reset}[a.cmd](a)
    except AlertError as e:
        print("alerts.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
