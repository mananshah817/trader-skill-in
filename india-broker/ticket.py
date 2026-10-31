#!/usr/bin/env python3
"""Order tickets and the decision log for india-broker. Standard library only.

This tool never talks to a broker and never places anything. It does its
work around the one moment that matters, your yes:

  show      renders each proposed trade as a plain-words ticket: the entry
            (BUY, MIS, LIMIT), its protective stop (SELL, MIS, SL, placed only
            after the entry fills), the target that is watched and not placed,
            and the max loss at the stop with charges. Plus the exact input
            Claude gives Zerodha Kite's place_order tool for each order. A
            ticket is READY only if india-risk passed THESE exact orders today
            (matched by fingerprint) and nobody has decided on them yet.
  approve   records your explicit yes for ONE ticket (an entry and its stop),
            with your reply word for word. It refuses "yes to all", "sure",
            "ok", a yes that names a different stock or quantity, and any
            ticket that is not READY.
  decline   records a no.
  result    live mode: records what happened after the Allow once / Deny
            card for one order: placed (with Kite's order id), denied,
            failed, or cancelled (a stop you cancelled).
  fill      records a fill in trader/portfolio.json (cash and shares).
            --paper records a PAPER fill: paper mode never calls a Kite tool
            that places, modifies or cancels anything.
  flatten   builds the exit ticket that closes a symbol's open position by
            flat_by: cancel the open stop order first, then SELL LIMIT. It
            still goes through india-risk and your yes.
  log       prints today's decisions.

    python ticket.py show                    # every ticket, READY or not
    python ticket.py show --index 1 --json   # one ticket as JSON
    python ticket.py approve --index 1 --reply "yes"
    python ticket.py fill --index 1 --paper --price 1194.20   # paper: the entry filled
    python ticket.py result --index 1 --leg entry --status placed --order-id 2610260001   # live
    python ticket.py flatten --symbol RELIANCE --qty 62 --price 1199.40
    python ticket.py log

(python3 on macOS/Linux.) Everything is written to trader/state/broker.json
(append only). Dates are India dates; --now "2026-10-26 10:00" (IST) pins the clock.
"""

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import sys

KITE_FIELDS = ("variety", "exchange", "tradingsymbol", "transaction_type", "quantity", "product", "order_type",
               "price", "trigger_price", "validity", "validity_ttl", "disclosed_quantity", "tag")
VALIDITY_WORDS = {"DAY": "good for today", "IOC": "immediate or cancel", "TTL": "for a set number of minutes"}
PRODUCT_WORDS = {"MIS": "intraday (MIS)", "CNC": "delivery (CNC)"}
ROLE_WORDS = {"entry": "entry", "stop": "stop", "exit": "exit"}
YES_WORDS = ("yes", "y")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later",
               "hold", "unless", "if"}
IST = datetime.timedelta(hours=5, minutes=30)
FRESH_MINUTES = 15


class TicketError(Exception):
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
        raise TicketError("--now wants an India time like \"2026-10-26 10:00\" (IST) or ...Z (UTC)")
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


def ist_iso(now_utc):
    """naive UTC -> "2026-10-26T10:00:00+05:30" (India wall clock with its offset)."""
    return to_ist(now_utc).replace(microsecond=0).isoformat() + "+05:30"


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TicketError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TicketError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def rec_day(rec):
    """The trading day a decision belongs to: its "date" (the day it was made under), else the day of "at"."""
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


# ---------------------------------------------------------------- money and ticks

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


def _p(v):
    return "%.2f" % float(v)


def _n(v):
    """62 -> "62", 62.0 -> "62", 0.5 -> "0.5"."""
    f = float(v)
    return str(int(f)) if f == int(f) else ("%.4f" % f).rstrip("0")


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


def tick_down(x, sym, cfg):
    over = cfg.get("tick_size") if isinstance(cfg.get("tick_size"), dict) else {}
    t = _f(over.get(sym))
    if not t or t <= 0:
        t = tick_size(x)
    tp = max(int(round(t * 100)), 1)
    return int(math.floor(float(x) * 100 / tp + 1e-7)) * tp / 100.0


# ---------------------------------------------------------------- the orders

