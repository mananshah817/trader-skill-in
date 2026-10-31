---
name: india-budget
description: >-
  Budget Manager. Sizes every NSE intraday position to your budget and your
  max loss per trade, in rupees, with Zerodha's charges counted. Reads the
  trade plan from india-exit (entry, stop, target), sets the entry as a
  marketable LIMIT (the plan's entry plus a small buffer, rounded up to the
  NSE tick) and the stop as an SL order, finds the whole shares that fit
  inside four limits (max loss per trade including charges, position cap,
  max order, cash after your buffer), says which limit decided it, and
  writes ONE trade group (entry + protective stop) to trader/orders.json in
  the exact fields of Zerodha Kite's place_order. A stop too far away for
  your max loss is a plain refusal, not a smaller order. Nothing is placed.
  Use when the user says "how many shares", "size this trade", "kitne share
  lun", "I have 1 lakh", "I only want to risk ₹1,000 a trade", "set my
  rules", "keep 10% in cash", or after /india-exit writes a plan.
argument-hint: "[budget in rupees]"
---

# india-budget

The Budget Manager turns a plan ("buy RELIANCE at 1193.60, stop 1183.60")
into an exact trade ("buy 62 shares, limit 1194.80, stop-loss 1183.60"),
inside rules the user sets once. The stop decides the size: the further away
the stop, the fewer shares. It writes a proposal only. india-risk checks it
and india-broker asks the user yes or no. SEBI's study of FY23 found that
about 7 in 10 (71%) individual intraday traders in the cash segment made a
loss. Sizing to a max loss keeps each loss small; it does not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md
lives). The commands use `python` (Windows); on macOS/Linux use `python3`.

```bash
python "${CLAUDE_SKILL_DIR}/size.py"                         # trader/plan.json + trader/config.json
python "${CLAUDE_SKILL_DIR}/size.py" --write                 # write trader/orders.json
python "${CLAUDE_SKILL_DIR}/size.py" --limit --write         # limit at the plan's entry, no buffer
python "${CLAUDE_SKILL_DIR}/size.py" --limit 1194.00 --json  # your own limit price
python "${CLAUDE_SKILL_DIR}/size.py" --targets "${CLAUDE_SKILL_DIR}/targets.example.json" --prices trader/prices.json   # weights mode
```

`--now "2026-10-26 10:00"` pins the clock (India time, IST). A plan for
another day is refused: make a new one with india-exit.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | the rules (start from `config.example.json` in this folder) |
| `trader/plan.json` | the trade: entry, stop, target, setup, flat-by time (india-exit writes it) |
| `trader/prices.json` | optional: a fresh price above the plan's entry means a bigger risk (india-alerts writes it) |
| `trader/portfolio.json` | optional: its cash caps the size (india-broker keeps it) |
| `trader/orders.json` | the proposed trade group (entry + stop) it writes with `--write` |

## Step 1: set the rules once

If `trader/config.json` does not exist, copy `config.example.json` into
`trader/config.json` and ask the user for each value in plain words:

- **budget**: how many rupees this is for. Only money they can afford to lose.
- **cash_buffer_pct**: how much always stays in cash (default 10).
- **max_loss_per_trade_inr**: the most one trade may lose if the stop fills at its trigger, charges
  included (default ₹1,000).
- **max_position_pct**, **max_order_inr**, **min_order_inr**: the biggest position (75% of the budget),
  the biggest order (₹80,000) and the smallest order (₹500).
- The day limits india-risk enforces: **max_daily_loss_inr** (₹3,000), **max_trades_per_day** (3),
  **min_reward_risk** (1.0), **require_stop** (true), **no_entries_before** / **no_entries_after**
  ("09:30" / "14:30") and **flat_by** ("15:00"), all India time.
- **flat_by** is 15:00 on purpose. Since 3 Aug 2026 stocks with F&O contracts leave continuous trading
  at 15:15 for the closing auction, where stop-loss orders are not allowed, and Zerodha squares off MIS
  positions itself at 15:12 (F&O stocks) or 15:25 (the rest), charging ₹50 + 18% GST per order.
- **entry_limit_buffer_pct** (0.1) and **stop_limit_buffer_pct** (0.5): how far above the entry the
  buy limit goes, and how far below the stop trigger the stop's own limit goes.
