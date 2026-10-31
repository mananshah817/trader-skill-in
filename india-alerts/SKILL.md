---
name: india-alerts
description: >-
  Price Alerts for NSE. Rings when a price, a stop, the target or the opening
  bell hits. Turns the trade plan into three alerts in one go (the stop, the
  target and "flat before square-off" at your flat-by time, 15:00 IST by
  default), plus above / below / stop / target levels, percent moves from the
  previous close, any time of day, and the NSE opening bell at 09:15, each
  ringing once so you are not spammed. Knows NSE hours in India time (pre-open
  09:00, continuous trading 09:15 to 15:30, the 15:15 closing auction for F&O
  stocks, Zerodha's MIS auto square-off), the 2026 NSE holidays and the
  Muhurat special session, fetches free delayed quotes in rupees (Yahoo chart
  JSON for RELIANCE.NS, with the BSE listing as a fallback) one symbol at a
  time, and writes trader/prices.json for sizing and the risk check. Use when
  the user says "alert me when", "tell me if RELIANCE hits", "watch my stop",
  "watch my target", "watch my trade", "remind me to close by 3 pm", "is the
  market open", "is NSE open today", "when does the market open", "get me a
  price", or right after /india-exit writes a plan.
argument-hint: "[symbol] [price]"
---

# india-alerts

Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when a stop alert rings,
nothing is sold by this skill. In this pack only ONE order rests at Zerodha
while a trade is open: the stop-loss (an SL order, placed after the entry
fills). Zerodha has no bracket orders and GTT does not work for intraday, so
the target is NOT an order at the broker. It is watched here. When the target
rings, the way to take it is india-broker `flatten` (cancel the stop order,
then sell), after india-risk and the user's yes.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
The commands use `python` (Windows); on macOS/Linux use `python3`.

```bash
python "${CLAUDE_SKILL_DIR}/alerts.py" clock                          # NSE open or closed, next open
python "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                # stop, target, flat-by from trader/plan.json
python "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --stop 1183.6
python "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --target 1203.6
python "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:00 --label "flat before square-off" --symbol RELIANCE
python "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol INFY --pct 3
python "${CLAUDE_SKILL_DIR}/alerts.py" check                          # exit 0 = something rang
python "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE --write         # -> trader/prices.json
python "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write       # portfolio + plan symbols
python "${CLAUDE_SKILL_DIR}/alerts.py" list
python "${CLAUDE_SKILL_DIR}/alerts.py" reset reliance-plan-stop
```

`--now "2026-10-26 10:05"` pins the clock (India time, IST; `10:05` alone
means today, a trailing `Z` or offset is absolute); `--offline DIR` reads
saved quote files instead of the network. Symbols are NSE trading symbols
(RELIANCE, M&M, BAJAJ-AUTO); `RELIANCE.NS` is accepted, `RELIANCE.BO` means
the BSE listing.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the trade from india-exit, for `add --from-plan` |
| `trader/config.json` | `flat_by` when the plan has none (default 15:00) |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest quotes in rupees (`"currency": "INR"`), merged in, for india-budget and india-risk |
| `trader/portfolio.json` | the symbols held, for `quote --holdings` |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `time` | the India clock passed `at` ("15:00") on a trading day | once per trading day |
| `opening_bell` | NSE is open (09:15, or the start of a special session) | once per trading day |

A rule with `"date"` is watched on that India date only; `--from-plan` rules
carry the plan's date, so yesterday's stop never rings today.

What each ring says:

- **stop**: the price is at or below your stop. The SL order at Zerodha should
  have triggered: check `get_orders` and record the fill (`ticket.py fill`).
  An SL order is a stop-LIMIT: in a fast fall, or a gap through its limit
  price, it can trigger and stay unfilled. If it is still open, the position
  is still open: india-broker `flatten`.
- **target**: the price is at or above your target. Nothing has been sold:
  the target is not an order. To take it, india-broker `flatten SYMBOL`
  (cancel the stop order first, then a SELL LIMIT), then india-risk and the
  user's yes. Never leave a second resting SELL for the target next to the
  stop: if both filled the account would be short.
- **flat by** (from the plan): it is time to be out. Close anything still
  open the same way. Zerodha squares off MIS positions itself at 15:12 for F&O
  stocks and 15:25 for the others, charges ₹50 + GST for each order it
  squares off, and takes no new MIS orders after that. The default 15:00
  keeps you ahead of that and of the 15:15 closing auction, where stop-loss
  orders are not allowed.

## Quotes

- `yahoo` (default): `query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS`,
  an unofficial free JSON endpoint, one request per symbol, half a second
  apart, symbols URL-quoted (`M%26M.NS`). On a 429 it waits and tries once
  more; on a 429 or a 5xx after that it tries `query1`. If the NSE quote
  cannot be fetched at all, it tries the BSE listing (`RELIANCE.BO`) once and
  labels that price `yahoo-bse` (`detail.exchange` is `BSE` in
  `trader/prices.json`): a BSE price can differ from NSE's by a few paise.
  An unknown symbol is Yahoo's "No data found" and is not retried. It
  identifies itself as `india-trader-skill/1.0`.
- `bse`: the BSE listing only, same endpoint.
- The US pack's Stooq source is gone: it has no Indian quotes.
- Free quotes are unofficial, delayed and can lag. They are fine for alerts
  and for checking a size, not for timing an order. The broker's price is the
  one that fills. Each price in `trader/prices.json` keeps its quote time, and
  india-risk only uses one under 15 minutes old.
- If Yahoo is down, read the price from Kite (`get_ltp` or `get_quotes`, both
  read-only, fine in paper mode) and write it into `trader/prices.json` as
  `{"as_of": "...Z", "currency": "INR", "prices": {"RELIANCE": 1192.5}}`.

## The NSE clock

India Standard Time is UTC+5:30 all year (no daylight saving); the tool works
it out itself and ignores the computer's own zone.

- Pre-open 09:00 to 09:15 (orders queue; no trading of our kind).
- Continuous trading 09:15 to 15:30 IST. Since 3 Aug 2026 stocks with F&O
  contracts stop at 15:15 and go into the closing auction (15:15 to 15:35),
  where stop-loss orders are not allowed. There are no early closes on NSE.
- Closed on weekends and on these 2026 trading holidays (NSE circular
  NSE/CMTR/71775): Jan 15 (Municipal Corporation Election, Maharashtra),
  Jan 26, Mar 3 (Holi), Mar 26 (Shri Ram Navami), Mar 31 (Shri Mahavir
  Jayanti), Apr 3 (Good Friday), Apr 14 (Ambedkar Jayanti), May 1
  (Maharashtra Day), May 28 (Bakri Id), Jun 26 (Muharram), Sep 14 (Ganesh
  Chaturthi), Oct 2 (Gandhi Jayanti), Oct 20 (Dussehra), Nov 10
  (Diwali-Balipratipada), Nov 24 (Guru Nanak Jayanti), Dec 25.
- Special session: Sunday 8 Nov 2026, Muhurat trading (Diwali), 14:20 to
  15:30 IST (NSE/CMTR/76941). The clock and the opening bell show it;
  india-risk blocks new entries in it unless `"allow_special_sessions": true`.
- NSE has not published its 2027 list yet (expected mid-December 2026). For
  a 2027 date weekdays count as trading days and the clock and every check
  add: "NSE has not published its 2027 holiday list in this tool yet: check
  nseindia.com before trading". It never invents a holiday.

## Steps

1. Right after india-exit writes the plan, run `add --from-plan`. Confirm
   the three alerts back to the user: the stop, the target and the flat-by
   time, all for the plan's day.
2. Run `check` while the trade is open: every few minutes from a Claude
   scheduled task or `/loop`. Each run fetches only the symbols in today's
   rules and rings each alert once. A check reads the latest delayed quote;
   it is not a live feed, and a price can pass a level between two checks.
3. Tell the user what rang in one line each. A stop alert means the SL order
   has probably triggered: check `get_orders` / `get_positions` and record the
   fill with india-broker; if the SL is still open after the price fell
   through it, say so plainly. A target alert means nothing has been sold
   yet: offer india-broker `flatten`. "Flat before square-off" means close
   anything still open: india-broker `flatten`, then india-risk, then the
   user's yes. Never act for them.

## Example: the reel

RELIANCE on Monday 2026-10-26: entry 1193.60 at 10:00 AM IST, stop 1183.60
(the opening-range midpoint), target 1203.60 (1R), flat by 15:00.
`add --from-plan` writes `reliance-plan-stop`, `reliance-plan-target` and
`reliance-flat-by` for that day. A `check` at 10:06 reads the 10:05 quote,
1192.50, between the stop and the target:

```
NSE OPEN (2026-10-26 10:06 IST, regular session).
Nothing hit. 3 rule(s) watched today, RELIANCE ₹1,192.50.
```

At 13:30 the quote is 1202.50 and nothing rings, although the 13:25 bar
traded up to 1204.00 in between: that is what "a price can pass a level
between two checks" means. At 13:40 the quote is 1204.40:

```
ALERT RELIANCE is ₹1,204.40: at or above your target ₹1,203.60 (plan target: 1R (one times the
risk)). The target is not an order at the broker: to take it, india-broker flatten RELIANCE (cancel
the stop order, then sell), then india-risk and your yes.
```

If the user stays in, at 15:00:

```
ALERT Flat before square-off: it is 15:00 IST (set for 15:00), continuous trading ends at 15:30 IST
(15:15 for F&O stocks, which go into the closing auction). If RELIANCE is still open, close it:
india-broker flatten RELIANCE builds the sell ticket (cancel the open stop order first), then
india-risk and your yes. Zerodha squares off MIS positions itself at 15:12 for F&O stocks and 15:25
for the others, charges ₹50 + GST for each order it squares off, and takes no new MIS orders after that.
```

Each rings once. The next morning the plan's three alerts are no longer
watched; with `add --opening-bell`, the bell rings at 09:15.

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a quote as live. Free quotes are delayed and unofficial.
- Confirm every new rule back to the user: symbol, type, level, day.
- Prices are in rupees (₹), with Indian digit grouping (₹1,00,000).

## What it never does

It never places, cancels or changes an order, never claims a quote is
live, and never predicts where a price is going.

## Safety

- Not financial advice. This is software that reads public data and does arithmetic; a PASS from
  india-risk is a rules check, not a recommendation.
- SEBI's study of FY23: about 7 in 10 (71%) individual intraday traders in the cash segment made a loss.
- Paper trading by default (`"paper": true`). Zerodha has no paper account, so paper mode never calls a Kite
  tool that places, modifies or cancels anything. Going live is the user's own decision, in their own config.
- No trade goes through until you say yes. Nothing in this pack places an order except india-broker, and
  only when all three hold: india-risk passed that exact order today, the user typed an explicit yes for
  that one order group in the chat, and the user clicked Allow once on Claude's own card because the order
  tool is set to "Needs approval". Never set place_order, modify_order or cancel_order to "Always allow".
- Cash equity intraday only: no F&O (never on in this pack), no short selling, no margin unless the config
  allows it and the user confirms.
- Kite API keys live in environment variables, never in the chat.
