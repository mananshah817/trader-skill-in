---
name: india-risk
description: >-
  Risk Manager. Checks every NSE intraday trade against your limits and
  stops anything over them, before it can reach Zerodha. For each entry: a
  protective stop leg is required, the loss at the stop plus Zerodha's
  charges must fit your max loss per trade, reward:risk must meet your
  minimum, the day's total risk must fit your daily loss limit, no more than
  your trades per day, and no new entries outside your entry window, on an
  NSE holiday or special session, or at or after your flat-by time. It also
  checks every order is a valid Kite place_order (exchange, product MIS,
  LIMIT or SL with the right prices, prices on the NSE tick, whole-number
  quantity) and keeps the basics: blocked symbols, order size, position cap,
  cash buffer, margin, short sales, F&O always blocked, duplicates and a
  daily order limit. Getting out (a stop or an exit) is never blocked by a
  day limit. Prints PASS or BLOCK with the reason and records the verdicts
  so india-broker only shows orders that passed. Use when the user says
  "check my order", "is this safe", "risk check", "does this break my
  rules", "can I take this trade", "how much have I risked today", or before
  /india-broker, always.
argument-hint: "[path to orders.json]"
---

# india-risk

The Risk Manager is the gate between a proposal and the broker. Every order written by india-budget or
india-broker's flatten passes through it. A BLOCK is final for that order: it does not go to the user
for a yes, and it does not go to Kite. SEBI's study of FY23 found that about 7 in 10 (71%) individual
intraday traders in the cash segment made a loss. These limits keep each loss and each day's losses
small. They do not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives). The commands use
`python` (Windows); on macOS/Linux use `python3`.