def fingerprint(order):
    """Same function as india-risk/check.py: identifies the exact order (Kite fields only)."""
    core = {k: order[k] for k in KITE_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def tool_input(order):
    """The exact input for Kite's place_order: the KITE_FIELDS subset, nothing else.
    role, group, after, why and plan are ours and are never sent."""
    return {k: order[k] for k in KITE_FIELDS if k in order and order[k] is not None}


def role_of(order):
    r = str(order.get("role") or "").lower()
    if r in ROLE_WORDS:
        return r
    return "entry" if str(order.get("transaction_type", "")).upper() == "BUY" else "exit"


def tickets(orders):
    """[(ticket number, [(file index, order), ...])]: orders with the same "group" make one ticket."""
    out, by = [], {}
    for i, o in enumerate(orders, 1):
        g = o.get("group") if isinstance(o, dict) else None
        if g and g in by:
            by[g][1].append((i, o))
            continue
        t = (len(out) + 1, [(i, o)])
        out.append(t)
        if g:
            by[g] = t
    return out


def leg(legs, role):
    for i, o in legs:
        if role_of(o) == role:
            return i, o
    return None, None


def plain(order):
    """BUY 62 RELIANCE on NSE, intraday (MIS), limit 1194.80, good for today"""
    side = str(order.get("transaction_type", "")).upper()
    sym = order.get("tradingsymbol", "?")
    typ = str(order.get("order_type", "")).upper()
    validity = VALIDITY_WORDS.get(str(order.get("validity", "DAY")).upper(), order.get("validity"))
    product = PRODUCT_WORDS.get(str(order.get("product", "")).upper(), order.get("product"))
    if typ == "LIMIT":
        how = "limit %s" % _p(order.get("price"))
    elif typ == "SL":
        how = "stop-loss: trigger %s, limit %s" % (_p(order.get("trigger_price")), _p(order.get("price")))
    elif typ == "SL-M":
        how = "stop-loss market: trigger %s" % _p(order.get("trigger_price"))
    else:
        how = typ.lower() or "?"
    return "%s %s %s on %s, %s, %s, %s" % (side, _n(order.get("quantity") or 0), sym, order.get("exchange", "?"),
                                           product, how, validity)


def max_loss(entry, stop=None, rv=None):
    """(rupees, stop, with_charges): the loss if the stop fills at its trigger.
    india-risk's number (charges included) when there is one, else the plan's, else qty x risk."""
    plan = entry.get("plan") or {}
    stop_p = _f((stop or {}).get("trigger_price")) or _f(plan.get("stop"))
    if rv and rv.get("max_loss") is not None:
        return round(float(rv["max_loss"]), 2), stop_p, rv.get("charges") is not None
    if _f(plan.get("max_loss")) is not None:
        return round(_f(plan["max_loss"]), 2), stop_p, _f(plan.get("charges")) is not None
    q, entry_p = _f(entry.get("quantity")), _f(entry.get("price")) or _f(plan.get("entry"))
    if not stop_p or not q or not entry_p:
        return None, stop_p, False
    return round(q * int(round(abs(entry_p - stop_p) * 100)) / 100.0, 2), stop_p, False


def plain_parts(legs, rvs=None):
    """The ticket in plain words, one part per line."""
    rvs = rvs or {}
    parts = []
    _, entry = leg(legs, "entry")
    _, stop = leg(legs, "stop")
    for n, (i, o) in enumerate(legs, 1):
        r = role_of(o)
        text = "%s: %s" % (r, plain(o))
        if r == "stop" and o.get("after") == "entry":
            text += "; placed only after the entry fills"
        parts.append(text)
    if entry is not None:
        plan = entry.get("plan") or {}
        if _f(plan.get("target")):
            parts.append("target %s: watched by india-alerts, not placed (the exit is ticket.py flatten)"
                         % _p(plan["target"]))
        ei = leg(legs, "entry")[0]
        loss, stop_p, with_ch = max_loss(entry, stop, rvs.get(ei))
        if loss is not None:
            text = "max loss about %s if the stop fills at %s%s" % (money(loss), _p(stop_p),
                                                                    ", charges included" if with_ch else "")
            sl = (rvs.get(ei) or {}).get("loss_at_stop_limit")
            if stop is not None and _f(stop.get("price")) and str(stop.get("order_type")).upper() == "SL":
                text += " (%sat its limit %s; a gap below that leaves the stop unfilled)" % (
                    (money(sl) + " ") if sl is not None else "more ", _p(stop["price"]))
            parts.append(text)
    return parts


def plain_ticket(legs, rvs=None):
    return " / ".join(plain_parts(legs, rvs))


def amount_numbers(legs, rvs=None):
    nums = []
    for i, o in legs:
        for k in ("quantity", "price", "trigger_price"):
            v = _f(o.get(k))
            if v is not None:
                nums.append(v)
        for k in ("entry", "limit", "stop", "stop_limit", "target", "max_loss"):
            v = _f((o.get("plan") or {}).get(k))
            if v is not None:
                nums.append(v)
    _, entry = leg(legs, "entry")
    if entry is not None:
        loss = max_loss(entry, leg(legs, "stop")[1], (rvs or {}).get(leg(legs, "entry")[0]))[0]
        if loss is not None:
            nums.append(loss)
    return nums


def parse_yes(reply, legs, batch_symbols=(), rvs=None):
    """Is this reply an explicit yes for THIS one ticket (an entry and its stop)? Returns (ok, why).
    legs: [(index, order)] or a single order."""
    if isinstance(legs, dict):
        legs = [(1, legs)]
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    words = re.findall(r"[a-z']+", raw.lower())
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" counts (not ok, sure, haan, go, do it)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one ticket at a time, not a batch"
    if DOUBT_WORDS & set(words[1:]):
        return False, "the reply has a condition or a doubt in it; ask again"
    syms = {str(o.get("tradingsymbol", "")).upper() for _, o in legs}
    tokens = set(re.findall(r"[A-Z0-9&][A-Z0-9&\-]*", raw.upper()))
    others = {str(s).upper() for s in batch_symbols} - syms
    named = tokens & others
    if named:
        return False, "the reply names %s, but this ticket is for %s" % (", ".join(sorted(named)),
                                                                         ", ".join(sorted(syms)))
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", raw.replace("₹", " "))]
    if nums:
        want = amount_numbers(legs, rvs)
        if not any(abs(n - w) < 0.005 for n in nums for w in want):
            return False, "the reply says %s, but this ticket is %s" % (
                ", ".join(_n(n) for n in nums), plain_ticket(legs, rvs))
    return True, "explicit yes for %s" % plain_ticket(legs, rvs)


# ---------------------------------------------------------------- state

def load_state(path):
    return read_json(path, "broker state", required=False) or {"orders": []}


def latest(state, fp, today):
    """The latest decision recorded today for this fingerprint, or None."""
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today:
            found = rec
    return found


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "india-risk has not checked these orders (run check.py)"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today; run check.py again" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "this exact order was not in the last risk check (it changed or is new); run check.py again"


def status_of(order, risk, state, today):
    """(ready, label, risk_result) for one order."""
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, fp, today)
    if rv is None:
        return False, "NOT READY: " + why, None
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by india-risk: " + "; ".join(rv.get("reasons", [])), rv
    rec = latest(state, fp, today)
    if rec:
        st = rec.get("status")
        if st == "approved":
            return False, "APPROVED by you at %s" % rec.get("at"), rv
        return False, "DONE: %s at %s" % (st, rec.get("at")), rv
    return True, "READY", rv


