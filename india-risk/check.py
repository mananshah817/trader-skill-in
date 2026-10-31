#!/usr/bin/env python3
"""Pre-trade risk check for india-risk. Standard library only, no network.

Reads the proposed orders (trader/orders.json, Zerodha Kite place_order
fields) and checks every one against your rules (trader/config.json), your
account (trader/portfolio.json), prices (trader/prices.json) and what was
placed today (trader/state/broker.json). Each order gets PASS or BLOCK with
the reasons. Orders are checked in file order as if each PASS had filled, so
buys that are fine one by one but break a limit together are caught.

    python check.py                                 # the trader/ files, writes trader/state/risk.json
    python check.py --now "2026-10-26 10:00"        # pin the clock (India time, IST)
    python check.py --orders trader/orders.json --json
    python check.py --no-write                      # check without recording the verdicts

(python3 on macOS/Linux.)

Entries, stops and exits. An ENTRY opens or adds to a position (a BUY, or a
SELL that would go short). A STOP is the protective SELL leg (role "stop")
of an entry: it is tied to its entry being in this batch, or to shares you
already hold. An EXIT only reduces what you hold. Getting out must always be
possible: a stop or an exit is checked for format, asset and short sales
only; every other rule turns into a warning for it.

What blocks an order (every order):

  format        Kite's fields: exchange NSE or BSE, transaction_type BUY or
                SELL, variety regular, product MIS (CNC only to sell delivery
                shares), quantity a positive whole number, order_type LIMIT
                with a price, SL with trigger_price and price (price at or
                below the trigger for a SELL), SL-M with trigger_price;
                MARKET is blocked (Kite wants market protection and the Kite
                MCP place_order tool cannot send it); prices on the tick;
                validity DAY, IOC or TTL; a tag of at most 20 letters,
                digits and dashes
  asset         derivatives (NFO, BFO, futures and options symbols) always:
                this pack is cash equity only; anything that is not an NSE
                or BSE equity symbol; trade-for-trade series (BE, BZ)
  short         selling more than you hold, unless "allow_short": true
  stop leg      a role "stop" order whose entry is not in this batch (or was
                blocked) and whose shares you do not hold

What blocks an entry (on top):

  blocked       a symbol on your "blocked" list
  size          under min_order_inr, over max_order_inr
  position      the stock would be over max_position_pct of the account
  cash          the buy would dip into your cash buffer, or need margin
  duplicate     the same symbol and side twice in this batch, or already
                placed today (trader/state/broker.json)
  daily limit   max_orders_per_day orders placed today
  stop          no stop while "require_stop": true (a role "stop" leg in the
                same group, or the order's plan stop); a stop on the wrong
                side of the entry; a stop leg that covers fewer shares
  loss          qty x (entry - stop) + charges over max_loss_per_trade_inr.
                The entry is the limit price for a LIMIT order (the most it
                pays); charges are Zerodha's intraday charges for the round
                trip at the entry and the stop ("include_charges": true)
  reward:risk   (target - entry) / (entry - stop) under min_reward_risk, from
                the price it expects to pay: the higher of the plan's entry
                and a fresh price (under 15 minutes old), never above the limit
  daily loss    the max losses (charges included) of today's entries plus
                this one over max_daily_loss_inr (win or lose, each counts in full)
  trades        max_trades_per_day entries already placed today
  window        before no_entries_before or after no_entries_after (India
                time), the market is closed (weekend, NSE holiday, outside
                09:15 to 15:30), a special session (Muhurat) unless
                "allow_special_sessions": true, or an MIS entry at or after
                flat_by

Exit code 0 when every order passes, 1 when any order is blocked.
The verdicts go to trader/state/risk.json with a fingerprint of each order,
so india-broker only shows an order as ready if THIS exact order passed today.
The tick schedule is NSE's, reviewed monthly: check Kite's instrument list if
an order is rejected for its price.
"""

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import sys

DEFAULTS = {
    "budget": None, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 80000,
    "min_order_inr": 500, "max_loss_per_trade_inr": 1000, "max_daily_loss_inr": 3000, "max_trades_per_day": 3,
    "max_orders_per_day": 10, "min_reward_risk": 1.0, "require_stop": True,
    "no_entries_before": "09:30", "no_entries_after": "14:30", "flat_by": "15:00",
    "exchange": "NSE", "product": "MIS", "include_charges": True,
    "paper": True, "blocked": [], "allow_margin": False, "allow_short": False, "allow_special_sessions": False,
}
KITE_FIELDS = ("variety", "exchange", "tradingsymbol", "transaction_type", "quantity", "product", "order_type",
               "price", "trigger_price", "validity", "validity_ttl", "disclosed_quantity", "tag")
VARIETIES = {"regular", "co", "amo", "iceberg", "auction"}
EXCHANGES = {"NSE", "BSE"}
DERIVATIVE_EXCHANGES = {"NFO", "BFO", "MCX", "CDS", "BCD", "NCO"}
ORDER_TYPES = {"MARKET", "LIMIT", "SL", "SL-M"}
VALIDITIES = {"DAY", "IOC", "TTL"}
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
DERIVATIVE = re.compile(r"(\d{2}[A-Z]{3}FUT|\d(CE|PE))$")
TRADE_FOR_TRADE = re.compile(r"-(BE|BZ|BL|IL|SM|ST)$")
TAG = re.compile(r"^[A-Za-z0-9\-]{1,20}$")
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
PAISA = 0.005
FRESH_MINUTES = 15
IST = datetime.timedelta(hours=5, minutes=30)
REF_WORDS = {"plan": "the plan's entry", "price": "the latest price", "limit": "the limit price",
             "trigger price": "the trigger price", "stale price": "an old price"}