```bash
python "${CLAUDE_SKILL_DIR}/check.py"                           # trader/ files; writes trader/state/risk.json
python "${CLAUDE_SKILL_DIR}/check.py" --json
python "${CLAUDE_SKILL_DIR}/check.py" --no-write                # look without recording
python "${CLAUDE_SKILL_DIR}/check.py" --now "2026-10-26 10:00"  # pin the clock (India time, IST)
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (india-budget, or india-broker's flatten), Kite place_order fields |
| `trader/config.json` | the limits (india-budget ships `config.example.json`) |
| `trader/portfolio.json` | cash and holdings; if missing, a fresh account funded with the budget |
| `trader/prices.json` | a fresh price raises the entry it measures reward:risk from; also values holdings |
| `trader/state/broker.json` | what was placed today (paper or live): duplicates, trades per day, daily loss |
| `trader/state/risk.json` | the verdicts it writes, with a fingerprint of each exact order |

## Entries, stops and exits

An **entry** opens or adds to a position: a BUY, or a SELL that would go short. A **stop** is the
protective SELL leg of an entry (`"role": "stop"`, same `group`). It is tied to its entry: it passes
only if its entry is in the same batch and passed, or if you already hold the shares (the entry
filled). A stop whose entry was blocked, or that would sell shares you do not hold, is blocked: it
would be a short sale. An **exit** only reduces what you already hold.

Getting out must always be possible, so a stop or an exit is checked for format, asset and short sales
only. Every other rule (day limits, entry window, flat-by, loss, reward:risk, blocked list, size,
duplicates) turns into a warning for it, never a BLOCK.

## The checks

Orders are checked in file order, as if each passed order had filled, so a batch that is fine order by
order but breaks a limit together is caught. A stop leg is contingent, so it does not move cash.

| check | blocks when |
| --- | --- |
| format | not Kite's place_order fields: exchange other than NSE/BSE, transaction_type not BUY/SELL, variety other than regular, product other than MIS (CNC only to sell delivery shares), quantity not a positive JSON integer, order_type MARKET (Kite wants market protection and the Kite MCP place_order tool cannot send it), LIMIT without a price, SL without trigger_price and price, an SL SELL with its price above its trigger, SL-M without trigger_price, a price off the NSE tick, validity not DAY/IOC/TTL, a tag over 20 characters, a constant in the wrong case |
| asset | always: a derivative (an NFO/BFO order, or a futures or options symbol): this pack is cash equity only and F&O is never on; anything that is not an NSE or BSE equity symbol; a trade-for-trade series (BE, BZ) |
| short | a sell of more than you hold (`allow_short` false) |
| stop leg | a stop whose entry is not in the batch, or was blocked, and whose shares you do not hold |
| blocked | (entries) the symbol is on the `blocked` list |
| size | (entries) under `min_order_inr` or over `max_order_inr` |
| position | (entries) the stock would be over `max_position_pct` of the account |
| cash | (entries) the buy would leave less than the cash buffer, or need margin (MIS leverage is off) |
| stop | (entries) no stop while `require_stop` is true (a `"role": "stop"` leg in the same group, or the order's plan stop); a stop on the wrong side of the entry; a stop leg for fewer shares than the entry |
| loss | (entries) qty x (entry minus stop) + charges over `max_loss_per_trade_inr` |
| reward:risk | (entries) (target minus entry) / (entry minus stop) under `min_reward_risk` |
| daily loss | (entries) the max losses (charges included) of today's entries, plus this one, over `max_daily_loss_inr` |
| trades | (entries) `max_trades_per_day` entries already placed today |
| window | (entries) before `no_entries_before` or after `no_entries_after`, the market closed (weekend, NSE holiday, outside 09:15 to 15:30), a special session unless `allow_special_sessions` is true, or an MIS entry at or after `flat_by` |
| duplicate | (entries) the same symbol and side twice in one batch, or already placed today |
| daily limit | (entries) `max_orders_per_day` orders already placed today |

How the numbers work:

- **The entry it measures the loss from** is the limit price of a LIMIT order: the most it can pay.
  The stop is the stop leg's trigger (or the plan's stop). **Charges** are Zerodha's intraday charges
  for the round trip at the entry and the stop (the same function as india-budget), when
  `include_charges` is true. The tick schedule is NSE's, reviewed monthly: check Kite's instrument list
  if an order is rejected for its price.
- **Reward:risk** is measured from the price it expects to pay: the HIGHER of the plan's entry and a
  fresh price from `trader/prices.json` (under 15 minutes old), never above the limit. It also says
  the reward:risk at the limit. A plan written at 10:00 cannot understate the risk at 10:15 after the
  price ran up. An older price is not used, and it says so.
- **The stop-limit price**: an SL order sells no lower than its limit. It says the loss if the stop
  fills there. A gap below that limit leaves the stop unfilled: the loss is then bigger still.
- **Daily loss** counts the full max loss of every entry placed today, win or lose, as india-broker
  logged it (paper fills count too). Three full-size reel trades use ₹2,303.07 of a ₹3,000 day.
- **The window** is India time (IST, UTC+5:30, no daylight saving), worked out in the tool, never from
  the computer's clock zone. Weekends and the NSE holidays of 2026 are closed. For 2027 it warns: NSE
  has not published its 2027 holiday list in this tool yet: check nseindia.com before trading. The
  Muhurat session (Sunday 2026-11-08, 14:20 to 15:30) blocks new entries unless
  `"allow_special_sessions": true`. Set both `no_entries_*` to null to switch the window off.
- **flat_by** (15:00): no new MIS entry at or after it. Zerodha squares off MIS itself at 15:12 for F&O
  stocks (which go into the 15:15 closing auction, where stop-loss orders are not allowed) and 15:25
  for the rest, for ₹50 + 18% GST an order.

Warnings do not block, but say them to the user: a LIVE account, a stop that lives only in the plan
and not as a stop leg, a price too old to use, a latest price above the limit (the order may not fill),
a stop trigger above the last price (Kite rejects it), a special session, a year without a holiday list.

## Steps

1. If the price may have moved since the plan, refresh it first (india-alerts:
   `alerts.py quote RELIANCE --write`, or Kite's read-only `get_ltp`).
2. Run `check.py`. Exit code 0 means every order passed, 1 means at least one was blocked.
3. Tell the user the result in plain words, trade by trade: the max loss at the stop with charges,
   the loss at the stop-limit, reward:risk, and how much of today's limits it uses. For a BLOCK, give
   the reason and the fix (fewer shares, a closer stop from india-exit, wait for the window, or skip
   the trade). Never change a limit yourself.
4. If an order changes in any way (a partial fill rewrites the stop for the filled quantity), run the
   check again. india-broker matches each order to its verdict by fingerprint, so an edited order is
   not ready until it passes again, and a verdict from yesterday does not count today.

## Example: the reel

The RELIANCE group from india-budget at 10:00 IST on Monday 2026-10-26, on a fresh ₹1,00,000 paper
account with the example config:

```
PASS  #1 BUY RELIANCE 62 shares, entry
          entry 1194.80 (the limit price), stop 1183.60, target 1203.60: risk 11.20 a share
          max loss ₹767.69 with ₹73.29 charges (limit ₹1,000), reward:risk 1.00 from 1193.60 (0.79 at the limit) (min 1)
          if the stop fills at its limit 1177.60: ₹1,139.58; a gap below it leaves the stop unfilled
          today before this: 0 of 3 trades, ₹0 of ₹3,000 daily loss