def ticket_status(legs, risk, state, today):
    """(ready, label, {file index: risk result}, [(index, order) still to decide])."""
    rvs, undecided, problems, done = {}, [], [], []
    for i, o in legs:
        ok, label, rv = status_of(o, risk, state, today)
        if rv:
            rvs[i] = rv
        if latest(state, fingerprint(o), today) is None:
            undecided.append((i, o))
            if not ok:
                problems.append("%s: %s" % (role_of(o), label))
        else:
            done.append("%s %s" % (role_of(o), label))
    if not undecided:
        return False, "; ".join(done), rvs, []
    if problems:
        return False, "; ".join(problems), rvs, undecided
    n = len(undecided)
    return True, "READY: india-risk PASS for %s, waiting for your yes" % (
        "this order" if n == 1 else "%s orders" % ("both" if n == 2 else n)), rvs, undecided


def kind_of(order, rv):
    """entry, stop or exit, as india-risk saw it."""
    if rv and rv.get("kind"):
        return rv["kind"]
    return role_of(order)


def open_stops(state, symbol, today):
    """Stop legs placed today for this symbol with no fill or cancel after."""
    last = {}
    for rec in state.get("orders", []):
        if rec_day(rec) == today and str(rec.get("symbol", "")).upper() == symbol.upper() \
                and rec.get("role") == "stop" and rec.get("fingerprint"):
            last[rec["fingerprint"]] = rec
    return [r for r in last.values() if r.get("status") == "placed"]


# ---------------------------------------------------------------- rendering

def render(legs, no, total, cfg, risk, state, today):
    ready, label, rvs, undecided = ticket_status(legs, risk, state, today)
    paper = cfg.get("paper", True)
    acct = "PAPER mode (nothing is sent to Kite)" if paper else "LIVE ACCOUNT, REAL MONEY"
    lines = ["TICKET %d of %d, %s, Zerodha Kite" % (no, total, acct)]
    for part in plain_parts(legs, rvs):
        lines.append("  " + part)
    for i, o in legs:
        if o.get("why") and role_of(o) != "stop":
            lines.append("  why: " + o["why"])
    lines.append("  " + label)
    for i, o in legs:
        for w in (rvs.get(i) or {}).get("warnings", []):
            lines.append("  note (%s): %s" % (role_of(o), w))
    _, ex = leg(legs, "exit")
    if ex is not None:
        stops = open_stops(state, str(ex.get("tradingsymbol", "")), today)
        if stops:
            lines.append("  FIRST cancel the open stop order%s: %s. If it already filled, you are out: do not "
                         "place this." % ("" if len(stops) == 1 else "s", ", ".join(
                             "%s (%s)" % (r.get("plain"), "a paper order" if r.get("paper")
                                          else "Kite order id %s" % (r.get("broker_order_id") or "?"))
                             for r in stops)))
    shown = undecided or legs
    for i, o in shown:
        r = role_of(o)
        when = ""
        if r == "stop" and o.get("after") == "entry":
            when = ", only after the entry fill is confirmed with get_orders / get_positions"
        lines.append("  Kite place_order input, %s%s%s:" % (r, when, " (paper: shown, not sent)" if paper else ""))
        for l in json.dumps(tool_input(o), indent=2).splitlines():
            lines.append("    " + l)
    if ready:
        what = "this trade (the entry and its stop)" if leg(undecided, "entry")[1] is not None \
            and leg(undecided, "stop")[1] is not None else "this order"
        lines.append("  Reply \"yes\" to take %s, or \"no\" to skip it." % what)
        if paper:
            lines.append("  Paper mode: after your yes the fill is recorded with ticket.py fill --index %d --paper." % no)
        else:
            lines.append("  Claude's own card then asks again for each place_order call: Allow once places it, "
                         "Deny stops it.")
    return "\n".join(lines), ready


def ticket_json(legs, no, cfg, risk, state, today):
    ready, label, rvs, undecided = ticket_status(legs, risk, state, today)
    _, entry = leg(legs, "entry")
    _, stop = leg(legs, "stop")
    loss, stop_p, with_ch = max_loss(entry, stop, rvs.get(leg(legs, "entry")[0])) if entry else (None, None, False)
    return {"index": no, "ready": ready, "status": label, "plain": plain_ticket(legs, rvs),
            "group": legs[0][1].get("group"), "paper": bool(cfg.get("paper", True)),
            "broker": cfg.get("broker", "zerodha"), "tool": "place_order",
            "orders": [{"index": i, "role": role_of(o), "fingerprint": fingerprint(o), "after": o.get("after"),
                        "plain": plain(o), "tool_input": tool_input(o)} for i, o in legs],
            "why": " / ".join(o.get("why", "") for _, o in legs if o.get("why")),
            "max_loss": loss, "stop": stop_p, "charges_included": with_ch}


# ---------------------------------------------------------------- fills -> portfolio

