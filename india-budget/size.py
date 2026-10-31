#!/usr/bin/env python3
"""Position sizing for india-budget. Standard library only, no network.

Default mode: sizes ONE intraday trade from trader/plan.json (written by
india-exit) to your budget and your max loss per trade, then writes one
trade GROUP to trader/orders.json in the field names of Zerodha Kite's
place_order: an entry (BUY, MIS, a marketable LIMIT) and its protective stop
(SELL, MIS, SL stop-limit, placed only after the entry fills). The target is
watched by india-alerts, not placed. It never places anything: india-risk
checks the orders and india-broker asks you yes or no.

    python size.py                                  # trader/plan.json + trader/config.json
    python size.py --write                          # write trader/orders.json
    python size.py --limit                          # limit at the plan's entry, no buffer
    python size.py --limit 1194.00 --write          # limit at 1194.00
    python size.py --now "2026-10-26 10:00" --json  # pin the clock (India time, IST)

(python3 on macOS/Linux.)

The entry is a LIMIT, never MARKET: Kite wants market protection on a market
order and the Kite MCP place_order tool cannot send it. The limit is the
plan's entry (or a fresh, higher price from trader/prices.json) plus
entry_limit_buffer_pct, rounded UP to the tick: the most one share can cost.
The stop is an SL order: trigger at the plan's stop, limit price
stop_limit_buffer_pct below it, rounded DOWN to the tick.

How many shares. Whole shares only.

    shares = the smallest of
      max loss       the most shares whose loss at the stop,
                     shares x (limit - stop) + Zerodha's charges for the round trip,
                     fits max_loss_per_trade_inr ("include_charges": true)
      position cap   budget x max_position_pct / limit
      max order      max_order_inr / limit
      cash           (budget minus the cash buffer) / limit
                     (and no more than the cash in trader/portfolio.json, if you have one)

Zero shares (the stop is too far away for your max loss) is a plain refusal:
no order is written. So is a reward:risk under min_reward_risk, measured from
the price it expects to pay (the plan's entry, or the fresher higher price,
never above the limit). The report also says the reward:risk if it fills at
the limit.

Ticks. NSE's tick schedule (NSE/CMTR/67133, from 15 Apr 2025) is used,
in integer paise. The tick schedule is NSE's, reviewed monthly: check Kite's
instrument list if an order is rejected for its price. "tick_size":
{"RELIANCE": 0.1} in the config overrides it.

Weights mode (kept from v1, no longer the headline): split a budget across a
list of stocks by weight, in whole shares at limit prices.

    python size.py --weights RELIANCE=30,HDFCBANK=25,ICICIBANK=20 --prices trader/prices.json
    python size.py --targets my-weights.json --budget 50000 --write

  1. cash buffer   budget x cash_buffer_pct stays in cash, never invested
  2. weights       renormalised to 100 over the stocks you keep (blocked
                   symbols are dropped first; --top N keeps the N biggest)
  3. cap           no stock gets more than max_position_pct of the budget;
                   anything over the cap is shared out to the others by
                   weight, again and again until nobody is over
  4. min order     a slice under min_order_inr is dropped and its weight
                   shared out, so you never place a ₹20 order
  5. whole shares  each slice buys whole shares at its limit price (the price
                   plus entry_limit_buffer_pct, rounded up to the tick)

Money that cannot be invested is reported as "left idle", never hidden.
"""

import argparse
import datetime
import json
import math
import os
import re
import sys

# the contract's config.example.json. Missing keys fall back to these.
DEFAULTS = {
    "budget": None,
    "cash_buffer_pct": 10,
    "max_position_pct": 75,
    "max_order_inr": 80000,
    "min_order_inr": 500,
    "max_loss_per_trade_inr": 1000,
    "max_daily_loss_inr": 3000,
    "max_trades_per_day": 3,
    "max_orders_per_day": 10,
    "min_reward_risk": 1.0,
    "require_stop": True,
    "no_entries_before": "09:30",
    "no_entries_after": "14:30",
    "flat_by": "15:00",
    "exchange": "NSE",
    "product": "MIS",
    "entry_limit_buffer_pct": 0.1,
    "stop_limit_buffer_pct": 0.5,
    "include_charges": True,
    "broker": "zerodha",
    "paper": True,
    "blocked": [],
    "allow_margin": False,
    "allow_short": False,
    "allow_special_sessions": False,
}
# Zerodha equity intraday, checked 2026-10-31 (zerodha.com/charges). Percent of the order value.
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
CHARGE_WORDS = (("brokerage", "brokerage"), ("stt", "STT"), ("txn", "exchange"), ("sebi", "SEBI"),
                ("stamp", "stamp duty"), ("gst", "GST"))
EPS = 1e-9
FRESH_MINUTES = 15
IST = datetime.timedelta(hours=5, minutes=30)
SETUP_WORDS = {"orb": "opening-range breakout", "flag": "flag", "bounce": "oversold bounce"}
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")


