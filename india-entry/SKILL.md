---
name: india-entry
description: >-
  Entry Timer for NSE intraday. Watches a setup bar by bar and tells you the
  moment to get in. Reads free Yahoo 1-minute or 5-minute bars (.NS), builds
  the opening range (the first 15 minutes from the 9:15 open by default) and
  calls the entry on the first completed bar that CLOSES above the range
  high, or above a level you give it (like a 20-day high from the scanner),
  with an optional volume check. Says waiting and how far, triggered with the
  time (IST) and the price in rupees, expired, or not open yet, and writes
  trader/entry.json for /india-exit. Long side only. It never places an
  order. Use when the user says "tell me when to get in", "watch RELIANCE",
  "has it broken out", "did it break the opening range", "entry signal",
  "time my entry", "check my watchlist", or after /india-scanner and
  /india-news, before /india-exit.
argument-hint: "[SYMBOL] [--setup orb|level] [--interval 5m|1m]"
---

# india-entry

The Entry Timer turns a setup into one moment: the bar that confirms it. It
reads bars, 1-minute or 5-minute, and waits for each one to finish. A price
that pokes above the high in the middle of a bar is not an entry. A bar that
CLOSES above it is. Then it says the time and the price, writes them down for
the Exit Planner, and stops. It never buys anything. Day trading loses most
people money (SEBI found about 7 in 10 individual intraday traders in the
cash segment lost money in FY23), and a clean signal does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md
lives). The commands use `python` (Windows); it is `python3` on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE                  # 15-minute opening range, 5-minute bars
python "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --write          # also save trader/entry.json
python "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --setup level --level 1195
python "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --interval 1m --min-volume-x 1.5
python "${CLAUDE_SKILL_DIR}/entry.py" watch RELIANCE --until 14:30    # one line per closed bar, stops on the trigger
python "${CLAUDE_SKILL_DIR}/entry.py" watchlist                       # every hit in trader/watchlist.json
```

Symbols are NSE trading symbols: `RELIANCE`, `M&M`, `BAJAJ-AUTO`. A typed
`RELIANCE.NS` is fine; `.BO` reads the BSE chart instead. Other flags:
`--or-minutes N` (default 15), `--json`, `--offline FILE` (a saved Yahoo chart
JSON, `.json` or `.json.gz`, or a folder of `<SYMBOL>.json`),
`--now "2026-10-26 10:00"` (India time, IST) to replay a day, `--special` for
a special session. `watch` with `--now` or `--offline` runs on a simulated
clock and replays the day bar by bar without waiting.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/watchlist.json` | the scanner's hits (`hits[].symbol`), for `watchlist` |
| `trader/config.json` | `no_entries_before` (09:30) and `no_entries_after` (14:30), IST. Optional |
| Yahoo chart API | today's bars, `RELIANCE.NS`, `range=1d&interval=5m` (or `1m`): free, unofficial, can lag |
| `trader/entry.json` | with `check --write`, or when `watch` triggers: symbol, exchange (NSE), setup, status, time, bar, price, level, opening range |

## The rule

- **Times are IST**, India Standard Time, UTC+5:30 all year (no daylight
  saving). NSE's pre-open runs 9:00 to 9:15; continuous trading 9:15 to
  15:30. NSE has no early closes.
- **Opening range (orb).** The high and low of the first 15 minutes: the
  9:15, 9:20 and 9:25 five-minute bars, or fifteen 1-minute bars. The level
  to beat is the range high.
- **Level.** A price you give, like a 20-day high from `/india-scanner`.
- **The trigger** is the first completed bar after the range that CLOSES
  above the level. A wick above is not a trigger. A close exactly at the
  level is not above it.
- **The time** is that bar's close time (the 9:55 bar closes at 10:00 AM
  IST). The price is its close, to two decimals, in rupees.
- **Only closed bars count.** A bar is closed when the clock passes its start
  plus 5 (or 1) minutes and Yahoo has caught up to that moment (it waits a
  minute at most). Yahoo's live tick, like 10:49:45, is never a bar.
- **Volume, if you want it.** `--min-volume-x 1.5` also needs the trigger
  bar's volume at 1.5 times the range bars' average. Yahoo shows no volume
  on NSE's 9:15 bar (58 of 60 days for RELIANCE: the bar carries the
  pre-open auction's price), so the average is over the range bars that do
  have volume.