def apply_fill(portfolio_path, cfg, symbol, side, qty, price, date):
    """Record a filled order in trader/portfolio.json: cash and shares, with a
    cost-basis lot per buy (oldest lots are relieved first on a sell)."""
    pf = read_json(portfolio_path, "portfolio", required=False)
    if pf is None:
        budget = cfg.get("budget")
        if not budget:
            raise TicketError("no %s yet and no budget in the config to start it from" % portfolio_path)
        pf = {"cash": float(budget), "positions": []}
    symbol = symbol.upper()
    side = side.lower()
    qty, price = float(qty), float(price)
    if qty <= 0 or price <= 0:
        raise TicketError("a fill needs a positive qty and price")
    cost = round(qty * price, 2)
    pos = None
    for p in pf.setdefault("positions", []):
        if str(p.get("ticker", "")).upper() == symbol:
            pos = p
    if side == "buy":
        if pos is None:
            pos = {"ticker": symbol, "shares": 0.0, "lots": []}
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({"id": "%s-%d" % (symbol, len(lots) + 1), "date": date, "shares": qty, "cost": cost})
        pos["shares"] = round(float(pos.get("shares", 0)) + qty, 6)
        pf["cash"] = round(float(pf.get("cash", 0)) - cost, 2)
    else:
        if pos is None or float(pos.get("shares", 0)) + 1e-9 < qty:
            raise TicketError("cannot record selling %s %s: the portfolio holds %s"
                              % (_n(qty), symbol, pos.get("shares", 0) if pos else 0))
        left = qty
        for lot in sorted(pos.get("lots", []), key=lambda l: l.get("date", "")):
            if left <= 1e-9:
                break
            take = min(left, float(lot["shares"]))
            per = float(lot["cost"]) / float(lot["shares"]) if float(lot["shares"]) else 0
            lot["cost"] = round(float(lot["cost"]) - per * take, 2)
            lot["shares"] = round(float(lot["shares"]) - take, 6)
            left -= take
        pos["lots"] = [l for l in pos.get("lots", []) if float(l["shares"]) > 1e-9]
        pos["shares"] = round(float(pos["shares"]) - qty, 6)
        if pos["shares"] <= 1e-9:
            pf["positions"].remove(pos)
        pf["cash"] = round(float(pf.get("cash", 0)) + cost, 2)
    write_json(portfolio_path, pf)
    return pf


def log_rec(state, now, today, status, order=None, **extra):
    rec = {"at": ist_iso(now), "date": today, "status": status}
    if order is not None:
        rec.update({"fingerprint": fingerprint(order), "symbol": str(order.get("tradingsymbol", "")).upper(),
                    "exchange": order.get("exchange"), "side": str(order.get("transaction_type", "")).upper(),
                    "quantity": order.get("quantity"), "role": role_of(order), "group": order.get("group"),
                    "plain": plain(order)})
    rec.update({k: v for k, v in extra.items() if v is not None})
    state.setdefault("orders", []).append(rec)
    return rec


def log_fill(state_path, now, today, symbol, side, qty, price, order=None, paper=None, ticket=None):
    """A fill in the log (with its order's fingerprint when it belongs to a ticket)."""
    state = load_state(state_path)
    extra = {"price": float(price), "paper": paper, "ticket": ticket}
    if order is None:
        extra.update({"symbol": symbol.upper(), "side": side.upper(), "quantity": _n(float(qty))})
    rec = log_rec(state, now, today, "filled", order, **extra)
    rec["quantity"] = int(float(qty)) if float(qty) == int(float(qty)) else float(qty)
    rec["plain"] = "%s %s %s at %s%s" % (side.upper(), _n(float(qty)), symbol.upper(), _p(price),
                                         " (paper)" if paper else "")
    write_json(state_path, state)


# ---------------------------------------------------------------- commands

def _clock(a):
    now = parse_now(getattr(a, "now", None))
    today = getattr(a, "today", None) or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", today):
        raise TicketError("--today wants a date like 2026-10-26")
    return now, today