class SizeError(Exception):
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
        t = "%s %05s" % (today.isoformat(), t.zfill(5))
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise SizeError("--now wants an India time like \"2026-10-26 10:00\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_utc(text):
    """"2026-10-26T04:30:00Z" -> naive UTC, or None."""
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def utc_iso(now_utc=None):
    now_utc = now_utc or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    return now_utc.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- inputs

def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise SizeError("no %s at %s" % (what, path))
    except json.JSONDecodeError as e:
        raise SizeError("%s is not valid JSON: %s" % (path, e))


def load_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        cfg.update(read_json(path, "config"))
    return cfg


def _f(x):
    if isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def clean_symbol(text):
    """"reliance.ns" -> ("RELIANCE", "NSE"); ".BO" means BSE."""
    s = str(text or "").strip().upper()
    if s.endswith(".NS"):
        return s[:-3], "NSE"
    if s.endswith(".BO"):
        return s[:-3], "BSE"
    return s, None


def parse_weights(data):
    """Accept a weights file, a {symbol: weight} dict or a list of rows.

    Returns [{"ticker", "name", "weight"}] with weight as a float."""
    if isinstance(data, dict) and "weights" in data:
        data = data["weights"]
    rows = []
    if isinstance(data, dict):
        for t, w in data.items():
            rows.append({"ticker": t, "name": "", "weight": w})
    elif isinstance(data, list):
        for i, r in enumerate(data):
            if not isinstance(r, dict):
                raise SizeError("weights[%d] is not an object" % i)
            t = r.get("ticker") or r.get("symbol") or r.get("tradingsymbol")
            w = r.get("weight", r.get("weight_pct", r.get("pct")))
            if not t or w is None:
                raise SizeError("weights[%d] needs a symbol and a weight" % i)
            rows.append({"ticker": t, "name": r.get("name", ""), "weight": w})
    else:
        raise SizeError("weights must be a list or an object")
    out = []
    for r in rows:
        try:
            w = float(r["weight"])
        except (TypeError, ValueError):
            raise SizeError("weight for %s is not a number: %r" % (r["ticker"], r["weight"]))
        out.append({"ticker": clean_symbol(r["ticker"])[0], "name": r["name"] or "", "weight": w})
    return out


def weights_from_arg(text):
    """RELIANCE=30,HDFCBANK=25 -> {symbol: weight}"""
    out = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise SizeError("--weights wants SYMBOL=WEIGHT pairs, got %r" % part)
        t, w = part.split("=", 1)
        out[clean_symbol(t)[0]] = w.strip()
    return out


def load_prices(path):
    if not path:
        return {}
    data = read_json(path, "prices")
    prices = data.get("prices", data) if isinstance(data, dict) else {}
    out = {}
    for t, p in prices.items():
        v = _f(p)
        if v is not None and v > 0:
            out[clean_symbol(t)[0]] = v
    return out


def price_time(doc, sym):
    """When this symbol's price is from: the quote's own time (detail[sym].time), else when it
    was fetched (detail[sym].fetched), else the file's as_of."""
    det = ((doc or {}).get("detail") or {}).get(sym) or {}
    for key in ("time", "fetched"):
        t = parse_utc(det.get(key))
        if t:
            return t
    return parse_utc((doc or {}).get("as_of"))


def fresh_price(doc, sym, now_utc, minutes=FRESH_MINUTES):
    """(price, fetched_utc, fresh) for sym from a prices.json doc, or (None, None, False)."""
    if not isinstance(doc, dict):
        return None, None, False
    p = _f((doc.get("prices") or {}).get(sym))
    if p is None or p <= 0:
        return None, None, False
    t = price_time(doc, sym)
    if t is None:
        return p, None, False
    age = (now_utc - t).total_seconds()
    return p, t, -60 <= age <= minutes * 60


# ---------------------------------------------------------------- ticks, money and charges

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


def tick_for(price, sym=None, cfg=None):
    """The tick in paise: the config's "tick_size" for this symbol, else NSE's schedule."""
    over = ((cfg or {}).get("tick_size") or {}) if isinstance((cfg or {}).get("tick_size"), dict) else {}
    t = _f(over.get(sym)) if sym else None
    if not t or t <= 0:
        t = tick_size(price)
    return max(int(round(t * 100)), 1)


def to_paise(x):
    return int(round(float(x) * 100))


def tick_down(x, sym=None, cfg=None):
    """Round DOWN to the tick (a long stop, a target, a stop-limit price), in paise."""
    t = tick_for(x, sym, cfg)
    return int(math.floor(float(x) * 100 / t + 1e-7)) * t


def tick_up(x, sym=None, cfg=None):
    """Round UP to the tick (an entry limit), in paise."""
    t = tick_for(x, sym, cfg)
    return int(math.ceil(float(x) * 100 / t - 1e-7)) * t


def rupees(paise):
    return round(paise / 100.0, 2)


def _price(x):
    """1194.8 -> "1194.80"."""
    return "%.2f" % float(x)


def _num(x):
    """75 -> "75", 0.1 -> "0.1"."""
    x = float(x)
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return ("%.4f" % x).rstrip("0").rstrip(".")


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
    p = int(round(abs(float(x)) * 100 + EPS))
    r, ps = divmod(p, 100)
    s = "₹" + _group_digits(r) + ("" if ps == 0 else ".%02d" % ps)
    return ("-" + s) if float(x) < 0 and p else s


def charges(buy_value, sell_value, rates=None, exchange="NSE"):
    """Zerodha intraday charges for a buy of buy_value and a sell of sell_value (rupees).
    Returns the breakdown, each part rounded to the paisa, with "total" (their sum).
    The same function is in india-risk/check.py."""
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


def charges_words(ch):
    return ", ".join("%s %s" % (w, money(ch[k])) for k, w in CHARGE_WORDS)


def shares_for(inr, per_share_paise):
    """Whole shares that fit in inr at per_share, in exact integer paise."""
    u = int(round(float(inr) * 100))
    if per_share_paise <= 0 or u <= 0:
        return 0
    return u // per_share_paise


def loss_at(qty, entry_p, exit_p, cfg, exchange):
    """(loss in rupees, charges breakdown) for qty bought at entry_p and sold at exit_p (paise)."""
    gross = qty * (entry_p - exit_p) / 100.0
    if cfg.get("include_charges", True):
        ch = charges(qty * entry_p / 100.0, qty * exit_p / 100.0, cfg.get("charges"), exchange)
    else:
        ch = dict({k: 0.0 for k, _ in CHARGE_WORDS}, total=0.0)
    return round(gross + ch["total"], 2), ch


def max_loss_shares(max_loss, entry_p, stop_p, cfg, exchange):
    """The most whole shares whose loss at the stop, charges included, fits max_loss."""
    risk_p = entry_p - stop_p
    lo, hi = 0, shares_for(max_loss, risk_p)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if loss_at(mid, entry_p, stop_p, cfg, exchange)[0] <= float(max_loss) + 0.005:
            lo = mid
        else:
            hi = mid - 1
    return lo


# ---------------------------------------------------------------- plan mode (the default)

def size_trade(plan, cfg, budget=None, limit=None, price=None, cash=None, today=None):
    """Pure function: one plan + your rules -> the sizing (a dict). qty may be 0.

    limit  your own limit price (None = the plan's entry plus entry_limit_buffer_pct)
    price  a FRESH latest price; a higher one replaces the plan's entry
    cash   the cash in trader/portfolio.json, if there is one
    today  India date (YYYY-MM-DD); a plan for another day is refused"""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    if not isinstance(plan, dict):
        raise SizeError("the plan is not a JSON object")
    sym, suffix_exchange = clean_symbol(plan.get("symbol") or plan.get("tradingsymbol"))
    if not sym:
        raise SizeError("the plan has no symbol (run india-exit)")
    if not SYMBOL.match(sym):
        raise SizeError("%s does not look like an NSE or BSE equity symbol" % sym)
    exchange = str(plan.get("exchange") or suffix_exchange or c.get("exchange") or "NSE").upper()
    if exchange not in ("NSE", "BSE"):
        raise SizeError("this pack trades NSE and BSE cash equity only; the plan says exchange %r" % exchange)
    side = str(plan.get("side") or "buy").strip().lower()
    if side != "buy":
        raise SizeError("this pack sizes long (buy) intraday trades only; the plan says %r" % side)
    if today and plan.get("date") and str(plan["date"]) != today:
        raise SizeError("the plan is for %s and today is %s in India: make a new plan with india-exit"
                        % (plan["date"], today))
    entry_plan, stop, target = _f(plan.get("entry")), _f(plan.get("stop")), _f(plan.get("target"))
    for name, v in (("entry", entry_plan), ("stop", stop), ("target", target)):
        if v is None or v <= 0:
            raise SizeError("the plan needs a positive %s (run india-exit); a trade needs entry, "
                            "stop and target" % name)
    blocked = {clean_symbol(b)[0] for b in (c.get("blocked") or [])}
    if sym in blocked:
        raise SizeError("%s is on your blocked list" % sym)

    budget = float(budget if budget is not None else (c.get("budget") or 0))
    if budget <= 0:
        raise SizeError("no budget: pass --budget or set \"budget\" in trader/config.json")
    buf_pct = float(c.get("cash_buffer_pct") or 0)
    if not 0 <= buf_pct < 100:
        raise SizeError("cash_buffer_pct must be between 0 and 100")
    entry_buf = float(c.get("entry_limit_buffer_pct") or 0)
    stop_buf = float(c.get("stop_limit_buffer_pct") or 0)
    if not 0 <= entry_buf < 5 or not 0 <= stop_buf < 20:
        raise SizeError("entry_limit_buffer_pct must be 0 to 5 and stop_limit_buffer_pct 0 to 20")

    plan_p = to_paise(entry_plan)
    if limit is not None:
        limit_p, limit_from = tick_up(limit, sym, c), "limit"
        expected_p, expected_from = min(plan_p, limit_p), "plan"
    else:
        expected_p, expected_from = plan_p, "plan"
        if price is not None and to_paise(price) > plan_p:
            expected_p, expected_from = to_paise(price), "price"
        limit_p = tick_up(expected_p / 100.0 * (1 + entry_buf / 100.0), sym, c)
        limit_from = "buffer"
    expected_p = min(expected_p, limit_p)
    stop_p = tick_down(stop, sym, c)
    target_p = tick_down(target, sym, c)
    stop_limit_p = tick_down(stop_p / 100.0 * (1 - stop_buf / 100.0), sym, c)
    if stop_p >= limit_p:
        raise SizeError("the stop %s is not below the entry limit %s; a long trade needs its stop underneath"
                        % (_price(rupees(stop_p)), _price(rupees(limit_p))))
    if target_p <= limit_p:
        raise SizeError("the target %s is not above the entry limit %s: the move has already happened. "
                        "Skip it or make a new plan with india-exit" % (_price(rupees(target_p)),
                                                                        _price(rupees(limit_p))))
    if stop_limit_p <= 0:
        raise SizeError("the stop-limit price works out at zero; lower stop_limit_buffer_pct")
    risk_p = limit_p - stop_p
    exp_risk_p = expected_p - stop_p
    reward_p = target_p - expected_p
    rr = round(reward_p / float(exp_risk_p), 2)
    rr_limit = round((target_p - limit_p) / float(risk_p), 2)
    lim_s = _price(rupees(limit_p))

    cash_buffer = round(budget * buf_pct / 100.0, 2)
    available = budget - cash_buffer
    cash_note = "budget %s minus the %s cash buffer" % (money(budget), money(cash_buffer))
    if cash is not None and cash - cash_buffer < available - 0.005:
        available = cash - cash_buffer
        cash_note = "cash %s in trader/portfolio.json minus the %s buffer" % (money(cash), money(cash_buffer))
    available = max(round(available, 2), 0.0)

    with_charges = bool(c.get("include_charges", True))
    limits = []
    max_loss = _f(c.get("max_loss_per_trade_inr"))
    if max_loss:
        limits.append({"name": "max loss", "inr": max_loss, "shares": max_loss_shares(max_loss, limit_p, stop_p,
                                                                                     c, exchange),
                       "how": "%s max loss / %s risk a share%s" % (money(max_loss), _price(rupees(risk_p)),
                                                                   ", charges included" if with_charges else "")})
    cap_pct = _f(c.get("max_position_pct"))
    if cap_pct:
        cap = round(budget * cap_pct / 100.0, 2)
        limits.append({"name": "position cap", "inr": cap, "shares": shares_for(cap, limit_p),
                       "how": "%s%% of %s = %s / %s" % (_num(cap_pct), money(budget), money(cap), lim_s)})
    max_order = _f(c.get("max_order_inr"))
    if max_order:
        limits.append({"name": "max order", "inr": max_order, "shares": shares_for(max_order, limit_p),
                       "how": "%s / %s" % (money(max_order), lim_s)})
    limits.append({"name": "cash", "inr": available, "shares": shares_for(available, limit_p),
                   "how": "%s = %s / %s" % (cash_note, money(available), lim_s)})

    qty = max(0, min(l["shares"] for l in limits))
    bound = [l["name"] for l in limits if l["shares"] == qty]
    for l in limits:
        l["binds"] = l["name"] == bound[0]
    cost = round(qty * limit_p / 100.0, 2)
    loss, ch = loss_at(qty, limit_p, stop_p, c, exchange)
    loss_sl, ch_sl = loss_at(qty, limit_p, stop_limit_p, c, exchange)
    gross = round(qty * risk_p / 100.0, 2)
    refusal = None
    if qty == 0:
        if bound[0] == "max loss":
            one = loss_at(1, limit_p, stop_p, c, exchange)[0]
            refusal = ("one share risks %s (limit %s minus stop %s%s), more than your %s max loss per trade. "
                       "No order. Skip this trade, or plan a closer stop with india-exit."
                       % (money(one), lim_s, _price(rupees(stop_p)), ", plus charges" if with_charges else "",
                          money(max_loss)))
        else:
            refusal = ("one share of %s costs %s, more than your %s limit (%s). No order."
                       % (sym, money(rupees(limit_p)), bound[0],
                          limits[[l["name"] for l in limits].index(bound[0])]["how"]))
    elif _f(c.get("min_reward_risk")) and reward_p < _f(c["min_reward_risk"]) * exp_risk_p - EPS:
        refusal = ("reward:risk %.2f (target %s, entry %s, stop %s) is under your minimum %s, so india-risk "
                   "would block it. No order. Skip it, or make a new plan with india-exit."
                   % (rr, _price(rupees(target_p)), _price(rupees(expected_p)), _price(rupees(stop_p)),
                      _num(c["min_reward_risk"])))
    elif cost < float(c.get("min_order_inr") or 0) - 0.005:
        refusal = ("%d share%s of %s is %s, under your %s minimum order. No order."
                   % (qty, "" if qty == 1 else "s", sym, money(cost), money(c["min_order_inr"])))
    return {
        "symbol": sym, "exchange": exchange, "product": str(c.get("product") or "MIS").upper(),
        "side": "buy", "setup": plan.get("setup", ""), "date": plan.get("date") or today,
        "entry_time": plan.get("entry_time", ""), "entry_plan": rupees(plan_p),
        "expected_entry": rupees(expected_p), "expected_from": expected_from,
        "limit": rupees(limit_p), "limit_from": limit_from, "entry_limit_buffer_pct": entry_buf,
        "stop": rupees(stop_p), "stop_limit": rupees(stop_limit_p), "stop_limit_buffer_pct": stop_buf,
        "target": rupees(target_p), "tick": tick_for(limit_p / 100.0, sym, c) / 100.0,
        "risk_per_share": rupees(risk_p), "reward_risk": rr, "reward_risk_at_limit": rr_limit,
        "stop_label": plan.get("stop_label", ""), "target_label": plan.get("target_label", ""),
        "budget": budget, "cash_buffer_pct": buf_pct, "cash_buffer": cash_buffer, "cash_available": available,
        "max_loss_per_trade_inr": max_loss, "include_charges": with_charges,
        "limits": limits, "qty": qty, "bound_by": bound[0], "cost": cost,
        "loss_before_charges": gross, "charges": ch, "max_loss": loss,
        "loss_at_stop_limit": loss_sl, "charges_at_stop_limit": ch_sl,
        "flat_by": plan.get("flat_by") or c.get("flat_by"), "refusal": refusal,
    }


def _tag(text):
    t = re.sub(r"[^A-Za-z0-9\-]", "", str(text or ""))
    return t[:20] or "india"


def order_group(s):
    """The proposed trade: [entry, stop] in Kite place_order fields, plus our role, group, after,
    why and plan (never sent). None when the sizing refused."""
    if s["refusal"] or s["qty"] <= 0:
        return None
    setup = SETUP_WORDS.get(s["setup"], s["setup"] or "intraday trade")
    group = "%s-%s-%s" % (s["symbol"], s["date"], _tag(s["setup"] or "trade"))
    bound = {"max loss": "your %s max loss per trade" % money(s["max_loss_per_trade_inr"] or 0),
             "position cap": "your position cap", "max order": "your max order",
             "cash": "your cash after the buffer"}[s["bound_by"]]
    charge_part = (" plus %s charges" % money(s["charges"]["total"])) if s["include_charges"] else ""
    why = ("%s on %s: %d shares, bound by %s (%d x %s = %s at the stop%s = %s)"
           % (setup, s["symbol"], s["qty"], bound, s["qty"], _price(s["risk_per_share"]),
              money(s["loss_before_charges"]), charge_part, money(s["max_loss"])))
    common = {"variety": "regular", "exchange": s["exchange"], "tradingsymbol": s["symbol"]}
    entry = {"role": "entry", "group": group}
    entry.update(common)
    entry.update({
        "transaction_type": "BUY", "quantity": int(s["qty"]), "product": s["product"],
        "order_type": "LIMIT", "price": s["limit"], "validity": "DAY", "tag": _tag("india-" + (s["setup"] or "trade")),
        "why": why,
        "plan": {"entry": s["entry_plan"], "limit": s["limit"], "stop": s["stop"], "stop_limit": s["stop_limit"],
                 "target": s["target"], "risk_per_share": s["risk_per_share"], "charges": s["charges"]["total"],
                 "max_loss": s["max_loss"], "setup": s["setup"], "entry_time": s["entry_time"],
                 "flat_by": s["flat_by"], "date": s["date"]},
    })
    stop = {"role": "stop", "group": group, "after": "entry"}
    stop.update(common)
    stop.update({
        "transaction_type": "SELL", "quantity": int(s["qty"]), "product": s["product"],
        "order_type": "SL", "trigger_price": s["stop"], "price": s["stop_limit"], "validity": "DAY",
        "tag": "india-stop",
        "why": ("protective stop for the %s entry: place it only after the entry fills, for the filled "
                "quantity. It triggers at %s and sells no lower than %s (loss %s there, charges included). "
                "A gap below %s leaves it unfilled." % (s["symbol"], _price(s["stop"]), _price(s["stop_limit"]),
                                                       money(s["loss_at_stop_limit"]), _price(s["stop_limit"]))),
    })
    return [entry, stop]


def plan_report(s, orders, notes=()):
    lines = []
    setup = SETUP_WORDS.get(s["setup"], s["setup"] or "setup")
    lines.append("%s buy on %s, %s, entry %s%s" % (s["symbol"], s["exchange"], setup, _price(s["entry_plan"]),
                                                  (" at " + s["entry_time"] + " IST") if s["entry_time"] else ""))
    lines.append("Stop %s%s. Target %s%s." % (_price(s["stop"]), (": " + s["stop_label"]) if s["stop_label"] else "",
                                             _price(s["target"]),
                                             (": " + s["target_label"]) if s["target_label"] else ""))
    for n in notes:
        lines.append(n)
    if s["limit_from"] == "limit":
        src = "your limit price, rounded up to the %s tick" % _price(s["tick"])
    else:
        src = "%s plus your %s%% entry buffer, rounded up to the %s tick" % (
            "the plan's entry" if s["expected_from"] == "plan" else "the latest price",
            _num(s["entry_limit_buffer_pct"]), _price(s["tick"]))
    lines.append("Entry limit %s (%s): the most one share can cost. Risk %s a share (limit %s minus stop %s)."
                 % (_price(s["limit"]), src, _price(s["risk_per_share"]), _price(s["limit"]), _price(s["stop"])))
    lines.append("Reward:risk %.2f from %s, %.2f if it fills at the limit."
                 % (s["reward_risk"], "%s %s" % ("the latest price" if s["expected_from"] == "price"
                                                 else "the plan's entry", _price(s["expected_entry"])),
                    s["reward_risk_at_limit"]))
    lines.append("")
    lines.append("Limits (whole shares; the smallest one wins):")
    for l in s["limits"]:
        lines.append("  %-13s %-66s %4d shares%s" % (l["name"], l["how"], l["shares"],
                                                      "  <- binds" if l["binds"] else ""))
    lines.append("")
    if orders is None:
        lines.append("No order: " + s["refusal"])
        lines.append("Not financial advice: this is software that does arithmetic on your plan.")
        return "\n".join(lines)
    lines.append("Size: %d shares of %s = %s at the limit %s, bound by the %s."
                 % (s["qty"], s["symbol"], money(s["cost"]), _price(s["limit"]), s["bound_by"]))
    if s["include_charges"]:
        lines.append("Max loss at the stop: %d x %s = %s plus %s charges = %s."
                     % (s["qty"], _price(s["risk_per_share"]), money(s["loss_before_charges"]),
                        money(s["charges"]["total"]), money(s["max_loss"])))
        lines.append("Charges (Zerodha intraday, buy at the limit, sell at the stop): %s."
                     % charges_words(s["charges"]))
    else:
        lines.append("Max loss at the stop: %d x %s = %s (charges not counted: \"include_charges\": false)."
                     % (s["qty"], _price(s["risk_per_share"]), money(s["max_loss"])))
    over = ""
    if s["max_loss_per_trade_inr"] and s["loss_at_stop_limit"] > s["max_loss_per_trade_inr"] + 0.005:
        over = " (over your %s max loss, which is measured at the trigger)" % money(s["max_loss_per_trade_inr"])
    lines.append("If the stop fills at its limit %s instead, the loss is %s%s. A stop is an order, not a promise: "
                 "a gap below %s leaves it unfilled and the loss bigger." % (_price(s["stop_limit"]),
                                                                           money(s["loss_at_stop_limit"]), over,
                                                                           _price(s["stop_limit"])))
    lines.append("")
    lines.append("Proposed trade (Kite place_order fields, nothing has been placed):")
    e, st = orders
    lines.append("  1. entry  BUY %d %s %s %s LIMIT %s %s"
                 % (e["quantity"], e["tradingsymbol"], e["exchange"], e["product"], _price(e["price"]), e["validity"]))
    lines.append("  2. stop   SELL %d %s %s %s SL trigger %s price %s %s, placed only after the entry fills, "
                 "for the filled quantity" % (st["quantity"], st["tradingsymbol"], st["exchange"], st["product"],
                                              _price(st["trigger_price"]), _price(st["price"]), st["validity"]))
    lines.append("The target %s is not placed: india-alerts watches it and the exit is a flatten ticket (cancel "
                 "the stop, then SELL LIMIT). A second resting SELL next to the stop could fill too and leave you "
                 "short." % _price(s["target"]))
    if s["flat_by"]:
        lines.append("Flat by %s IST: anything still open then gets closed (india-alerts rings, india-broker "
                     "flatten), before Zerodha's own MIS square-off (15:12 for F&O stocks, 15:25 for the rest), "
                     "which costs ₹50 + 18%% GST an order." % s["flat_by"])
    lines.append("")
    lines.append("Next: india-risk checks both orders, then india-broker asks you yes or no for the trade.")
    lines.append("Not financial advice: this is software that does arithmetic on your plan.")
    return "\n".join(lines)


# ---------------------------------------------------------------- weights mode (v1)

def normalise(rows, blocked=(), top=None):
    """Drop blocked and non-positive weights, keep the top N, renormalise to 100."""
    blocked = {clean_symbol(b)[0] for b in (blocked or [])}
    dropped, keep, seen = [], [], set()
    for r in rows:
        if r["ticker"] in seen:
            raise SizeError("%s is listed twice in the weights" % r["ticker"])
        seen.add(r["ticker"])
        if r["ticker"] in blocked:
            dropped.append({"ticker": r["ticker"], "reason": "on your blocked list"})
        elif r["weight"] <= 0:
            dropped.append({"ticker": r["ticker"], "reason": "weight is zero or negative"})
        else:
            keep.append(dict(r))
    keep.sort(key=lambda r: -r["weight"])
    if top:
        for r in keep[top:]:
            dropped.append({"ticker": r["ticker"], "reason": "outside the top %d" % top})
        keep = keep[:top]
    total = sum(r["weight"] for r in keep)
    for r in keep:
        r["source_weight"] = r["weight"]
        r["weight"] = 100.0 * r["weight"] / total if total else 0.0
    return keep, dropped


def allocate(rows, investable, cap):
    """Exact (unrounded) rupees per symbol with the cap enforced.

    Proportional to weight; any stock over the cap is pinned at the cap and
    the rest of the money is shared over the others, repeated until no stock
    is over. Returns ({symbol: rupees}, set of capped symbols)."""
    alloc, capped = {}, set()
    active = [r for r in rows]
    money_left = investable
    while active:
        wsum = sum(r["weight"] for r in active)
        trial = {r["ticker"]: money_left * r["weight"] / wsum for r in active}
        over = [r for r in active if trial[r["ticker"]] > cap + EPS]
        if not over:
            alloc.update(trial)
            break
        for r in over:
            alloc[r["ticker"]] = cap
            capped.add(r["ticker"])
            money_left -= cap
        active = [r for r in active if r["ticker"] not in capped]
        if money_left <= EPS:
            for r in active:
                alloc[r["ticker"]] = 0.0
            break
    return alloc, capped


def whole_shares(slices, prices, order, cap, cash):
    """Whole shares: floor each slice, then buy one more share at a time for the
    stock furthest below its slice while cash lasts and the cap holds."""
    shares = {}
    for t in order:
        shares[t] = int(math.floor(slices[t] / prices[t] + EPS)) if slices[t] > 0 else 0
    spent = sum(shares[t] * prices[t] for t in order)
    left = cash - spent
    while True:
        best, gap = None, None
        for t in order:
            p = prices[t]
            if p > left + EPS or (shares[t] + 1) * p > cap + EPS:
                continue
            g = slices[t] - shares[t] * p
            if best is None or g > gap + EPS:
                best, gap = t, g
        if best is None:
            break
        shares[best] += 1
        left -= prices[best]
    return shares


def size(rows, cfg, budget=None, prices=None, top=None):
    """Weights mode. Pure function: weights + rules + prices -> the full sizing result (a dict).
    Whole shares only, each at its limit price (price plus entry_limit_buffer_pct, rounded up)."""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    budget = float(budget if budget is not None else (c.get("budget") or 0))
    if budget <= 0:
        raise SizeError("no budget: pass --budget or set \"budget\" in trader/config.json")
    buf_pct = float(c.get("cash_buffer_pct") or 0)
    cap_pct = float(c.get("max_position_pct") or 100)
    min_order = float(c.get("min_order_inr") or 0)
    entry_buf = float(c.get("entry_limit_buffer_pct") or 0)
    if not 0 <= buf_pct < 100:
        raise SizeError("cash_buffer_pct must be between 0 and 100")
    if not 0 < cap_pct <= 100:
        raise SizeError("max_position_pct must be above 0 and at most 100")
    prices = prices or {}

    keep, dropped = normalise(rows, c.get("blocked"), top)
    if not keep:
        raise SizeError("nothing to size: no weights left after the blocked list")
    missing = [r["ticker"] for r in keep if not prices.get(r["ticker"])]
    if missing:
        raise SizeError("whole shares need a price for: %s (add them to trader/prices.json, "
                        "e.g. with india-alerts: alerts.py quote %s --write)"
                        % (", ".join(missing), " ".join(missing)))
    limits = {r["ticker"]: rupees(tick_up(prices[r["ticker"]] * (1 + entry_buf / 100.0), r["ticker"], c))
              for r in keep}

    cash_buffer = round(budget * buf_pct / 100.0, 2)
    investable = round(budget - cash_buffer, 2)
    cap = round(budget * cap_pct / 100.0, 2)

    while True:
        live = [dict(r) for r in keep]
        tot = sum(r["source_weight"] for r in live)
        for r in live:
            r["weight"] = 100.0 * r["source_weight"] / tot
        exact, capped = allocate(live, investable, cap)
        order = [r["ticker"] for r in sorted(live, key=lambda r: -exact[r["ticker"]])]
        slices = {t: math.floor(exact[t] * 100 + EPS) / 100.0 for t in order}
        shares = whole_shares(slices, limits, order, cap, investable)
        inr = {t: round(shares[t] * limits[t], 2) for t in order}
        small = [r for r in live if inr[r["ticker"]] < min_order - EPS]
        if not small:
            break
        worst = min(small, key=lambda r: (r["source_weight"], r["ticker"]))
        if shares[worst["ticker"]] == 0:
            reason = ("one share costs %s at its limit, more than its slice; it cannot be bought in whole shares"
                      % money(limits[worst["ticker"]]))
        else:
            reason = "its slice (%s) is under your %s minimum order" % (money(inr[worst["ticker"]]), money(min_order))
        dropped.append({"ticker": worst["ticker"], "reason": reason})
        keep = [r for r in keep if r["ticker"] != worst["ticker"]]
        if not keep:
            raise SizeError("every slice is under your minimum order; raise the budget or hold fewer stocks")

    positions = []
    for r in sorted(live, key=lambda r: -r["weight"]):
        t = r["ticker"]
        positions.append({"ticker": t, "name": r["name"], "weight": round(r["weight"], 2),
                          "source_weight": r["source_weight"], "inr": inr[t], "price": prices[t],
                          "limit": limits[t], "shares": shares[t], "capped": t in capped})
    invested = round(sum(p["inr"] for p in positions), 2)
    return {
        "budget": budget, "cash_buffer_pct": buf_pct, "cash_buffer": cash_buffer,
        "investable": investable, "max_position_pct": cap_pct, "cap": cap,
        "min_order_inr": min_order, "entry_limit_buffer_pct": entry_buf,
        "exchange": str(c.get("exchange") or "NSE").upper(), "product": str(c.get("product") or "MIS").upper(),
        "positions": positions, "invested": invested,
        "idle": round(investable - invested, 2), "dropped": dropped,
    }


def orders_from(result, source="", today=""):
    """Weights mode: proposed BUY LIMIT orders, one per position, in Kite place_order fields."""
    out = []
    for p in result["positions"]:
        if p["shares"] <= 0:
            continue
        why = "%.2f%% of the split" % p["weight"]
        if source:
            why = "%s, weights from %s" % (why, source)
        if p["capped"]:
            why += "; capped at %s%% of the %s budget" % (_num(result["max_position_pct"]), money(result["budget"]))
        out.append({"role": "entry", "group": "%s-%s-split" % (p["ticker"], today or "today"),
                    "variety": "regular", "exchange": result["exchange"], "tradingsymbol": p["ticker"],
                    "transaction_type": "BUY", "quantity": int(p["shares"]), "product": result["product"],
                    "order_type": "LIMIT", "price": p["limit"], "validity": "DAY", "tag": "india-split",
                    "why": why})
    return out


def report(result, orders, source=""):
    """Weights mode table."""
    r = result
    lines = []
    if source:
        lines.append("Weights from: %s" % source)
    lines.append("Budget %s, cash buffer %s%% = %s, investing %s, cap %s%% = %s per stock"
                 % (money(r["budget"]), _num(r["cash_buffer_pct"]), money(r["cash_buffer"]),
                    money(r["investable"]), _num(r["max_position_pct"]), money(r["cap"])))
    lines.append("")
    lines.append("%-11s %-20s %8s %10s %9s %7s %12s  note" % ("symbol", "name", "weight", "price", "limit",
                                                             "shares", "rupees"))
    for p in r["positions"]:
        row = "%-11s %-20s %7.2f%% %10s %9s %7d %12s" % (p["ticker"], (p["name"] or "")[:20], p["weight"],
                                                        _price(p["price"]), _price(p["limit"]), p["shares"],
                                                        money(p["inr"]))
        if p["capped"]:
            row += "  capped at %s%%" % _num(r["max_position_pct"])
        lines.append(row)
    lines.append("%-11s %-20s %8s %10s %9s %7s %12s" % ("", "invested", "", "", "", "", money(r["invested"])))
    lines.append("%-11s %-20s %8s %10s %9s %7s %12s" % ("", "cash buffer", "", "", "", "", money(r["cash_buffer"])))
    lines.append("%-11s %-20s %8s %10s %9s %7s %12s" % ("", "left idle", "", "", "", "", money(r["idle"])))
    if r["idle"] >= 1:
        lines.append("")
        lines.append("Left idle %s: whole shares do not divide evenly%s." % (
            money(r["idle"]), "" if not any(p["capped"] for p in r["positions"])
            else ", and capped stocks cannot take more"))
    for d in r["dropped"]:
        lines.append("Dropped %s: %s" % (d["ticker"], d["reason"]))
    lines.append("")
    lines.append("Proposed orders (Kite place_order fields, nothing has been placed):")
    for o in orders:
        lines.append("  BUY %d %s %s %s LIMIT %s %s" % (o["quantity"], o["tradingsymbol"], o["exchange"],
                                                       o["product"], _price(o["price"]), o["validity"]))
    lines.append("")
    lines.append("These buys have no stop. india-risk blocks them while \"require_stop\" is true and applies "
                 "your intraday limits to every buy.")
    lines.append("Next: india-risk checks every order, then india-broker asks you yes or no for each one.")
    lines.append("Not financial advice: the weights are yours (or an example), this only does the arithmetic.")
    return "\n".join(lines)


# ---------------------------------------------------------------- orders file

def write_orders(path, orders, cfg, now_utc=None, note=None):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    doc = {
        "created_by": "india-budget",
        "created_at": utc_iso(now_utc),
        "status": "proposed",
        "paper": bool(cfg.get("paper", True)),
        "note": note or "Proposal only. Nothing is placed until india-risk passes it and you say yes in the chat.",
        "orders": orders,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- main

def run_plan(a, cfg):
    now = parse_now(a.now)
    today = to_ist(now).date().isoformat()
    plan = read_json(a.plan, "plan (run india-exit first, or use --weights for a split)")
    limit = None
    if a.limit is not None:
        if a.limit == "plan":
            limit = _f((plan or {}).get("entry"))
        else:
            limit = _f(a.limit)
            if limit is None or limit <= 0:
                raise SizeError("--limit wants a price, like --limit 1194.00")
    sym = clean_symbol((plan or {}).get("symbol"))[0] if isinstance(plan, dict) else ""
    notes, price = [], None
    ppath = a.prices or ("trader/prices.json" if os.path.exists("trader/prices.json") else None)
    if ppath and limit is None and sym:
        pdoc = read_json(ppath, "prices")
        p, t, fresh = fresh_price(pdoc, sym, now)
        if p is not None and fresh:
            price = p
            if to_paise(p) > to_paise(_f(plan.get("entry")) or 0):
                notes.append("%s last %s (as of %s IST) is above the plan's entry %s, so the risk "
                             "is bigger: the limit is worked out from %s." % (sym, _price(p), to_ist(t).strftime("%H:%M"),
                                                                             _price(plan.get("entry")), _price(p)))
        elif p is not None:
            notes.append("%s in %s is %s: not used (older than %d minutes). Sized from the plan's entry."
                         % (sym, ppath, ("from " + to_ist(t).strftime("%Y-%m-%d %H:%M") + " IST")
                            if t else "undated", FRESH_MINUTES))
    cash = None
    if a.portfolio and os.path.exists(a.portfolio):
        pf = read_json(a.portfolio, "portfolio")
        cash = _f(pf.get("cash")) if isinstance(pf, dict) else None
    s = size_trade(plan, cfg, a.budget, limit, price, cash, today)
    if limit is not None and abs(s["limit"] - limit) > 0.001:
        notes.append("Your limit %s is not on the %s tick: rounded up to %s." % (_price(limit), _price(s["tick"]),
                                                                                 _price(s["limit"])))
    orders = order_group(s)
    if orders and a.write:
        write_orders(a.write, orders, cfg, now)
    if a.json:
        print(json.dumps({"sizing": s, "orders": orders or [], "notes": notes}, indent=2, ensure_ascii=False))
    else:
        print(plan_report(s, orders, notes))
        if orders and a.write:
            print("Wrote the proposed trade (entry + stop) to %s" % a.write)
    return 0 if orders else 1


def run_weights(a, cfg):
    source = ""
    if a.weights:
        rows = parse_weights(weights_from_arg(a.weights))
    else:
        data = read_json(a.targets, "weights file")
        rows = parse_weights(data)
        if isinstance(data, dict):
            source = data.get("source", "")
    prices = load_prices(a.prices)
    if not prices and os.path.exists("trader/prices.json") and not a.prices:
        prices = load_prices("trader/prices.json")
    now = parse_now(a.now)
    result = size(rows, cfg, a.budget, prices, a.top)
    orders = orders_from(result, source, to_ist(now).date().isoformat())
    if a.write:
        write_orders(a.write, orders, cfg, now,
                     "Proposals only. Nothing is placed until india-risk passes each order and you say yes "
                     "to it in the chat.")
    if a.json:
        print(json.dumps({"result": result, "orders": orders}, indent=2, ensure_ascii=False))
    else:
        print(report(result, orders, source))
        if a.write:
            print("Wrote %d proposed orders to %s" % (len(orders), a.write))
    return 0


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Size an intraday trade to your budget and max loss (india-budget).")
    ap.add_argument("--plan", default="trader/plan.json", help="the trade plan (default trader/plan.json)")
    ap.add_argument("--config", default="trader/config.json", help="rules (default trader/config.json)")
    ap.add_argument("--budget", type=float, help="override the budget in the config (rupees)")
    ap.add_argument("--limit", nargs="?", const="plan", metavar="PRICE",
                    help="your own entry limit: the plan's entry with no buffer, or PRICE")
    ap.add_argument("--prices", help="prices JSON (default trader/prices.json if it exists)")
    ap.add_argument("--portfolio", default="trader/portfolio.json",
                    help="your account; its cash caps the size if it exists")
    ap.add_argument("--now", help="pretend it is this India time, e.g. \"2026-10-26 10:00\"")
    ap.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH",
                    help="write the proposed orders (default trader/orders.json)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the report")
    g = ap.add_argument_group("weights mode (split a budget across a list)")
    g.add_argument("--weights", help="inline weights: RELIANCE=30,HDFCBANK=25")
    g.add_argument("--targets", help="a weights file: {\"weights\": [{\"symbol\", \"weight\"}]}")
    g.add_argument("--top", type=int, help="keep only the N biggest weights")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config)
        if a.weights or a.targets:
            return run_weights(a, cfg)
        if a.top:
            raise SizeError("--top works with --weights or --targets")
        return run_plan(a, cfg)
    except SizeError as e:
        print("size.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