- **include_charges** (true): count Zerodha's charges in the max loss.
- **product** "MIS" (intraday), **exchange** "NSE". **allow_margin** false means every trade is paid
  in full from cash: MIS leverage is not used. **blocked**: symbols they never want to trade.
- Keep **paper** true. Zerodha has no paper account: in paper mode nothing is sent to Kite at all.

## Step 2: size the trade

```bash
python "${CLAUDE_SKILL_DIR}/size.py" --write
```

**The two orders.** Zerodha has no bracket orders, and GTT does not work with MIS. So one trade is a
group of two Kite orders plus a target that is watched, not placed:

1. **entry**: BUY, MIS, LIMIT at the plan's entry plus `entry_limit_buffer_pct`, rounded UP to the
   tick (a marketable limit: the most one share can cost). Never MARKET: Kite wants market protection
   on a market order and the Kite MCP place_order tool cannot send it.
2. **stop**: SELL, MIS, SL (stop-limit), trigger at the plan's stop, limit `stop_limit_buffer_pct`
   below it, rounded DOWN. It is placed only AFTER the entry fills, for the filled quantity.
3. **target**: stays in the plan. india-alerts rings at it; the exit is a flatten ticket (cancel the
   stop, then SELL LIMIT). Never a second resting SELL next to the stop: if both filled you would be short.

**Ticks.** Prices round to NSE's tick in integer paise: 0.01 under ₹250, 0.05 to ₹1,000, 0.10 to
₹5,000, 0.50 to ₹10,000, 1.00 to ₹20,000, 5.00 above (NSE/CMTR/67133). The tick schedule is NSE's,
reviewed monthly: check Kite's instrument list if an order is rejected for its price. A
`"tick_size": {"RELIANCE": 0.1}` in the config overrides it.

**Charges** (Zerodha equity intraday, checked 2026-10-31): brokerage 0.03% or ₹20 per executed order,
whichever is lower; STT 0.025% on the sell; NSE transaction charge 0.00307% both sides (BSE 0.00375%);
SEBI ₹10 a crore; stamp duty 0.003% on the buy; GST 18% on brokerage, transaction charge and SEBI fee.
They are worked out for the round trip at the entry limit and at the stop (the losing case). Rates can
be overridden under `"charges"` in the config.

Whole shares only. The size is the smallest of four limits:

| limit | shares |
| --- | --- |
| max loss | the most shares whose shares x (limit minus stop) + charges fits `max_loss_per_trade_inr` |
| position cap | budget x `max_position_pct` / limit |
| max order | `max_order_inr` / limit |
| cash | (budget minus the cash buffer) / limit, never more than the cash in `trader/portfolio.json` |

The limit is worked out from the plan's entry, or from the price in `trader/prices.json` if it is
fresh (under 15 minutes old) and higher. A price that ran up since the plan means a bigger loss at the
same stop, so fewer shares. A lower price never shrinks the risk.

Reward:risk is measured from the price it expects to pay (the plan's entry or that fresher higher
price, never above the limit), and the report also says what it is if the buy fills at the limit.

It refuses, and writes no order, when: the size is zero shares, the order is under the minimum,
reward:risk is under `min_reward_risk`, the stop is not below the limit, the target is not above it
(the move already happened), the plan is for another day, the symbol is blocked, the plan is a short,
or the exchange is not NSE or BSE. Say the reason to the user in one line.

## Step 3: show the user

Show the four limits, which one decided the size, the shares, the rupees, the max loss at the stop with
the charges broken down, and the loss if the stop fills at its own limit instead. Say plainly that a
stop is an order, not a promise: a fast fall can fill it at its limit, and a gap below its limit leaves
it unfilled and the loss bigger. Then hand over to `/india-risk`.

## Example: the reel

RELIANCE on Monday 2026-10-26. Opening-range breakout: entry 1193.60 at 10:00 IST, stop 1183.60 (the
opening-range midpoint 1183.65, rounded down to the 0.10 tick), target 1203.60 (1R), flat by 15:00. The
example config: ₹1,00,000 budget, 10% cash buffer, 75% position cap, ₹80,000 max order, ₹1,000 max loss.

Entry limit 1193.60 + 0.1% = 1194.7936, rounded up: **1194.80**. Stop limit 1183.60 - 0.5% = 1177.682,
rounded down: **1177.60**. Risk 11.20 a share at the limit.

