---
name: india-timemachine
description: >-
  Time Machine for NSE intraday. Replays every possible outcome of a trade
  before you risk a rupee: 10,000 simulated sessions plus every past time the
  setup fired. Takes the plan from india-exit (entry, stop, target, flat by),
  rebuilds the rest of the day 10,000 times from the stock's own 5-minute
  bars (Yahoo .NS) over the last 60 days, and re-runs the same rule on every
  past session. Prints the odds of target first, stop first, or neither by
  the flat-by time (15:00 IST), where the price sits then, the average R, the
  history, and one honest verdict, then hands off to /india-budget. Use when
  the user says "run the time machine", "replay this trade", "what are the
  odds", "simulate this trade", "monte carlo", "backtest this setup", "how
  often does this hit the target", "is this a coin flip", or after
  /india-exit writes a plan, before /india-budget.
argument-hint: "[path to plan.json, or SYMBOL]"
---

# india-timemachine

The Time Machine sits between the plan and the money. india-exit has set the
entry, the stop, the target and the flat-by time. Before anything is sized or
sent, this skill asks one question: if today plays out like the stock's own
last 60 days, how often does this plan hit the target first, the stop first,
or neither by the time you would be out anyway? It answers with 10,000
replays and with the real record of the same rule on every past session. It
is a simulation built from the stock's own recent bars, not a prediction.
Intraday trading loses most people money (SEBI's study of FY23: about 7 in 10
individual intraday traders in the cash segment made a loss), and a
good-looking replay does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
The commands use `python` (Windows); use python3 on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/timemachine.py" run                    # trader/plan.json, 10,000 replays, seed 19
python "${CLAUDE_SKILL_DIR}/timemachine.py" run --write            # also save trader/timemachine.json
python "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 14:30      # end each replay earlier than the flat-by
python "${CLAUDE_SKILL_DIR}/timemachine.py" run --json
python "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol RELIANCE --date 2026-10-26 \
    --entry 1193.60 --stop 1183.60 --target 1203.60 --entry-time "10:00 AM"
