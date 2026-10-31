---
name: india-exit
description: >-
  Exit Planner for NSE intraday (Zerodha MIS). Sets your stop and your target
  before you enter, so you know when to get out. Reads the entry from
  india-entry (or a price you give), puts the stop at the opening-range
  midpoint by default (or the range low, your price, a percent, or a multiple
  of the ATR), rounds it down to the NSE tick, and sets the target at 2R (or
  1R, or your price). Refuses a stop at or above the entry, a target at or
  below it, or a stop too tight to survive the spread, and says why. Flat by
  15:00 IST, before Zerodha's auto square-off and the closing auction. Writes
  trader/plan.json for the Time Machine, the Budget Manager and Price Alerts.
  Long side only. Use when the user says "where's my stop", "set my stop and
  target", "plan the exit", "when do I get out", "stop loss",
  "what's my R", "risk reward", or right after /india-entry
  triggers, before /india-timemachine.
argument-hint: "[--stop or-mid|or-low|price:X|pct:X|atr:K] [--target 2R|1R|price:X]"
---

# india-exit

The Exit Planner decides how a trade ends before it starts. The stop is where
the setup has failed. The target is where you take what you came for. Both
are set BEFORE the entry, written down and handed on, so nobody has to decide
anything in the middle of a fast move. A stop is a plan, not a guarantee: a
gap or a fast market can fill past it. Intraday trading loses most people
money: SEBI's study of FY23 found about 7 in 10 (71%) individual intraday
traders in the cash segment made a loss. A neat plan does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
The commands use `python` (Windows); use python3 on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/exit.py" plan                          # trader/entry.json: midpoint stop, 2R target
python "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 1R --write
python "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5           # needs the session's bars (Yahoo or --offline)
python "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol RELIANCE --entry 1193.60 --or-high 1192 --or-low 1175.30
python "${CLAUDE_SKILL_DIR}/exit.py" show                          # the current trader/plan.json
```

Other flags: `--flat-by 15:00`, `--json`, `--now "2026-10-26 10:00"` (India
time, IST), `--entry PRICE` with a waiting entry.json to plan ahead, `--setup
level`, `--offline FILE` (a saved Yahoo chart JSON for an ATR stop). A symbol
is the NSE trading symbol (RELIANCE, M&M, BAJAJ-AUTO); `RELIANCE.NS` works
too, and `.BO` means BSE.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | the trigger from india-entry: symbol, exchange, entry price, time, bar, opening range |
| `trader/config.json` | `flat_by` (15:00), `min_reward_risk` (1.0: a note here, india-risk enforces it), `tick_size` overrides like `{"RELIANCE": 0.1}` |
| Yahoo chart API | only for an `atr:K` stop: the session's `.NS` bars up to the entry bar, free and unofficial |
| `trader/plan.json` | with `--write`: entry, stop and its rule, target and its R, risk a share, reward:risk, flat by, `"exchange": "NSE"` and `"tick"` (the tick the stop was rounded to). india-timemachine, india-budget and india-alerts read it |

## Stops and targets

| stop | where it goes |
| --- | --- |
| `or-mid` | the opening-range midpoint (the default for an opening-range breakout) |
| `or-low` | the opening-range low: wider, so fewer shares for the same max loss |
| `price:X` | your price |
| `pct:X` | X percent under the entry |
| `atr:K` | K times the 14-bar ATR under the entry, on the session's bars up to the entry bar. With fewer than 14 bars by then it averages all of them and says so |

A level setup has no opening range to lean on, so it needs a stop you name.

- **Rounding to the tick.** NSE prices move in ticks: ₹0.01 under ₹250,
  ₹0.05 up to ₹1,000, ₹0.10 up to ₹5,000, ₹0.50 up to ₹10,000, ₹1 up to
  ₹20,000, ₹5 above (NSE/CMTR/67133). The tick comes from the price being
  rounded, and the sums are done in whole paise. The stop rounds DOWN to the
  tick, away from the entry. Risk = entry minus stop. Target = entry + R x
  risk, rounded DOWN to the tick (toward the entry, never past what you
  planned). `2R` by default; `1R`, `1.5R` or `price:X` also work. The tick
  schedule is NSE's, reviewed monthly: check Kite's instrument list if an
  order is rejected for its price, and put the right tick under `tick_size`
  in the config.
- **Refused, with the reason.** A stop at or above the entry. A target at or
  below it. A risk under 0.05% of the price, or under 3 ticks: too tight to
  survive the spread, normal wiggle would stop you out. An entry.json that
  has not triggered (unless you give `--entry`). A short: this pack plans
  longs only.
- **Flat by.** 15:00 IST by default (or `flat_by` in the config). Out by
  then, win or lose, no overnight hold: MIS is an intraday product. Why
  15:00 and not the 15:30 close: since 3 Aug 2026 stocks with F&O contracts
  stop continuous trading at 15:15 and go into the closing auction session
  (CAS, 15:15 to 15:35), where stop-loss orders are not allowed. Zerodha
  squares off MIS positions itself at 15:12 for those stocks and at 15:25 for
  the others, charges ₹50 + GST for every order it squares off, and blocks
  fresh MIS orders after that time. A flat-by at or after 15:12 is allowed
  with a note; one at or after the close is refused, and so is an entry at or
  after the flat-by time. On a special session (Muhurat trading, Sunday
  2026-11-08, 14:20 to 15:30) the flat-by must fall inside it, and Zerodha
  announces its own square-off time.

## Steps

1. Run it right after `/india-entry` triggers. Before that, `--entry PRICE`
   plans ahead at the level you expect.
2. Use the default stop for an opening-range breakout unless the user names
   another. A level setup needs one: ask.
3. Run `exit.py plan --write`. Read back the entry, the stop and its rule,
   the target and its rule, the risk a share, reward:risk and flat by, and any
   Note line as printed.
4. If it refuses, say the reason and the fix (a wider stop, a farther
   target). Never loosen anything on your own.
5. Say once that the stop is a plan, not a guarantee.
6. Hand off to `/india-timemachine` to replay the plan, then `/india-budget`
   to size it to the max loss per trade. Offer `/india-alerts` for the stop,
   the target and the flat-by time. Claude can keep `entry.py watch` running
   in the background or on a `/loop` until a setup triggers, then come here.

## Example: the reel

RELIANCE, Monday 2026-10-26. The 15-minute opening range (09:15 to 09:30) is
1175.30 to 1192.00. The 09:55 bar closes at 10:00 AM at 1193.60, the first
close above it: the entry (bar 8). `exit.py plan --stop or-mid --target 1R
--write`:

```
Exit plan: RELIANCE (NSE) long, Mon 2026-10-26, opening-range breakout on 5-minute bars
  Entry         ₹1,193.60 at 10:00 AM IST (bar 8)
  Stop          ₹1,183.60  opening-range midpoint: (₹1,192 + ₹1,175.30) / 2 = ₹1,183.65, rounded down to the ₹0.10 tick
  Target        ₹1,203.60  1R (one times the risk)
  Risk a share  ₹10 (0.84% of the entry)
  Reward:risk   1.0
  Flat by       15:00 IST, win or lose: no overnight hold (MIS)