ZERODHA_INTRADAY = {
    "brokerage_pct": 0.03,      # per executed order...
    "brokerage_max": 20.0,      # ...or ₹20, whichever is lower
    "stt_sell_pct": 0.025,      # sell side only
    "txn_pct": 0.00307,         # NSE transaction charge incl. IPF, both sides (BSE: 0.00375)
    "sebi_pct": 0.0001,         # ₹10 per crore, both sides
    "stamp_buy_pct": 0.003,     # buy side only
    "gst_pct": 18.0,            # on brokerage + transaction charge + SEBI fee
}
BSE_TXN_PCT = 0.00375

# NSE circular NSE/CMTR/71775 plus the NSE holiday API: weekday closures only.
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
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)


class RiskError(Exception):
    pass


# ---------------------------------------------------------------- time (India, UTC+5:30, no daylight saving)

def to_ist(utc):
    return utc.replace(tzinfo=None) + IST


def ist_to_utc(local):
    return local.replace(tzinfo=None) - IST


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is India time (IST);
    "10:00" alone is today in India; a trailing Z or offset is absolute."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    if re.match(r"^\d{1,2}:\d{2}$", t):
        today = to_ist(datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)).date()
        t = "%s %s" % (today.isoformat(), t.zfill(5))
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise RiskError("--now wants an India time like \"2026-10-26 10:00\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_utc(text):
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def hhmm(value, key):
    """"09:30" -> time(9, 30); None or "" -> None (that bound is off)."""
    if value in (None, "", False):
        return None
    m = HHMM.match(str(value).strip())
    if not m:
        raise RiskError("%s must be an India time like \"09:30\", got %r" % (key, value))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def session_for(d):
    """(open, close, name) for an India date; (None, None, why) when NSE is closed.
    A special session (Muhurat) has its name; a normal day has name ""."""
    sp = SPECIAL_SESSIONS.get(d.isoformat())
    if sp:
        return hhmm(sp["open"], "open"), hhmm(sp["close"], "close"), sp["name"]
    if d.weekday() >= 5:
        return None, None, "weekend"
    name = NSE_HOLIDAYS.get(d.isoformat())
    if name:
        return None, None, "NSE holiday: " + name
    return OPEN, CLOSE, ""


def window_check(now_ist, c):
    """Reasons an entry cannot be placed now (India time). Empty when the window is off."""
    before = hhmm(c.get("no_entries_before"), "no_entries_before")
    after = hhmm(c.get("no_entries_after"), "no_entries_after")
    if before is None and after is None:
        return [], []
    warns = []
    d, t = now_ist.date(), now_ist.time().replace(second=0, microsecond=0)
    if d.year not in CALENDAR_YEARS:
        warns.append("NSE has not published its %d holiday list in this tool yet: check nseindia.com before "
                     "trading" % d.year)
    op, cl, name = session_for(d)
    stamp = now_ist.strftime("%a %Y-%m-%d %H:%M")
    if op is None:
        return ["window: the market is closed today (%s, %s IST)" % (name, stamp)], warns
    if name:
        if not c.get("allow_special_sessions"):
            return ["window: today is a special session (%s, %s to %s IST); new entries are off in special "
                    "sessions (\"allow_special_sessions\": false)" % (name, op.strftime("%H:%M"),
                                                                      cl.strftime("%H:%M"))], warns
        warns.append("special session today: %s, %s to %s IST" % (name, op.strftime("%H:%M"), cl.strftime("%H:%M")))
    if t < op or now_ist.time() >= cl:
        return ["window: the market is closed (%s IST; the session is %s to %s)"
                % (stamp, op.strftime("%H:%M"), cl.strftime("%H:%M"))], warns
    if before is not None and t < before:
        return ["window: no new entries before %s IST (it is %s)" % (before.strftime("%H:%M"),
                                                                     t.strftime("%H:%M"))], warns
    if after is not None and t > after:
        return ["window: no new entries after %s IST (it is %s)" % (after.strftime("%H:%M"),
                                                                    t.strftime("%H:%M"))], warns
    return [], warns


def flat_by_check(now_ist, c):
    """A new MIS entry at or after flat_by is blocked."""
    fb = hhmm(c.get("flat_by"), "flat_by")
    if fb is None:
        return []
    t = now_ist.time().replace(second=0, microsecond=0)
    if t >= fb:
        return ["window: no new MIS entries at or after flat_by %s IST (it is %s); Zerodha squares off MIS "
                "itself at 15:12 for F&O stocks and 15:25 for the rest, for ₹50 + 18%% GST an order"
                % (fb.strftime("%H:%M"), t.strftime("%H:%M"))]
    return []


# ---------------------------------------------------------------- files and helpers

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise RiskError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise RiskError("%s is not valid JSON: %s" % (path, e))


def fingerprint(order):
    """Same function in india-broker/ticket.py: the exact order that was checked (Kite fields only)."""
    core = {k: order[k] for k in KITE_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _f(x):
    if isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _group_digits(n):
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
    """100000 -> "₹1,00,000", 767.69 -> "₹767.69", -500 -> "-₹500"."""
    p = int(round(abs(float(x)) * 100 + 1e-9))
    r, ps = divmod(p, 100)
    s = "₹" + _group_digits(r) + ("" if ps == 0 else ".%02d" % ps)
    return ("-" + s) if float(x) < 0 and p else s


def _paisa(x):
    """A price to the paisa, half up (1193.595 -> 1193.6)."""
    return math.floor(float(x) * 100 + 0.5 + 1e-9) / 100.0


def _p(x):
    return "%.2f" % float(x)


def tick_size(price):
    """NSE tick schedule (NSE/CMTR/67133, from 15 Apr 2025; reviewed monthly)."""
    p = float(price)
    if p < 250:
        return 0.01
    if p <= 1000:
        return 0.05
    if p <= 5000:
        return 0.10
    if p <= 10000:
        return 0.50
    if p <= 20000:
        return 1.00
    return 5.00


def tick_for(price, sym, cfg):
    over = cfg.get("tick_size") if isinstance(cfg.get("tick_size"), dict) else {}
    t = _f(over.get(sym))
    if not t or t <= 0:
        t = tick_size(price)
    return max(int(round(t * 100)), 1)


def on_tick(price, sym, cfg):
    p = float(price) * 100
    if abs(p - round(p)) > 1e-6:
        return False
    return int(round(p)) % tick_for(price, sym, cfg) == 0


def charges(buy_value, sell_value, rates=None, exchange="NSE"):
    """Zerodha intraday charges for a buy of buy_value and a sell of sell_value (rupees).
    Returns the breakdown, each part rounded to the paisa, with "total" (their sum).
    The same function is in india-budget/size.py."""
    r = dict(ZERODHA_INTRADAY)
    if str(exchange).upper() == "BSE" and "txn_pct" not in (rates or {}):
        r["txn_pct"] = BSE_TXN_PCT
    r.update(rates or {})
    buy, sell = max(float(buy_value or 0), 0.0), max(float(sell_value or 0), 0.0)

    def brokerage(v):
        return min(v * r["brokerage_pct"] / 100.0, r["brokerage_max"]) if v > 0 else 0.0
    brok = brokerage(buy) + brokerage(sell)
    txn = (buy + sell) * r["txn_pct"] / 100.0
    sebi = (buy + sell) * r["sebi_pct"] / 100.0
    out = {
        "brokerage": brok,
        "stt": sell * r["stt_sell_pct"] / 100.0,
        "txn": txn,
        "sebi": sebi,
        "stamp": buy * r["stamp_buy_pct"] / 100.0,
        "gst": (brok + txn + sebi) * r["gst_pct"] / 100.0,
    }
    out = {k: math.floor(v * 100 + 0.5 + 1e-7) / 100.0 for k, v in out.items()}
    out["total"] = round(sum(out.values()), 2)
    return out


def account(cfg, portfolio, prices):
    """Starting account: cash and position values. No portfolio = a fresh
    account funded with the budget."""
    warnings = []
    if portfolio is None:
        budget = _f(cfg.get("budget"))
        if not budget:
            raise RiskError("no trader/portfolio.json and no budget in the config: "
                            "nothing to check the orders against")
        return {"cash": budget, "shares": {}, "values": {}}, warnings
    cash = _f(portfolio.get("cash"))
    if cash is None:
        raise RiskError("portfolio.json needs a \"cash\" number")
    shares, values = {}, {}
    for p in portfolio.get("positions", []):
        t = str(p.get("ticker") or p.get("tradingsymbol") or p.get("symbol") or "").upper()
        if not t:
            continue
        n = _f(p.get("shares", p.get("quantity", p.get("qty"))))
        if n is None:
            n = sum(_f(l.get("shares")) or 0 for l in p.get("lots", []))
        shares[t] = shares.get(t, 0) + n
        if prices.get(t):
            values[t] = shares[t] * prices[t]
        else:
            basis = sum(_f(l.get("cost")) or 0 for l in p.get("lots", []))
            values[t] = basis
            warnings.append("no price for %s: valued at its cost basis %s" % (t, money(basis)))
    return {"cash": cash, "shares": shares, "values": values}, warnings


def rec_day(rec):
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


def rec_is_entry(rec):
    """india-broker logs the kind india-risk gave; older records: a buy is an entry."""
    k = rec.get("kind")
    if k:
        return k == "entry"
    return str(rec.get("side", "")).lower() == "buy"


def placed_today(state, today):
    """Orders recorded as placed today by india-broker (paper or live)."""
    out = []
    for rec in (state or {}).get("orders", []):
        if rec.get("status") == "placed" and rec_day(rec) == today:
            out.append(rec)
    return out


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


# ---------------------------------------------------------------- the check

def check(orders, cfg, portfolio=None, prices=None, state=None, today=None, now=None, price_times=None):
    """Pure function. Returns {"results": [...], "pass": n, "block": n, "warnings": [...]}.

    now          naive UTC (default: the real clock); its India date is "today"
    price_times  {SYM: naive UTC time of that price}; None = every price counts as fresh"""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    prices = {str(k).upper(): float(v) for k, v in (prices or {}).items() if _f(v)}
    now_utc = now or parse_now(None)
    now_ist = to_ist(now_utc)
    today = today or now_ist.date().isoformat()
    acct, top_warn = account(c, portfolio, prices)
    blocked = {str(b).upper().replace(".NS", "") for b in c.get("blocked") or []}
    done_today = placed_today(state, today)
    count_today = len(done_today)
    entries_today = [r for r in done_today if rec_is_entry(r)]
    loss_today = 0.0
    for r in entries_today:
        ml = _f(r.get("max_loss"))
        if ml is None:
            top_warn.append("an entry placed today (%s %s) has no max loss in the log; not counted in "
                            "the daily loss" % (r.get("side"), r.get("symbol")))
        else:
            loss_today += ml
    placed_keys = {(str(r.get("symbol", "")).upper(), str(r.get("side", "")).upper()) for r in done_today}
    win_reasons, win_warns = window_check(now_ist, c)
    flat_reasons = flat_by_check(now_ist, c)
    include = bool(c.get("include_charges", True))
    seen = set()
    results = []
    entry_results = {}                   # group -> the result of its entry in this batch
    batch_entries, batch_loss = 0, 0.0
    if not c.get("paper", True):
        top_warn.append("LIVE account (\"paper\": false): these orders use real money")
    top_warn.extend(win_warns)

    def fresh(sym):
        if sym not in prices:
            return None
        if price_times is None:
            return prices[sym]
        t = price_times.get(sym)
        if t is None:
            return None
        age = (now_utc - t).total_seconds()
        return prices[sym] if -60 <= age <= FRESH_MINUTES * 60 else None

    for i, o in enumerate(orders, 1):
        reasons, warns, info = [], [], []
        later = []                       # entry-only reasons, turned into warnings for a stop or an exit
        if not isinstance(o, dict):
            o = {}
        role = str(o.get("role") or "").strip().lower()
        group = o.get("group")
        sym = str(o.get("tradingsymbol") or "").strip().upper()
        exch = str(o.get("exchange") or "").strip().upper()
        side = str(o.get("transaction_type") or "").strip().upper()
        typ = str(o.get("order_type") or "").strip().upper()
        product = str(o.get("product") or "").strip().upper()
        variety = str(o.get("variety") or "").strip().lower()
        validity = str(o.get("validity") or "DAY").strip().upper()
        qty = o.get("quantity")
        plan = o.get("plan") if isinstance(o.get("plan"), dict) else {}
        value = None

        # format: Kite's place_order fields
        if not sym:
            reasons.append("format: no tradingsymbol" + (" (\"symbol\" is not a Kite field)" if o.get("symbol") else ""))
        elif sym.endswith(".NS") or sym.endswith(".BO"):
            reasons.append("format: on Kite the tradingsymbol is %s, not %s" % (sym[:-3], sym))
        if exch in DERIVATIVE_EXCHANGES:
            pass                         # an asset reason below
        elif not exch:
            reasons.append("format: no exchange (NSE or BSE)")
        elif exch not in EXCHANGES:
            reasons.append("format: exchange must be NSE or BSE, got %r" % o.get("exchange"))
        if variety not in VARIETIES:
            reasons.append("format: unknown variety %r (Kite: regular, co, amo, iceberg, auction)" % o.get("variety"))
        elif variety != "regular":
            reasons.append("format: this pack places regular orders only, not %s" % variety)
        if side not in ("BUY", "SELL"):
            reasons.append("format: transaction_type must be BUY or SELL, got %r" % o.get("transaction_type"))
        if not _is_int(qty) or qty <= 0:
            reasons.append("format: quantity must be a positive whole number (a JSON integer), got %r" % (qty,))
        if product not in ("MIS", "CNC"):
            reasons.append("format: product must be MIS (intraday), got %r; NRML and MTF are not used by "
                           "this pack" % o.get("product"))
        if typ not in ORDER_TYPES:
            reasons.append("format: unknown order_type %r (Kite: MARKET, LIMIT, SL, SL-M)" % o.get("order_type"))
        elif typ == "MARKET":
            reasons.append("format: a MARKET order needs market protection on Kite and the Kite MCP place_order "
                           "tool cannot send it: use a LIMIT (india-budget and the flatten ticket do)")
        for k in ("price", "trigger_price"):
            v = o.get(k)
            if v is None:
                continue
            if not _is_num(v) or v <= 0:
                reasons.append("format: %s must be a positive JSON number, got %r" % (k, v))
            elif sym and not on_tick(v, sym, c):
                reasons.append("format: %s %s is not on the %s tick (NSE's tick schedule, reviewed monthly: check "
                               "Kite's instrument list if an order is rejected for its price)"
                               % (k, v, _p(tick_for(v, sym, c) / 100.0)))
        price, trig = _f(o.get("price")), _f(o.get("trigger_price"))
        if typ == "LIMIT" and not price:
            reasons.append("format: a LIMIT order needs a price")
        if typ == "SL":
            if not trig or not price:
                reasons.append("format: an SL (stop-limit) order needs a trigger_price and a price")
            elif side == "SELL" and price > trig + PAISA:
                reasons.append("format: an SL SELL needs its price (%s) at or below its trigger_price (%s)"
                               % (_p(price), _p(trig)))
            elif side == "BUY" and price < trig - PAISA:
                reasons.append("format: an SL BUY needs its price (%s) at or above its trigger_price (%s)"
                               % (_p(price), _p(trig)))
        if typ == "SL-M" and not trig:
            reasons.append("format: an SL-M order needs a trigger_price")
        if validity not in VALIDITIES:
            reasons.append("format: validity must be DAY, IOC or TTL, got %r" % o.get("validity"))
        elif validity == "TTL" and (not _is_int(o.get("validity_ttl")) or o["validity_ttl"] <= 0):
            reasons.append("format: validity TTL needs validity_ttl, a whole number of minutes")
        if o.get("tag") is not None and not TAG.match(str(o.get("tag"))):
            reasons.append("format: tag must be at most 20 letters, digits and dashes, got %r" % o.get("tag"))
        dq = o.get("disclosed_quantity")
        if dq is not None and (not _is_int(dq) or dq < 0 or (_is_int(qty) and dq > qty)):
            reasons.append("format: disclosed_quantity must be a whole number no bigger than the quantity")
        for k, allowed in (("exchange", EXCHANGES | DERIVATIVE_EXCHANGES), ("transaction_type", {"BUY", "SELL"}),
                           ("product", {"MIS", "CNC", "NRML", "MTF"}), ("order_type", ORDER_TYPES),
                           ("validity", VALIDITIES), ("variety", VARIETIES)):
            v = o.get(k)
            if isinstance(v, str) and v not in allowed and (v.upper() in allowed or v.lower() in allowed):
                reasons.append("format: %s %r must be written exactly as Kite's constant %r"
                               % (k, v, v.lower() if k == "variety" else v.upper()))
        if role == "stop":
            if side != "SELL":
                reasons.append("format: a stop leg must be a SELL (this pack trades long only)")
            if typ not in ("SL", "SL-M"):
                reasons.append("format: a stop leg must be order_type SL (stop-limit)")

        # asset: NSE/BSE cash equity only, never derivatives
        if exch in DERIVATIVE_EXCHANGES or (sym and DERIVATIVE.search(sym)):
            reasons.append("asset: %s%s is a derivative (F&O); this pack is NSE/BSE cash equity only and F&O "
                           "is never on" % (sym or "?", (" on " + exch) if exch else ""))
        elif sym and not SYMBOL.match(sym.replace(".NS", "").replace(".BO", "")):
            reasons.append("asset: %s does not look like an NSE or BSE equity symbol; this pack trades cash "
                           "equity only" % sym)
        elif sym and TRADE_FOR_TRADE.search(sym):
            reasons.append("asset: %s is in a trade-for-trade or SME series: no intraday trading" % sym)

        # the reference price: what one share costs to get in
        plan_entry = _f(plan.get("entry"))
        live = fresh(sym)
        ref, ref_from = None, None
        if typ in ("LIMIT", "SL") and price:
            ref, ref_from = price, "limit"
        elif typ == "SL-M" and trig:
            ref, ref_from = trig, "trigger price"
        if ref is None:
            cands = [(p, w) for p, w in ((plan_entry, "plan"), (live, "price")) if p]
            if cands:
                ref, ref_from = max(cands, key=lambda x: x[0])
                ref = _paisa(ref)
            elif sym in prices:
                ref, ref_from = prices[sym], "stale price"
        if plan_entry and live is None and sym in prices and price_times is not None:
            warns.append("the %s price in trader/prices.json is older than %d minutes: not used"
                         % (sym, FRESH_MINUTES))
        q = float(qty) if _is_int(qty) and qty > 0 else None
        if q and ref:
            value = q * ref

        # entry, stop or exit
        held = acct["shares"].get(sym, 0.0)
        held_value = acct["values"].get(sym, 0.0)
        exiting = False
        if side == "SELL" and held > 1e-9 and q is not None:
            exiting = q <= held + 1e-9
        elif side == "BUY" and held < -1e-9 and q is not None:
            exiting = q <= -held + 1e-9
        kind = "stop" if role == "stop" else ("exit" if exiting else "entry")
        for_entry = entry_results.get(group) if group else None
        if kind == "entry" and value is None and q:
            later.append("price: no price for %s to size a %s-share order; add it to "
                         "trader/prices.json (india-alerts: alerts.py quote %s --write)" % (sym, qty, sym))
        if kind == "entry" and product == "CNC":
            later.append("product: entries are intraday (MIS) only in this pack; CNC is delivery")
        if kind != "entry" and product == "CNC":
            warns.append("a CNC sell: only for delivery shares in your demat; shares bought MIS are sold MIS")

        # blocked list and size
        if sym in blocked:
            later.append("blocked: %s is on your blocked list" % sym)
        if value is not None:
            if value < float(c.get("min_order_inr") or 0) - PAISA:
                later.append("size: %s is under your %s minimum order"
                             % (money(value), money(c["min_order_inr"])))
            if c.get("max_order_inr") and value > float(c["max_order_inr"]) + PAISA:
                later.append("size: %s is over your %s max order"
                             % (money(value), money(c["max_order_inr"])))

        # account effects, simulated in order
        cash = acct["cash"]
        total = cash + sum(acct["values"].values())
        buffer_inr = total * float(c.get("cash_buffer_pct") or 0) / 100.0
        if value is not None and side == "BUY" and kind == "entry":
            after_cash = cash - value
            if after_cash < -PAISA and not c.get("allow_margin"):
                later.append("cash: needs %s but only %s cash; that would be margin (MIS leverage is off: "
                             "\"allow_margin\": false)" % (money(value), money(cash)))
            elif after_cash < buffer_inr - PAISA:
                later.append("cash: leaves %s cash, under your %s%% buffer of %s"
                             % (money(max(after_cash, 0)), _pct(c["cash_buffer_pct"]), money(buffer_inr)))
            pos_after = held_value + value
            limit = total * float(c.get("max_position_pct") or 100) / 100.0
            if total > 0 and pos_after > limit + PAISA:
                later.append("position: %s would be %s, %.1f%% of the %s account, over your %s%% cap"
                             % (sym, money(pos_after), 100.0 * pos_after / total, money(total),
                                _pct(c["max_position_pct"])))
        if kind == "stop" and side == "SELL" and sym:
            if q is not None and q > held + 1e-9:
                if for_entry is not None and for_entry["verdict"] != "PASS":
                    reasons.append("stop: its entry (#%d) was blocked, so there is nothing to protect"
                                   % for_entry["index"])
                elif for_entry is None:
                    reasons.append("stop: no entry for group %s in this batch and only %s %s shares held; a stop "
                                   "for %s would sell short. Record the entry fill first (ticket.py fill)"
                                   % (group or "(none)", _num(held), sym, _num(q)))
                else:
                    reasons.append("stop: sells %s %s but the entry #%d buys %s" % (_num(q), sym, for_entry["index"],
                                                                                    _num(held)))
            else:
                if live is not None and trig and live <= trig + PAISA:
                    warns.append("the latest price %s is at or below the trigger %s: Kite rejects a stop-loss "
                                 "SELL whose trigger is above the last price; get out with ticket.py flatten "
                                 "instead" % (_p(live), _p(trig)))
                info.append("%s trigger %s, limit %s: %s"
                            % (typ, _p(trig) if trig else "?", _p(price) if price and typ == "SL" else "none",
                               ("placed only after #%d fills, for the filled quantity" % for_entry["index"])
                               if for_entry and for_entry["verdict"] == "PASS"
                               else "protects the %s shares you hold" % _num(held)))
        elif side == "SELL" and sym and kind == "entry":
            over = q > held + 1e-9 if q is not None else held <= 0
            if over and not c.get("allow_short"):
                reasons.append("short: selling more %s than you hold (%s shares); short selling is off. "
                               "If you bought it, record the fill first (ticket.py fill)" % (sym, _num(held)))

        # intraday checks: stop, loss, reward:risk, daily loss, trades, window
        max_loss = risk_ps = rr = stop = target = ch_total = loss_sl = None
        if kind == "entry" and side in ("BUY", "SELL"):
            long = side == "BUY"
            legs = [x for x in orders if isinstance(x, dict) and x is not o
                    and str(x.get("role") or "").lower() == "stop" and group and x.get("group") == group]
            leg = legs[0] if legs else None
            leg_stop = _f(leg.get("trigger_price")) if leg else None
            leg_limit = _f(leg.get("price")) if leg and str(leg.get("order_type", "")).upper() == "SL" else None
            plan_stop = _f(plan.get("stop"))
            stop = leg_stop if leg_stop else plan_stop
            target = _f(plan.get("target"))
            if len(legs) > 1:
                later.append("stop: %d stop legs in group %s; an entry has one" % (len(legs), group))
            if leg is not None and str(leg.get("tradingsymbol", "")).upper() != sym:
                later.append("stop: the stop leg is for %s, not %s" % (leg.get("tradingsymbol"), sym))
            elif leg is not None and q is not None and leg.get("quantity") != qty:
                later.append("stop: the stop leg sells %s shares but this entry buys %s; it must cover the "
                             "whole entry" % (leg.get("quantity"), qty))
            if leg_stop and plan_stop and abs(leg_stop - plan_stop) > PAISA:
                warns.append("the stop leg's trigger %s differs from the plan's stop %s: the leg is the one "
                             "Kite gets" % (_p(leg_stop), _p(plan_stop)))
            if stop and not leg:
                warns.append("the stop %s is in the plan only, not a stop leg for Kite: set a stop alert "
                             "(india-alerts: alerts.py add --from-plan) or size it again with india-budget"
                             % _p(stop))
            if not stop:
                if c.get("require_stop", True):
                    later.append("stop: no stop. Every entry needs one (\"require_stop\": true): a role \"stop\" "
                                 "leg in the same group, from india-exit and india-budget")
                else:
                    warns.append("no stop: the max loss is not capped, so it is not counted in the daily loss")
            elif ref:
                if (long and stop >= ref - PAISA) or (not long and stop <= ref + PAISA):
                    later.append("stop: the stop %s must be %s the entry %s for a %s"
                                 % (_p(stop), "below" if long else "above", _p(ref), side))
                else:
                    risk_c = int(round(abs(ref - stop) * 100))
                    risk_ps = risk_c / 100.0
                    gross = round(q * risk_ps, 2) if q else 0.0
                    if include and q:
                        buy_v, sell_v = (q * ref, q * stop) if long else (q * stop, q * ref)
                        ch_total = charges(buy_v, sell_v, c.get("charges"), exch)["total"]
                    else:
                        ch_total = 0.0
                    max_loss = round(gross + ch_total, 2)
                    if leg_limit and long and q:
                        sl_ch = charges(q * ref, q * leg_limit, c.get("charges"), exch)["total"] if include else 0.0
                        loss_sl = round(q * int(round((ref - leg_limit) * 100)) / 100.0 + sl_ch, 2)
                    cap = _f(c.get("max_loss_per_trade_inr"))
                    if cap and max_loss > cap + PAISA:
                        later.append("loss: %s shares x %s risk a share = %s%s at the stop, over your %s max loss "
                                     "per trade" % (_num(q or 0), _p(risk_ps), money(gross),
                                                    (" plus %s charges = %s" % (money(ch_total), money(max_loss)))
                                                    if include else "", money(cap)))
                    min_rr = _f(c.get("min_reward_risk"))
                    exp = ref
                    if typ in ("LIMIT", "SL"):
                        cands = [p for p in (plan_entry, live) if p]
                        if cands:
                            exp = min(ref, _paisa(max(cands)))
                        if live and long and live > ref + PAISA:
                            warns.append("the latest price %s is above your limit %s: the order may not fill"
                                         % (_p(live), _p(ref)))
                    if target:
                        if (long and target <= ref + PAISA) or (not long and target >= ref - PAISA):
                            later.append("target: the target %s must be %s the entry %s"
                                         % (_p(target), "above" if long else "below", _p(ref)))
                        elif abs(exp - stop) > PAISA:
                            rr = round(int(round(abs(target - exp) * 100)) / float(int(round(abs(exp - stop) * 100))), 2)
                            rr_lim = round(int(round(abs(target - ref) * 100)) / float(risk_c), 2)
                            if min_rr and rr < min_rr - 1e-9:
                                later.append("reward:risk: %.2f (target %s, entry %s, stop %s) is under your "
                                             "minimum %s" % (rr, _p(target), _p(exp), _p(stop), _num(min_rr)))
                    elif min_rr:
                        later.append("reward:risk: no target to measure it against (min_reward_risk %s)"
                                     % _num(min_rr))
            daily = _f(c.get("max_daily_loss_inr"))
            if daily and max_loss is not None:
                used = loss_today + batch_loss
                if used + max_loss > daily + PAISA:
                    later.append("daily loss: %s already at risk today plus %s for this one = %s, over your %s "
                                 "daily loss limit" % (money(used), money(max_loss), money(used + max_loss),
                                                       money(daily)))
            cap_t = int(c.get("max_trades_per_day") or 0)
            if cap_t and len(entries_today) + batch_entries >= cap_t:
                later.append("trades: %d entries placed today plus %d passed earlier in this batch, your limit "
                             "is %d a day" % (len(entries_today), batch_entries, cap_t))
            later.extend(win_reasons)
            if product == "MIS":
                later.extend(flat_reasons)
            if stop and ref and risk_ps:
                info.append("entry %s (%s), stop %s, target %s: risk %s a share"
                            % (_p(ref), REF_WORDS.get(ref_from, ref_from), _p(stop),
                               _p(target) if target else "none", _p(risk_ps)))
                rr_text = "n/a"
                if rr is not None:
                    rr_text = "%.2f" % rr
                    if abs(exp - ref) > PAISA:
                        rr_text += " from %s (%.2f at the limit)" % (_p(exp), rr_lim)
                info.append("max loss %s%s%s, reward:risk %s%s"
                            % (money(max_loss), (" with %s charges" % money(ch_total)) if include else "",
                               (" (limit %s)" % money(c["max_loss_per_trade_inr"]))
                               if _f(c.get("max_loss_per_trade_inr")) else "", rr_text,
                               (" (min %s)" % _num(c["min_reward_risk"])) if _f(c.get("min_reward_risk")) else ""))
                if loss_sl is not None:
                    info.append("if the stop fills at its limit %s: %s; a gap below it leaves the stop unfilled"
                                % (_p(leg_limit), money(loss_sl)))
            if daily or cap_t:
                info.append("today before this: %d of %s trades, %s of %s daily loss"
                            % (len(entries_today) + batch_entries, cap_t or "unlimited",
                               money(loss_today + batch_loss), money(daily) if daily else "no limit on"))

        # duplicates and the daily order limit
        key = (sym, side)
        if key in seen:
            later.append("duplicate: a second %s %s in this batch" % (side, sym))
        if key in placed_keys:
            later.append("duplicate: a %s %s was already placed today" % (side, sym))
        passed_so_far = sum(1 for r in results if r["verdict"] == "PASS")
        cap_n = int(c.get("max_orders_per_day") or 0)
        if cap_n and count_today + passed_so_far >= cap_n:
            later.append("daily limit: %d placed today plus %d passed earlier in this batch, "
                         "your limit is %d a day" % (count_today, passed_so_far, cap_n))
        seen.add(key)

        if kind == "entry":
            reasons.extend(later)
        else:
            warns.extend("%s, not blocked: %s" % (kind, x) for x in later)

        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS" and kind != "stop":      # a stop leg is contingent: it does not fill now
            if kind == "entry" and side in ("BUY", "SELL"):
                batch_entries += 1
                batch_loss += max_loss or 0.0
            if q is not None:
                if side == "BUY":
                    if value is not None:
                        acct["cash"] -= value
                        acct["values"][sym] = acct["values"].get(sym, 0.0) + value
                    acct["shares"][sym] = held + q
                elif side == "SELL":
                    if value is not None:
                        acct["cash"] += value
                        acct["values"][sym] = max(acct["values"].get(sym, 0.0) - value, 0.0)
                    acct["shares"][sym] = held - q
        res = {
            "index": i, "symbol": sym, "exchange": exch, "side": side, "kind": kind, "role": role or None,
            "group": group, "amount": "%s shares" % (qty,),
            "value_inr": round(value, 2) if value is not None else None,
            "entry_ref": round(ref, 2) if ref else None, "entry_ref_from": ref_from,
            "stop": stop if kind == "entry" else trig, "target": target, "risk_per_share": risk_ps,
            "charges": ch_total, "max_loss": max_loss, "loss_at_stop_limit": loss_sl,
            "reward_risk": rr, "fingerprint": fingerprint(o), "verdict": verdict,
            "reasons": reasons, "warnings": warns, "info": info,
        }
        if kind == "stop" and for_entry is not None:
            res["for_index"] = for_entry["index"]
        if kind == "entry" and group and group not in entry_results:
            entry_results[group] = res
        results.append(res)

    return {
        "date": today,
        "now_ist": now_ist.strftime("%Y-%m-%d %H:%M"),
        "paper": bool(c.get("paper", True)),
        "results": results,
        "pass": sum(1 for r in results if r["verdict"] == "PASS"),
        "block": sum(1 for r in results if r["verdict"] == "BLOCK"),
        "warnings": top_warn,
        "cash_after": round(acct["cash"], 2),
        "today": {"orders_placed": count_today, "entries_placed": len(entries_today),
                  "loss_at_risk": round(loss_today, 2)},
    }


def _pct(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x)


def _num(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.4f" % x).rstrip("0")


def report(res):
    lines = []
    for w in res["warnings"]:
        lines.append("WARNING: " + w)
    if res["warnings"]:
        lines.append("")
    for r in res["results"]:
        kind = r["kind"]
        if kind == "stop" and r.get("for_index"):
            kind = "stop for #%d" % r["for_index"]
        lines.append("%-5s #%d %s %s %s, %s" % (r["verdict"], r["index"], r["side"], r["symbol"], r["amount"], kind))
        for x in r.get("info", []):
            lines.append("          " + x)
        for x in r["reasons"]:
            lines.append("        - " + x)
        for x in r["warnings"]:
            lines.append("        ~ " + x)
    lines.append("")
    lines.append("%d passed, %d blocked at %s IST. Cash after the passed orders: %s (%s account)."
                 % (res["pass"], res["block"], res["now_ist"], money(res["cash_after"]),
                    "paper" if res["paper"] else "LIVE"))
    if res["block"]:
        lines.append("Blocked orders do not go to the broker. Fix the order or your rules, then check again.")
    else:
        lines.append("Every order passed. india-broker still asks you yes or no for each trade (one yes covers "
                     "an entry and its stop).")
    lines.append("Not financial advice: a PASS is a rules check, not a recommendation.")
    return "\n".join(lines)


def load_prices(pdoc):
    """prices.json -> ({SYM: price}, {SYM: price time as naive UTC or None}).

    The price time is the quote's own time (detail.time), else when it was fetched
    (detail.fetched), else the file's as_of."""
    if not isinstance(pdoc, dict):
        return {}, {}
    prices = pdoc.get("prices", pdoc)
    if not isinstance(prices, dict):
        return {}, {}
    out, times = {}, {}
    as_of = parse_utc(pdoc.get("as_of"))
    detail = pdoc.get("detail") or {}
    for s, p in prices.items():
        if _f(p) is None:
            continue
        key = str(s).upper()
        for suffix in (".NS", ".BO"):
            if key.endswith(suffix):
                key = key[:-3]
        out[key] = float(p)
        d = detail.get(s) or detail.get(key) or {}
        times[key] = parse_utc(d.get("time")) or parse_utc(d.get("fetched")) or as_of
    return out, times


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Check proposed orders against your limits (india-risk).")
    ap.add_argument("--orders", default="trader/orders.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--portfolio", default="trader/portfolio.json",
                    help="your account; if missing, a fresh account funded with the budget")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--state", default="trader/state/broker.json", help="what india-broker placed today")
    ap.add_argument("--now", help="pretend it is this India time, e.g. \"2026-10-26 10:00\"")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today in India)")
    ap.add_argument("--out", default="trader/state/risk.json", help="where the verdicts are recorded")
    ap.add_argument("--no-write", action="store_true", help="do not record the verdicts")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        now = parse_now(a.now)
        if a.today and not re.match(r"^\d{4}-\d{2}-\d{2}$", a.today):
            raise RiskError("--today wants a date like 2026-10-26")
        doc = read_json(a.orders, "orders file (run india-budget first)")
        orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
        if not isinstance(orders, list) or not orders:
            raise RiskError("no orders in %s" % a.orders)
        cfg = read_json(a.config, "config", required=False) or {}
        portfolio = read_json(a.portfolio, "portfolio", required=False)
        pdoc = read_json(a.prices, "prices", required=False) or {}
        prices, times = load_prices(pdoc)
        state = read_json(a.state, "broker state", required=False)
        res = check(orders, cfg, portfolio, prices, state, a.today, now, times)
        if pdoc.get("as_of"):
            res["prices_as_of"] = pdoc["as_of"]
        if not a.no_write:
            d = os.path.dirname(a.out)
            if d:
                os.makedirs(d, exist_ok=True)
            rec = dict(res)
            rec["checked_at"] = datetime.datetime.now(datetime.timezone.utc).replace(
                microsecond=0, tzinfo=None).strftime("%Y-%m-%dT%H:%M:%SZ")
            rec["orders_file"] = a.orders
            tmp = a.out + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, a.out)
    except RiskError as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2, ensure_ascii=False) if a.json else report(res))
    return 1 if res["block"] else 0


if __name__ == "__main__":
    sys.exit(main())