python "${CLAUDE_SKILL_DIR}/timemachine.py" chart --from           # ASCII fan chart of the saved run
```

Other flags: `--replays N`, `--seed N` (default 19), `--window N` (default 2
bars), `--offline FILE` (a saved Yahoo chart JSON, `.json` or `.json.gz`),
`--now "2026-10-26 10:00"` (India time, IST) to pin the clock.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the plan from india-exit: symbol, exchange, date, entry, entry_time / entry_bar, stop, stop_rule, target, target_r, opening range, flat_by |
| Yahoo chart API | one request: 60 days of 5-minute bars for `<SYMBOL>.NS` (`range=60d&interval=5m`), free and unofficial, can lag |
| `trader/timemachine.json` | with `--write`: `{plan, mc, history}` plus the verdict. `mc` holds the odds, percentiles, the fan-chart bands, 160 sample paths and a 24-bin histogram of the price at the end |

## How it works

1. **The pool.** The NSE session is 09:15 to 15:30 IST: 75 five-minute
   bars, 09:15 to 15:25 (India has no daylight saving, so the clock never
   shifts). It keeps only COMPLETE sessions before the plan's date: every bar
   from 09:15 through the last bar a replay can draw (the end bar plus the
   window). Yahoo leaves most bars from 15:15 on empty for stocks with F&O
   contracts, because those go into the closing auction (CAS) at 15:15, so a
   replay to 15:00 needs the bars to 15:05 only. Sessions with a gap in
   Yahoo's bars, special sessions (Muhurat trading) and today are left out.
   Every bar becomes four ratios to the bar before it: open, high, low and
   close over the previous close.
2. **One replay.** Start at the entry price. For each bar left until the
   flat-by time, pick a past bar from the same time of day (plus or minus 2
   bars) at random, and scale it to the current price. Bar by bar, it checks
   the stop first, then the target. A bar that touches both counts as the
   stop.
3. **10,000 replays** give four odds. Target first. Stop first. Neither,
   which means the trade was still open at the end: the plan's `flat_by`
   (15:00 when it has none), or `--until`. That is when you would square off
   anyway, before Zerodha does it for you at 15:12 (F&O stocks) or 15:25
   (the others) at ₹50 + GST an order. Green, which means target first or
   above the entry at the end.
   R is +1 at a 1R target, -1 at the stop, and otherwise the distance from
   the entry at the end divided by the risk. A gap through the stop still
   counts as -1R here. A real fill can be worse. R is before Zerodha's
   charges (brokerage, STT, exchange fees, stamp duty, GST); india-budget
   adds them to the max loss.
4. **The history.** The plan's own rule (the same opening range, stop rule
   and target R, stops and targets rounded to the NSE tick) runs again on
   every one of those past sessions. That is what really happened, not a
   simulation.
5. **The verdict** is one line from the numbers. It looks only at the
   replays that hit one side, and asks how often the target came first. A
   bigger target needs to win less often to break even: 1 in (1 + R). That
   is 50 % at 1R, 33 % at 2R, 25 % at 3R. Less than 5 points from break-even:
   "Close to a coin flip" at 1R, "Close to break-even" at any other R. 5 to
   15 points: "Leans toward the target" or "Leans toward the stop". 15 or
   more: "Strongly toward" one of them. So a 2R plan with target first
   29.2 % and stop first 69.9 % is close to break-even (it needs 33 % of
   the 99.1 % that hit a side, and got 29 %), not a sure loser.
6. **Below the verdict**, when it applies: a line when more than 40 % of the
   replays hit neither (the price at the end decides those), and the average
   R when its sign goes against the lean. Those can only point the other way
   because of the replays that hit neither.

**The seed.** The draws come from Python's `random.Random(seed)`, seed 19 by
default. Same plan, same data, same seed: the same numbers, on any machine.
Say the seed with every result so the user can repeat it.

**5-minute bars only.** Yahoo keeps 60 days of 5-min bars but only about 7
days of 1-min bars, too few sessions for a fair pool. It refuses, in one
plain line, when there are fewer than 20 complete sessions (a replay into
the closing auction gets the reason and the `--until` that works), when the
entry bar is not in the data (wrong date, an NSE holiday, not closed yet, a
future day), or when the plan does not make sense (a stop above a buy's
entry, an entry off the 5-minute grid). A live in-progress bar is never used.

## Steps

1. Make sure `trader/plan.json` is today's plan from `/india-exit`. If there
   is none, run that first, or pass the plan as flags.
2. Run `timemachine.py run --write`. It ends each replay at the plan's
   flat-by time, so "neither" means still open at the time you would get out.
3. Tell the user, in this order: the four odds, the median and the 5 to 95
   range of the price at the end, the average R, the history line, then the
   verdict line and any line under it exactly as printed. Say the seed.
4. If the verdict is a coin flip or close to break-even, say so plainly. Do
   not dress it up. If the history and the replays disagree, say that too.
5. Say once that this is built from the last 60 days and is not a
   prediction. Results, news, circuit limits and halts can do things those
   60 days never did. Check `/india-news` for the stock before going on.
6. If the user still wants the trade, hand off to `/india-budget` to size it.

## Example: the reel

RELIANCE, Monday 2026-10-26. The 15-minute opening range is 1175.30 to
1192.00. The 09:55 bar closes above it: entry 1193.60 at 10:00 AM (bar 8),
stop 1183.60 (the range midpoint 1183.65, rounded down to the 0.10 tick),
target 1203.60 (1R, risk 10.00 a share), flat by 15:00. The data is Yahoo's
as it stood at 10:00 that morning: 54 complete sessions, 2026-08-05 to
2026-10-23 (2026-10-15 has a gap and is left out).

```
10,000 replays of the rest of the session, 10:00 AM to 3:00 PM IST (60 five-minute bars), seed 19.
  target first   21.8 %
  stop first     30.0 %
  neither        48.2 %   still open at 3:00 PM
  green          44.7 %   target first, or above the entry at 3:00 PM
  Price at 3:00 PM: median 1192.42, 90 % of replays between 1177.95 and 1206.86.
  Average R: -0.095
History: the same rule on each of those 54 real sessions fired 24 times:
11 target, 10 stop, 3 neither. Average R +0.009.
Leans toward the stop: target first 21.8 % vs stop first 30.0 %.
48.2 % of the replays hit neither by 3:00 PM; the price at 3:00 PM decides those.
```

At 1R the target has to come first in half of the replays that hit one side.
It did in 42 %. The replays lean toward the stop, while the real history of
the same rule came out about even (+0.009R over 24 trades, before charges).
The two disagree, and that is the honest answer for this setup: it is what
the user should hear before `/india-budget` sizes anything.

## Rules

- Every number comes from the run. Never round a coin flip up into an edge.
- The verdict line and the lines under it are printed by the tool. Repeat
  them, do not rewrite them. Never judge a 2R or 3R plan by target first vs
  stop first alone: the verdict already weighs the target's R.
- Simulated odds are not predictions and not promises. Say it once, plainly.
- Free Yahoo data is unofficial and can lag. Bars are 5-minute bars, checked
  bar by bar, never a live feed.
- Always say the seed, so any run can be repeated.
- One plan per run. A changed entry, stop, target or date is a new run.

## What it never does

It never places an order, never sizes a position, never changes the plan,
and never calls a result a sure thing. It never prints a P&L or a return.
It never uses a bar that has not closed yet, and never mixes today's bars
into the pool.

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