```

The default 2R target would be 1193.60 + 2 x 10.00 = ₹1,213.60.
india-budget then sizes it to your max loss per trade, Zerodha's charges
included.

## Rules

- The stop and the target are set before the entry, never after.
- Never move a stop further from the entry once the trade is on.
- Plain numbers only: entry, stop, target, risk a share, reward:risk. It
  never prints a P&L or a return.
- Nothing here promises winning trades or money. A 1R target is not a
  prediction that the price gets there.
- Long side only. This pack plans longs only: no short selling.

## What it never does

It never places an order, never sizes a position, never widens a stop to
make a plan pass, and never calls a stop a guarantee.

## Safety

- Not financial advice. This is software that reads public data and does arithmetic.
- SEBI's study of FY23: about 7 in 10 (71%) individual intraday traders in the cash segment made a loss.
- Paper trading by default (`"paper": true`). Zerodha has no paper account, so paper mode never calls a
  Kite tool that places, modifies or cancels anything. Going live is the user's own decision, in their own config.
- No trade goes through until you say yes. Nothing in this pack places an order except india-broker, and
  only when all three hold: india-risk passed that exact order group today, the user typed an explicit yes
  for that group (the entry and its protective stop) in the chat, and the user clicked Allow once on
  Claude's own card because the Kite order tools are set to "Needs approval". Never set an order tool to
  "Always allow".
- No F&O, no short selling, no margin unless the config allows it and the user confirms (F&O is never on
  in this pack).
- Kite API keys live in environment variables, never in the chat.