def _load(a):
    doc = read_json(a.orders, "orders file (run india-budget, or ticket.py flatten, first)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise TicketError("no orders in %s" % a.orders)
    cfg = read_json(a.config, "config", required=False) or {}
    risk = read_json(a.risk, "risk check", required=False)
    state = load_state(a.state)
    now, today = _clock(a)
    return orders, cfg, risk, state, today, now


def _pick(orders, index):
    ts = tickets(orders)
    if index is None:
        raise TicketError("say which ticket: --index N")
    if not 1 <= index <= len(ts):
        raise TicketError("there is no ticket %d (there are %d)" % (index, len(ts)))
    return ts[index - 1][1]


def _symbols(orders):
    return [o.get("tradingsymbol", "") for o in orders if isinstance(o, dict)]


def _fresh_price(path, sym, now):
    doc = read_json(path, "prices", required=False)
    if not isinstance(doc, dict):
        return None, None
    p = _f((doc.get("prices") or {}).get(sym))
    if p is None or p <= 0:
        return None, None
    det = (doc.get("detail") or {}).get(sym) or {}
    t = parse_utc(det.get("time")) or parse_utc(det.get("fetched")) or parse_utc(doc.get("as_of"))
    if t is None or not -60 <= (now - t).total_seconds() <= FRESH_MINUTES * 60:
        return None, t
    return p, t


def cmd_show(a):
    orders, cfg, risk, state, today, now = _load(a)
    ts = tickets(orders)
    picks = [(a.index, _pick(orders, a.index))] if a.index else ts
    if a.json:
        out = [ticket_json(legs, no, cfg, risk, state, today) for no, legs in picks]
        print(json.dumps(out[0] if a.index else out, indent=2, ensure_ascii=False))
        return 0 if any(t["ready"] for t in out) else 1
    blocks, any_ready = [], False
    for no, legs in picks:
        text, ready = render(legs, no, len(ts), cfg, risk, state, today)
        blocks.append(text)
        any_ready = any_ready or ready
    print("\n\n".join(blocks))
    print("\nOne ticket at a time: a yes covers one trade (an entry and its stop), never the batch. "
          "Not financial advice.")
    return 0 if any_ready else 1


def cmd_approve(a):
    orders, cfg, risk, state, today, now = _load(a)
    legs = _pick(orders, a.index)
    ready, label, rvs, undecided = ticket_status(legs, risk, state, today)
    if not ready:
        raise TicketError("ticket %d is not ready: %s" % (a.index, label))
    ok, why = parse_yes(a.reply, undecided, _symbols(orders), rvs)
    if not ok:
        print("Not recorded: %s. Ask the user again with ticket %d." % (why, a.index))
        return 1
    paper = bool(cfg.get("paper", True))
    _, entry = leg(legs, "entry")
    _, stop = leg(legs, "stop")
    for i, o in undecided:
        loss = stop_p = None
        if role_of(o) == "entry":
            loss, stop_p, _ = max_loss(o, stop, rvs.get(i))
        log_rec(state, now, today, "approved", o, ticket=a.index, index=i, kind=kind_of(o, rvs.get(i)),
                max_loss=loss, stop=stop_p, reply=a.reply, paper=paper, broker=cfg.get("broker", "zerodha"))
    write_json(a.state, state)
    print("Recorded your yes for ticket %d: %s." % (a.index, plain_ticket(undecided, rvs)))
    first_i, first = undecided[0]
    if role_of(first) == "stop":
        filled, _ = _entry_filled(legs, state, today)
        if not filled:
            print("The entry has no fill recorded yet: the stop goes in only after it fills.")
            return 0
    if paper and role_of(first) == "stop":
        log_rec(state, now, today, "placed", first, ticket=a.index, index=first_i, kind="stop",
                broker_order_id="paper", paper=True)
        write_json(a.state, state)
        print("PAPER mode: nothing goes to Kite. Paper stop resting: %s." % plain(first))
        return 0
    if paper:
        print("PAPER mode: do not call place_order, modify_order or cancel_order. Nothing goes to Kite.")
        if role_of(first) == "exit" and open_stops(state, str(first.get("tradingsymbol", "")), today):
            print("The paper stop for %s is cancelled first, when you record this exit's paper fill."
                  % first.get("tradingsymbol"))
        print("Record the paper fill at a real price (the user's, or a fresh one in trader/prices.json):")
        print("  ticket.py fill --index %d --paper --price P" % a.index)
        if stop is not None and any(o is stop for _, o in undecided):
            print("The paper stop is then recorded as resting, for the filled quantity.")
        return 0
    if role_of(first) == "exit":
        stops = open_stops(state, str(first.get("tradingsymbol", "")), today)
        if stops:
            print("1. FIRST cancel the open stop order: cancel_order with variety regular and order_id %s "
                  "(Needs approval). Check get_orders shows it CANCELLED. If it shows COMPLETE, the stop "
                  "already sold: do not place this exit." % ", ".join(str(r.get("broker_order_id") or "?")
                                                                    for r in stops))
            print("2. Then call place_order with exactly:")
        else:
            print("Now call place_order with exactly:")
    else:
        print("Now call place_order with exactly:")
    print(json.dumps(tool_input(first), indent=2))
    print("Claude's card shows Allow once / Deny. Then: ticket.py result --index %d --leg %s --status "
          "placed|denied|failed --order-id ID%s" % (a.index, role_of(first),
                                                    " --stop-cancelled" if role_of(first) == "exit" else ""))
    if any(o is stop for _, o in undecided) and stop is not first:
        print("Do NOT place the stop yet. When get_orders shows the entry COMPLETE and get_positions shows the "
              "shares, record the fill (ticket.py fill --index %d --qty Q --price P); it prints the stop's "
              "place_order input for the filled quantity." % a.index)
    return 0


def cmd_decline(a):
    orders, cfg, risk, state, today, now = _load(a)
    legs = _pick(orders, a.index)
    for i, o in legs:
        if latest(state, fingerprint(o), today) is None:
            log_rec(state, now, today, "declined", o, ticket=a.index, index=i, reply=a.reply or "no")
    write_json(a.state, state)
    print("Recorded: you said no to ticket %d (%s). Nothing was sent." % (a.index, plain_ticket(legs)))
    return 0


def _choose_leg(legs, state, today, want, statuses):
    """The leg named by --leg, else the first whose latest record has one of these statuses."""
    for i, o in legs:
        if want and role_of(o) != want:
            continue
        rec = latest(state, fingerprint(o), today)
        if want or (rec and rec.get("status") in statuses):
            return i, o, rec
    return None, None, None


def _entry_filled(legs, state, today):
    _, entry = leg(legs, "entry")
    if entry is None:
        return True, None
    fp = fingerprint(entry)
    qty = 0.0
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today and rec.get("status") == "filled":
            qty += _f(rec.get("quantity")) or 0
    return qty > 0, qty


def _cancel_open_stops(state, now, today, symbol, note):
    out = []
    for r in open_stops(state, symbol, today):
        out.append(log_rec(state, now, today, "cancelled", None, fingerprint=r["fingerprint"], symbol=r["symbol"],
                           side=r.get("side"), role="stop", group=r.get("group"), plain=r.get("plain"),
                           broker_order_id=r.get("broker_order_id"), note=note))
    return out


def _rewrite_stop(a, legs, filled_qty):
    """A partial entry fill: the stop must be for the filled quantity. Rewrites the stop leg in
    trader/orders.json; that is a new order, so it needs a new risk check and a new yes."""
    si, stop = leg(legs, "stop")
    if stop is None or stop.get("quantity") == filled_qty:
        return None
    doc = read_json(a.orders, "orders file")
    target = doc["orders"] if isinstance(doc, dict) else doc
    target[si - 1]["quantity"] = int(filled_qty)
    write_json(a.orders, doc)
    return ("The entry filled %s of %s shares. The stop must be for the %s you hold, never more (a bigger stop "
            "would sell short): the stop leg in %s now says %s. That is a new order: run india-risk "
            "(check.py) again, then ask the user's yes for the stop (ticket.py show).%s"
            % (_n(filled_qty), stop.get("quantity"), _n(filled_qty), a.orders, _n(filled_qty),
               "" if a.paper else " The rest of the entry may still be open at Kite (get_orders shows it OPEN): "
               "cancel it (cancel_order, Needs approval) or it can fill later without a stop."))


def cmd_result(a):
    orders, cfg, risk, state, today, now = _load(a)
    if cfg.get("paper", True):
        raise TicketError("paper mode (\"paper\": true): nothing is sent to Kite, so there is no broker result. "
                          "Record the paper fill instead: ticket.py fill --index %d --paper" % a.index)
    legs = _pick(orders, a.index)
    want_status = ("placed",) if a.status == "cancelled" else ("approved",)
    i, order, rec = _choose_leg(legs, state, today, a.leg, want_status)
    if order is None:
        raise TicketError("ticket %d has no %s recorded today; a result can only follow %s"
                          % (a.index, "placed order" if a.status == "cancelled" else "yes",
                             "a placed order" if a.status == "cancelled" else "an approval"))
    if not rec or rec.get("status") not in want_status:
        if a.status == "cancelled":
            raise TicketError("the %s of ticket %d is not recorded as placed today; only a placed order can be "
                              "cancelled" % (role_of(order), a.index))
        raise TicketError("the %s of ticket %d has no yes recorded today; a result can only follow an approval"
                          % (role_of(order), a.index))
    r = role_of(order)
    if r == "stop" and a.status == "placed":
        filled, _ = _entry_filled(legs, state, today)
        if not filled:
            raise TicketError("the entry has no fill recorded: the stop goes in only after the entry fill is "
                              "confirmed with get_orders / get_positions. Record it first: ticket.py fill "
                              "--index %d --qty Q --price P" % a.index)
    if r == "exit" and a.status == "placed":
        stops = open_stops(state, str(order.get("tradingsymbol", "")), today)
        if stops and not a.stop_cancelled:
            raise TicketError("the log still shows the stop order %s open for %s: cancel it first (cancel_order, "
                              "then get_orders shows CANCELLED), then pass --stop-cancelled"
                              % (", ".join(str(s.get("broker_order_id") or "?") for s in stops),
                                 order.get("tradingsymbol")))
        if stops:
            _cancel_open_stops(state, now, today, str(order.get("tradingsymbol", "")),
                               "cancelled before the exit (you confirmed it with get_orders)")
    extra = {k: rec.get(k) for k in ("kind", "max_loss", "stop", "ticket", "index") if rec.get(k) is not None}
    log_rec(state, now, today, a.status, order, broker_order_id=a.order_id, note=a.note, paper=False, **extra)
    write_json(a.state, state)
    print("Recorded the %s of ticket %d as %s." % (r, a.index, a.status))
    if a.status == "placed" and a.filled_qty and a.filled_price:
        a.qty, a.price, a.leg, a.paper = a.filled_qty, a.filled_price, r, False
        return _fill_leg(a, orders, cfg, state, today, now, legs, i, order)
    if a.status == "placed":
        print("When it fills (get_orders shows COMPLETE), record it: ticket.py fill --index %d --leg %s "
              "--qty Q --price P" % (a.index, r))
    return 0


def _fill_leg(a, orders, cfg, state, today, now, legs, i, order):
    """Record a fill for one leg of a ticket (paper or live) and say what comes next."""
    r = role_of(order)
    sym = str(order.get("tradingsymbol", "")).upper()
    side = str(order.get("transaction_type", "")).lower()
    paper = bool(a.paper)
    qty = _f(a.qty) if a.qty is not None else _f(order.get("quantity"))
    if not qty or qty <= 0 or qty != int(qty):
        raise TicketError("--qty wants a whole number of shares, like --qty %s" % order.get("quantity"))
    if qty > float(order.get("quantity") or 0):
        raise TicketError("the %s is for %s shares, not %s" % (r, order.get("quantity"), _n(qty)))
    price = _f(a.price) if a.price is not None else None
    if price is None:
        price, t = _fresh_price(a.prices, sym, now)
        if price is None:
            raise TicketError("a fill needs a price: --price P (the user's, or Kite's get_ltp), or a fresh "
                              "(under %d minutes) price in %s" % (FRESH_MINUTES, a.prices))
    if price <= 0:
        raise TicketError("a fill needs a positive price")
    if paper:
        typ = str(order.get("order_type", "")).upper()
        lim, trig = _f(order.get("price")), _f(order.get("trigger_price"))
        if typ == "LIMIT" and side == "buy" and price > lim + 0.005:
            raise TicketError("a LIMIT BUY at %s does not fill at %s: a real order would still be waiting. "
                              "Wait, or skip the trade" % (_p(lim), _p(price)))
        if typ == "LIMIT" and side == "sell" and price < lim - 0.005:
            raise TicketError("a LIMIT SELL at %s does not fill at %s: lower the limit with a new flatten ticket"
                              % (_p(lim), _p(price)))
        if typ == "SL" and side == "sell":
            if price > trig + 0.005:
                raise TicketError("the stop triggers at %s; %s is above it, so it has not triggered" % (_p(trig),
                                                                                                      _p(price)))
            if price < lim - 0.005:
                raise TicketError("%s is below the stop's limit %s: a real SL order would not fill (the price "
                                  "gapped through it). You still hold the shares: get out with ticket.py flatten"
                                  % (_p(price), _p(lim)))
        if r == "exit":
            _cancel_open_stops(state, now, today, sym, "paper stop cancelled before the exit")
        rec = latest(state, fingerprint(order), today)
        extra = {k: rec.get(k) for k in ("kind", "max_loss", "stop") if rec and rec.get(k) is not None}
        log_rec(state, now, today, "placed", order, ticket=a.index, index=i, broker_order_id="paper",
                paper=True, **extra)
        write_json(a.state, state)
    pf = apply_fill(a.portfolio, cfg, sym, side, qty, price, today)
    log_fill(a.state, now, today, sym, side, qty, price, order, paper, a.index)
    state = load_state(a.state)
    print("Recorded the %s fill: %s %s %s at %s%s. Cash now %s." % (r, side.upper(), _n(qty), sym, _p(price),
                                                                    " (paper)" if paper else "", money(pf["cash"])))
    if r != "entry":
        return 0
    si, stop = leg(legs, "stop")
    if stop is None:
        return 0
    note = _rewrite_stop(a, legs, int(qty))
    if note:
        print(note)
        return 0
    if latest(state, fingerprint(stop), today) is None or latest(state, fingerprint(stop), today).get(
            "status") != "approved":
        return 0
    if paper:
        log_rec(state, now, today, "placed", stop, ticket=a.index, index=si, kind="stop", broker_order_id="paper",
                paper=True)
        write_json(a.state, state)
        print("Paper stop resting: %s. If india-alerts rings at the stop, record it: ticket.py fill --index %d "
              "--leg stop --paper --price P. To get out before that: ticket.py flatten --symbol %s."
              % (plain(stop), a.index, sym))
    else:
        print("Confirm the fill with get_orders (the entry shows COMPLETE) and get_positions (%s %s %s), then "
              "place the stop: place_order with exactly:" % (_n(qty), sym, order.get("product")))
        print(json.dumps(tool_input(stop), indent=2))
        print("Then: ticket.py result --index %d --leg stop --status placed --order-id ID" % a.index)
    return 0


def cmd_fill(a):
    cfg = read_json(a.config, "config", required=False) or {}
    now = parse_now(a.now)
    date = a.date or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise TicketError("--date wants a date like 2026-10-26")
    if a.index is not None:
        if a.paper and not cfg.get("paper", True):
            raise TicketError("the config is live (\"paper\": false): a paper fill would not match your real "
                              "account. Record the real fill without --paper")
        if not a.paper and cfg.get("paper", True):
            raise TicketError("paper mode (\"paper\": true): record it as a paper fill with --paper")
        a.today = date
        orders, cfg, risk, state, today, now = _load(a)
        legs = _pick(orders, a.index)
        statuses = ("approved", "placed") if a.paper else ("placed",)
        i, order, rec = _choose_leg(legs, state, today, a.leg, statuses)
        if order is None:
            raise TicketError("ticket %d has no %sorder waiting for a fill (%s)"
                              % (a.index, (a.leg + " ") if a.leg else "",
                                 "a paper fill follows your yes" if a.paper else "a fill follows a placed order"))
        need = "approved" if a.paper and role_of(order) != "stop" else "placed"
        if not rec or rec.get("status") != need:
            raise TicketError("the %s of ticket %d is %s, not %s: %s"
                              % (role_of(order), a.index, rec.get("status") if rec else "not decided", need,
                                 "a paper fill follows your yes" if need == "approved"
                                 else "a fill follows a placed order"))
        return _fill_leg(a, orders, cfg, state, today, now, legs, i, order)
    for k in ("symbol", "side", "qty", "price"):
        if getattr(a, k) is None:
            raise TicketError("fill wants --index N (a ticket), or --symbol, --side, --qty and --price")
    pf = apply_fill(a.portfolio, cfg, a.symbol, a.side, a.qty, a.price, date)
    log_fill(a.state, now, date, a.symbol, a.side, a.qty, a.price, paper=True if a.paper else None)
    print("Recorded %s %s %s at %s on %s in %s%s. Cash now %s." % (a.side, a.qty, a.symbol.upper(), money(a.price),
                                                                  date, a.portfolio, " (paper)" if a.paper else "",
                                                                  money(pf["cash"])))
    return 0


def cmd_flatten(a):
    cfg = read_json(a.config, "config", required=False) or {}
    sym = a.symbol.strip().upper()
    if sym.endswith(".NS") or sym.endswith(".BO"):
        sym = sym[:-3]
    now, today = _clock(a)
    source = "the broker, get_positions"
    if a.qty is not None:
        qty = _f(a.qty)
        if qty is None or qty <= 0 or qty != int(qty):
            raise TicketError("--qty wants the whole number of shares you hold, like --qty 62")
    else:
        pf = read_json(a.portfolio, "portfolio", required=False) or {}
        qty = sum(_f(p.get("shares", p.get("qty"))) or 0 for p in pf.get("positions", [])
                  if str(p.get("ticker") or p.get("symbol") or "").upper() == sym)
        source = a.portfolio
        if qty <= 1e-9:
            raise TicketError("nothing to flatten: no %s shares in %s. Check the broker (get_positions) and "
                              "pass --qty if you do hold some" % (sym, a.portfolio))
        if qty != int(qty):
            raise TicketError("%s shows %s %s shares; pass the whole number Kite shows with --qty" % (a.portfolio,
                                                                                                   _n(qty), sym))
    last, price_from = _f(a.price), "--price"
    if last is None:
        last, t = _fresh_price(a.prices, sym, now)
        price_from = a.prices
        if last is None:
            raise TicketError("flatten needs the last price for its SELL LIMIT: --price P (Kite's get_ltp "
                              "NSE:%s, read only) or a fresh price in %s (alerts.py quote %s --write)"
                              % (sym, a.prices, sym))
    buf = float(cfg.get("stop_limit_buffer_pct", 0.5) or 0)
    limit = tick_down(last * (1 - buf / 100.0), sym, cfg)
    plan = read_json(a.plan, "plan", required=False) or {}
    mine = str(plan.get("symbol", "")).upper().replace(".NS", "") == sym
    flat_by = (plan.get("flat_by") if mine else None) or cfg.get("flat_by") or "15:00"
    exchange = str((plan.get("exchange") if mine else None) or cfg.get("exchange") or "NSE").upper()
    order = {"role": "exit", "group": "%s-%s-flat" % (sym, today), "variety": "regular", "exchange": exchange,
             "tradingsymbol": sym, "transaction_type": "SELL", "quantity": int(qty),
             "product": str(cfg.get("product") or "MIS").upper(), "order_type": "LIMIT", "price": limit,
             "validity": "DAY", "tag": "india-flat",
             "why": ("flat by %s IST: closes the %s intraday trade before Zerodha's own square-off (qty from %s; "
                     "limit = last %s minus %s%%, rounded down to the tick). Cancel the open stop order first."
                     % (flat_by, sym, source, _p(last), _n(buf)))}
    old = read_json(a.orders, "orders", required=False)
    replaced = len(old.get("orders", [])) if isinstance(old, dict) else 0
    doc = {"created_by": "india-broker", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "proposed",
           "paper": bool(cfg.get("paper", True)),
           "note": "Flatten ticket. Nothing is placed until india-risk passes it and you say yes in the chat.",
           "orders": [order]}
    write_json(a.orders, doc)
    print("Flatten %s: %s (flat by %s IST)." % (sym, plain(order), flat_by))
    print("Wrote it to %s%s." % (a.orders, (" (it replaced %d earlier proposal%s)"
                                             % (replaced, "" if replaced == 1 else "s")) if replaced else ""))
    if a.qty is None:
        print("The qty comes from %s. Check it against get_positions before you say yes." % source)
    state = load_state(a.state)
    stops = open_stops(state, sym, today)
    paper = cfg.get("paper", True)
    print("Next:")
    print("  1. india-risk: check.py. A sell that only closes what you hold is never blocked by the daily")
    print("     limits or the entry window.")
    print("  2. ticket.py show, then the user's yes for this one order.")
    if paper:
        print("  3. Paper mode: no Kite tool is called. ticket.py fill --index 1 --paper --price P records the exit")
        print("     and cancels the paper stop%s." % ((" (" + ", ".join(r.get("plain") for r in stops) + ")")
                                                     if stops else ""))
    else:
        print("  3. FIRST cancel the open SL order%s: get_orders shows it (status TRIGGER PENDING), then" % (
            (" (Kite order id %s)" % ", ".join(str(r.get("broker_order_id") or "?") for r in stops)) if stops else ""))
        print("     cancel_order with variety regular and its order_id (Needs approval). On Kite an open SL SELL")
        print("     and this SELL could both fill and leave you short. Then place_order with the ticket, then")
        print("     ticket.py result --index 1 --leg exit --status placed --order-id ID --stop-cancelled.")
    print("  If the LIMIT does not fill (the price fell through it), make a new flatten ticket at the new price.")
    print("  Zerodha squares off MIS itself at 15:12 (F&O stocks) or 15:25, for ₹50 + 18% GST an order.")
    return 0


def cmd_log(a):
    state = load_state(a.state)
    now, today = _clock(a)
    rows = [r for r in state.get("orders", []) if a.all or rec_day(r) == today]
    if not rows:
        print("No decisions recorded%s." % ("" if a.all else " today"))
        return 1
    for r in rows:
        extra = (" id " + str(r["broker_order_id"])) if r.get("broker_order_id") else ""
        reply = (' reply "%s"' % r["reply"]) if r.get("reply") and r["status"] in ("approved", "declined") else ""
        loss = (" max loss %s" % money(r["max_loss"])) if r.get("max_loss") is not None and r["status"] == "placed" \
            else ""
        idx = ("#%s" % r["ticket"]) if r.get("ticket") is not None else "  "
        print("%s  %s %-9s %s%s%s%s" % (r.get("at"), idx, r.get("status"), r.get("plain"), reply, loss, extra))
    placed = sum(1 for r in rows if r.get("status") == "placed")
    print("%d placed%s." % (placed, "" if a.all else " today"))
    return 0


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Order tickets and your yes (india-broker). Never places orders.")
    sub = ap.add_subparsers(dest="cmd")

    def clock(p):
        p.add_argument("--now", help="pretend it is this India time, e.g. \"2026-10-26 10:00\"")
        p.add_argument("--today", help="YYYY-MM-DD (default: today in India)")

    def common(p):
        p.add_argument("--orders", default="trader/orders.json")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--risk", default="trader/state/risk.json")
        p.add_argument("--state", default="trader/state/broker.json")
        clock(p)

    p = sub.add_parser("show", help="render the tickets")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve", help="record the user's explicit yes for one ticket")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="the user's reply, word for word")
    p = sub.add_parser("decline", help="record a no")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply")
    p = sub.add_parser("result", help="live: record what happened after the Allow once / Deny card")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--leg", choices=["entry", "stop", "exit"])
    p.add_argument("--status", required=True, choices=["placed", "denied", "failed", "cancelled"])
    p.add_argument("--order-id", "--broker-id", dest="order_id")
    p.add_argument("--note")
    p.add_argument("--filled-qty")
    p.add_argument("--filled-price")
    p.add_argument("--stop-cancelled", action="store_true",
                   help="for an exit: you cancelled the open stop order and get_orders shows it CANCELLED")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--prices", default="trader/prices.json")
    p = sub.add_parser("fill", help="record a fill in trader/portfolio.json (--paper for a paper fill)")
    p.add_argument("--index", type=int, help="the ticket this fill belongs to")
    p.add_argument("--leg", choices=["entry", "stop", "exit"])
    p.add_argument("--paper", action="store_true", help="a paper fill: nothing was sent to Kite")
    p.add_argument("--symbol")
    p.add_argument("--side", choices=["buy", "sell"])
    p.add_argument("--qty")
    p.add_argument("--price", help="the fill price (default: a fresh price in trader/prices.json)")
    p.add_argument("--date", help="YYYY-MM-DD (default: today in India)")
    p.add_argument("--now")
    p.add_argument("--orders", default="trader/orders.json")
    p.add_argument("--risk", default="trader/state/risk.json")
    p.add_argument("--prices", default="trader/prices.json")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--state", default="trader/state/broker.json")
    p = sub.add_parser("flatten", help="the exit ticket that closes a symbol's position by flat_by")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", help="shares you hold, from get_positions (default: trader/portfolio.json)")
    p.add_argument("--price", help="the last price, from get_ltp (default: a fresh price in trader/prices.json)")
    p.add_argument("--orders", default="trader/orders.json", help="where the ticket is written")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--prices", default="trader/prices.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--state", default="trader/state/broker.json")
    clock(p)
    p = sub.add_parser("log", help="today's decisions")
    p.add_argument("--state", default="trader/state/broker.json")
    clock(p)
    p.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"show": cmd_show, "approve": cmd_approve, "decline": cmd_decline, "result": cmd_result,
                "fill": cmd_fill, "flatten": cmd_flatten, "log": cmd_log}[a.cmd](a)
    except TicketError as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