PASS  #2 SELL RELIANCE 62 shares, stop for #1
          SL trigger 1183.60, limit 1177.60: placed only after #1 fills, for the filled quantity

2 passed, 0 blocked at 2026-10-26 10:00 IST. Cash after the passed orders: ₹25,922.40 (paper account).
```

With a ₹700 max loss it is blocked, and its stop with it:

```
BLOCK #1 BUY RELIANCE 62 shares, entry
        - loss: 62 shares x 11.20 risk a share = ₹694.40 plus ₹73.29 charges = ₹767.69 at the stop, over your ₹700 max loss per trade
BLOCK #2 SELL RELIANCE 62 shares, stop for #1
        - stop: its entry (#1) was blocked, so there is nothing to protect
```

The same group checked with a fresh 09:58 quote of 1194.00 in `trader/prices.json` is blocked:
reward:risk 0.92 from 1194.00. At 09:25 it is blocked by the window (no new entries before 09:30), on
2026-11-10 by the Diwali-Balipratipada holiday, at 14:31 by the entry cutoff, and from 15:00 by flat_by
even with the cutoff switched off. (The BLOCK lines above leave out the info lines.) The 15:00 SELL LIMIT that
closes the position passes, even though it is after the 14:30 entry cutoff: it is an exit.

## Rules

- A PASS only means the order is allowed to be shown to the user. It is not a yes.
- Every number comes from the config, the order, the portfolio, the prices or the broker log.
- Free Yahoo prices are unofficial and can lag; Kite's price is the one that fills, and a stop can fill
  at its limit, or not at all, in a fast market.

## What it never does

It never places an order, never edits an order to make it pass, never loosens a limit, never blocks a
sell that only gets the user out, never passes F&O, and never treats a PASS as the user's yes.

## Safety

- Not financial advice. This is software that reads public data and does arithmetic. A PASS is a rules
  check, not a recommendation.
- SEBI's study of FY23: about 7 in 10 (71%) individual intraday traders in the cash segment made a loss.
- No trade goes through until you say yes. Paper first (`"paper": true`); going live is the user's own
  decision, in their own config.
- Nothing in this pack places an order except india-broker, and only when all three hold: india-risk
  passed that exact order today, the user typed an explicit yes for that one trade (the entry and its
  stop) in the chat, and the user clicked Allow once on Claude's own card for each place_order call
  because the order tools are set to "Needs approval". Never set an order tool to "Always allow".
- No F&O (never on in this pack), no short selling, no margin unless the config turns it on and the
  user confirms.
- Kite API keys live in environment variables of the self-hosted Kite MCP server, never in the chat.