- **Time limits.** No entries before 9:30 or after 14:30 (config), so there
  is time left before your flat-by (15:00 in the example config). Zerodha
  squares MIS positions off by itself at 15:12 for F&O stocks (they go into
  the closing auction at 15:15) and at 15:25 for the others, and charges
  ₹50 + 18% GST per squared-off order.
- **The closing auction.** From 15:15 F&O stocks trade in NSE's closing
  auction; Yahoo's 15:15 and 15:20 bars for them are often empty and are
  skipped.
- **What it says.** Waiting (and how far the last close is from the level),
  triggered, expired, or no data. Before 9:15: not open yet. Before 9:30: the
  range is still forming. Weekends and NSE holidays: the market is closed,
  with the next open.
- **Calendar.** NSE's 2026 holidays are built in. For a 2027 date weekdays
  count as trading days and it says: "NSE has not published its 2027 holiday
  list in this tool yet: check nseindia.com before trading". It never
  invents a holiday.
- **Special sessions.** Muhurat trading on Sun 2026-11-08 (14:20 to 15:30) is
  checked only when asked for: `--special`, or `--now` with that date. The
  config times still apply (no entries after 14:30 makes the default expire
  there), and india-risk blocks new entries in a special session unless
  `"allow_special_sessions": true`.

## Steps

1. Get the symbol from the user, or run `entry.py watchlist` after
   `/india-scanner`. Check `/india-news` for it first.
2. Run `entry.py check SYMBOL`. Tell the user the status in one line:
   waiting and how far, triggered at what time (IST) and price, or expired.
3. To wait for it, Claude can run `entry.py watch SYMBOL --until 14:30` in
   the background, or `entry.py check SYMBOL --write` on a `/loop` every 5
   minutes. `watch` wakes just after each bar closes, prints one line per
   bar, and exits 0 on the trigger (it writes `trader/entry.json`) or 1 when
   the setup expires. Keep the computer awake while it watches: a sleeping
   laptop pauses it, and it catches up, late, when it wakes.
4. When it triggers, say the time and the price, and that nothing was placed.
   If the trigger is more than a bar old, say that too: a late entry is a
   different trade.
5. Hand off to `/india-exit` to set the stop and the target BEFORE anything
   is bought.

## Example: the reel

RELIANCE, Monday 2026-10-26, 5-minute bars (real Yahoo data). The opening
range is ₹1,175.30 to ₹1,192 (the 9:15 bar's high, the 9:25 bar's low). At
9:58 the 9:55 bar is still open, so it waits:

```
WAITING  last close ₹1,186.40 (9:50 bar), ₹5.60 under the range high ₹1,192 (-0.47%).
The 9:55 bar is still forming (₹1,193.60 so far, closes 10:00 AM IST); it does not count until it closes.
```

At 10:00 the bar has closed:

```
RELIANCE  Mon 2026-10-26 10:00 IST  opening-range breakout (15-minute range) on 5-minute bars (Yahoo, free, can lag)
Opening range 9:15-9:30: high ₹1,192, low ₹1,175.30 (3 of 3 bars).
TRIGGERED 10:00 AM IST at ₹1,193.60: the 9:55 bar (bar 8) closed above the range high ₹1,192 (+0.13%).
This is the moment to get in, if you take the trade. Nothing was placed.
Next: set the stop and target BEFORE you buy: /india-exit (exit.py plan --from trader/entry.json).
```

The 9:55 bar traded 3,22,649 shares, 1.51x the range's average (the 9:20 and
9:25 bars; the 9:15 bar shows none). With `--min-volume-x 2` no close above
the range had the volume, and the setup expires at 14:30.

(The "so far" price in a replay is the bar's final close: a saved file
cannot know what the bar showed at 9:58.)

## Rules

- Bar by bar, on free Yahoo 1-minute or 5-minute bars. They are unofficial
  and can lag. Say so when a minute matters.
- A signal is information, not an order, and not a promise. Nothing here
  promises winning trades or money.
- Long side only: this pack plans longs only, and short selling is off. If
  the user wants a short, say that.
- One trigger per setup per day: the first close above. Later closes do not
  reset it.
- Never call an entry early because a bar looks like it will close above.

## What it never does

It never places an order, never sizes a position, never sets the stop (that
is `/india-exit`), never moves the level after the fact, and never counts a
bar that has not closed. It never runs `/india-broker` from a background
watch: nobody is there to say yes.

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