| limit | the math | shares |
| --- | --- | --- |
| max loss | ₹1,000 / 11.20 with charges (82 shares would lose ₹1,000.10) | 81 |
| position cap | 75% of ₹1,00,000 = ₹75,000 / 1194.80 | **62 (decides it)** |
| max order | ₹80,000 / 1194.80 | 66 |
| cash | ₹1,00,000 minus ₹10,000 = ₹90,000 / 1194.80 | 75 |

62 shares = ₹74,077.60. Max loss at the stop: 62 x 11.20 = ₹694.40 plus ₹73.29 charges (brokerage ₹40,
STT ₹18.35, exchange ₹4.53, SEBI ₹0.15, stamp duty ₹2.22, GST ₹8.04) = **₹767.69**. If the stop fills at
its limit 1177.60 the loss is ₹1,139.58, over the ₹1,000 max loss, which is measured at the trigger.
Reward:risk 1.00 from the plan's entry, 0.79 if the buy fills at the limit. It writes:

```json
{"role": "entry", "group": "RELIANCE-2026-10-26-orb", "variety": "regular", "exchange": "NSE",
 "tradingsymbol": "RELIANCE", "transaction_type": "BUY", "quantity": 62, "product": "MIS",
 "order_type": "LIMIT", "price": 1194.8, "validity": "DAY", "tag": "india-orb",
 "why": "opening-range breakout on RELIANCE: 62 shares, bound by your position cap (62 x 11.20 = ₹694.40 at the stop plus ₹73.29 charges = ₹767.69)",
 "plan": {"entry": 1193.6, "limit": 1194.8, "stop": 1183.6, "stop_limit": 1177.6, "target": 1203.6,
          "risk_per_share": 11.2, "charges": 73.29, "max_loss": 767.69, "setup": "orb",
          "entry_time": "10:00", "flat_by": "15:00", "date": "2026-10-26"}}
{"role": "stop", "group": "RELIANCE-2026-10-26-orb", "after": "entry", "variety": "regular",
 "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "SELL", "quantity": 62,
 "product": "MIS", "order_type": "SL", "trigger_price": 1183.6, "price": 1177.6, "validity": "DAY",
 "tag": "india-stop", "why": "protective stop for the RELIANCE entry: ..."}
```

`role`, `group`, `after`, `why` and `plan` are notes for india-risk and india-broker. They are never
sent to Kite. With the stop at 150.00 instead, one share would risk more than ₹1,000: no order. If a
fresh quote shows 1197.00, the limit becomes 1198.20 and reward:risk falls to 0.49: refused, because
the move it was waiting for has mostly happened.

## Weights mode (kept from v1)

`--weights RELIANCE=30,HDFCBANK=25` or `--targets FILE` splits a budget across a list by weight: the
cash buffer comes off the top, weights are renormalised to 100, any stock over `max_position_pct` is
pinned at the cap and its excess shared out, slices under the minimum order are dropped, and each slice
buys whole shares at its limit price (price plus `entry_limit_buffer_pct`, rounded up). Kite has no
fractional or rupee-amount orders, so it always needs prices (`--prices trader/prices.json`).
`targets.example.json` is an EXAMPLE ONLY, not advice: five large NIFTY 50 stocks with made-up weights,
not the index's own. With the real 10:00 IST prices of 2026-10-26 and the example config it buys 22
RELIANCE, 33 HDFCBANK, 13 ICICIBANK, 11 INFY and 4 TCS (₹89,986.70, ₹13.30 left idle). These are plain
MIS buys with no stop: india-risk blocks them while `require_stop` is true and counts each one against
`max_trades_per_day`. Use a separate config (`--config`) for anything else.

## Rules

- Proposals only. This skill never calls a broker tool.
- Never size money the user has not said is for this. Ask for the budget and the max loss.
- Every number comes from the config, the plan or the prices. Nothing is guessed.
- No projections: never say what a trade will make. A target is a plan, not a forecast.
- Free Yahoo prices are unofficial and can lag. Kite's price is the one that fills.

## What it never does

It never places, approves or schedules an order, never raises a limit on its own, never shrinks a stop
to fit more shares, and never sizes F&O, margin or short positions.

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
