---
name: india-scanner
description: >-
  Chart Scanner for NSE stocks. Scans thousands of Indian charts for setups:
  opening-range breakouts, flags, oversold bounces. Builds the list of NSE
  EQ-series stocks (the ones you can trade intraday) from NSE's EQUITY_L.csv,
  flags 2% and 5% price-band stocks from sec_list.csv, runs a pre-market
  scan of daily closes (20-day closing-high breakout, bull flag, reclaimed
  the 20-day average, oversold, oversold bounce) and an intraday
  opening-range breakout scan on 5-minute bars from the 9:15 IST open, then
  writes trader/watchlist.json for india-news and india-entry. Free Yahoo
  data (.NS), bar by bar, prices in rupees. Use when the user says "scan the
  market", "scan NSE", "find me setups", "what is breaking out", "opening
  range breakouts", "scan for bull flags", "oversold stocks", "pre-market
  scan", "build my watchlist", "what should I look at today", or at the start
  of a trading day before /india-news.
argument-hint: "[universe | daily | orb] [--at HH:MM]"
---

# india-scanner

The Chart Scanner is the first step of a day trade. It reads thousands of
free charts and lists the ones that match a rule. That is all it does. A scan
is a list of charts that match a rule, not a list of stocks to buy, and a
setup on a chart is not a prediction. Most people who day trade lose money
(SEBI found about 7 in 10 individual intraday traders in the cash segment
lost money in FY23); the scanner does not change that. It only saves you from
flipping through 2,357 charts by hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md
lives). The commands use `python` (Windows); it is `python3` on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/scan.py" universe                          # -> trader/universe.txt
python "${CLAUDE_SKILL_DIR}/scan.py" daily                             # before the open -> trader/watchlist.json
python "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,TCS,M&M  # or --watchlist FILE, --limit 200
python "${CLAUDE_SKILL_DIR}/scan.py" orb                               # after 9:35 AM IST -> trader/watchlist.json
python "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15                    # cut the clock at 10:15 AM IST
python "${CLAUDE_SKILL_DIR}/scan.py" orb --watchlist trader/watchlist.json --json
```

Flags: `--min-price 50` (rupees) and `--min-volume` (shares: daily
5,00,000, orb 0), `--or-minutes 15`, `--max-confirm 200` (orb stage 2),
`--now "2026-10-26 10:00"` (India time, IST) to pin the clock,
`--offline PATH` to read saved files, `--special` for a special session,
`--no-write`, `--show N`, `--json`. Exit code 0 when the scan found
something, 1 when it found nothing or could not run (the reason is one plain
line).

The floors: ₹50 keeps out the penny stocks whose tick (1 paisa) is a big part
of the price, and 5,00,000 shares on the last session keeps the daily scan to
names that trade in size. Lower them on purpose, never by accident.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Name\|band` a line, read by `daily` and `orb` |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. india-news and india-entry read `hits[].symbol`. A hit on a 2% or 5% band also carries `"band"` |
| `trader/config.json` | read by `orb`: `no_entries_after` (default 14:30) |

## The universe

`universe` downloads `EQUITY_L.csv` (every listed equity) and `sec_list.csv`
(the price bands) from nsearchives.nseindia.com, with a browser User-Agent
because NSE turns tools away otherwise. When NSE refuses, it primes NSE's
cookies once and tries once more, then says NSE refused and to try again in a
minute. It keeps a row when, in this order:

1. Its SERIES is `EQ`. `BE` and `BZ` are trade-for-trade (every trade ends
   in delivery, so no intraday); any other series is out too.
2. The symbol is a plain NSE symbol: capitals, digits, `&` and `-`, at most
   20 characters (`RELIANCE`, `M&M`, `BAJAJ-AUTO`, `3MINDIA`).

On the 2026-10-31 file this keeps 2,357 of 2,620 rows (230 BE and 33 BZ
out). The bands of those 2,357: 1,884 on 20%, 171 on 10%, 83 on 5%, 218 with
no band (the F&O stocks, which have dynamic limits instead) and 1 not in
sec_list.csv. A stock on a 2% or 5% band freezes at the band and can stay
there, so its hits say "tight band". Without sec_list.csv the universe is the
same, just without the flags.

## The daily scan (before the open)

Yahoo's spark endpoint returns closes for 20 symbols a request (`.NS`
symbols work), so 2,357 symbols take 118 requests. Liquidity comes from the
spark meta: last price at least `--min-price` and last session's volume at
least `--min-volume`. Run it before the open or after the close: during the
session the volume is the day's so far, and today's daily bar is left out
until 15:30.

Spark thins its line: it sometimes leaves a day out (on 2026-10-31, 9 of the
NIFTY 100 had lost a day of their 64, ITC two: Aug 3 and Oct 27). A setup
counted over a missing day would be wrong, so a liquid chart with a missing
NSE trading day is fetched again as a v8 chart (one request each). Offline,
such a chart is skipped and counted.

The setups are computed from daily closes only (spark has no highs or lows):

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

Hits are listed most traded first (price times volume).

## The opening-range breakout scan (during the session)

The opening range is the first `--or-minutes` (15) of the session: the 9:15,
9:20 and 9:25 five-minute bars, so the range is set at 9:30 and the first bar
after it closes at 9:35. A hit is the first completed 5-minute bar to CLOSE
above the range high, at that bar's close time.

1. Stage 1, every symbol: spark closes of today's 5-minute bars, 20 a
   request. A chart goes on if a completed bar after the range closed at or
   above the highest close of the range, or if a bar after the range is
   missing from the spark answer. Spark leaves bars out (85 of the 7,368
   NIFTY 100 bars of Oct 26), and the bar it leaves out can be the very bar
   that broke out: it dropped EICHERMOT's 9:50 bar, which closed ₹7,028 over
   a ₹7,008 high. With that rule stage 1 is still a necessary condition, so
   no breakout is lost; requiring every spark bar instead would have lost 5
   of the 63 hits at 10:00 and 45 of 91 by 14:30.
2. Stage 2, each candidate: the v8 chart with real highs and lows, one
   request each. Range high = the highest high of the range bars, range low =
   the lowest low. On a strong morning most charts are candidates, so stage 2
   checks at most `--max-confirm` (200) of them, the most traded first (price
   times volume so far), and prints PARTIAL with the count it skipped.
   `--max-confirm 0` checks them all.

A v8 chart is usable only when every 5-minute bar from 9:15 to the cutoff is
there; a missing bar means a halt or a thin stock (on thin small caps many v8
charts fail this). The check stops at 15:15: F&O stocks go into the closing
auction then, and Yahoo's 15:15 and 15:20 bars for them are often empty.
Null closes are dropped, a bar off the 5-minute grid (the live tick) is
dropped, and a bar counts only once it is complete (start + 5 minutes).
Before 9:35 AM, on a weekend or NSE holiday, after the close, or after
`no_entries_after` (an expired setup) it prints one plain line and writes
nothing. NSE has no early closes.

Timing, live on Sat 2026-10-31 replaying Fri Oct 30 at 10:00: 60 symbols
took 18 seconds (3 spark requests, 23 v8 charts). For a fast answer, scan the
daily watchlist or a list of names you trade (`--watchlist`, `--symbols`).

## Data and limits

- Yahoo's chart and spark endpoints are free, unofficial and can lag. Bars are
  5-minute bars, bar by bar, never single trades. query2 first, query1 on a
  429 or 5xx, one retry after a 5-second wait on a 429.
- Times are IST (UTC+5:30, no daylight saving). Prices are rupees.
- Partial results are said as partial: "Fetched 2,300 of 2,357 charts (57
  failed or had no data)". Never round a partial scan up to "the whole market".
- NSE holidays for 2026 (NSE circular CMTR71775). NSE has not published 2027
  yet: on a 2027 date weekdays count as trading days and the scan says "NSE
  has not published its 2027 holiday list in this tool yet: check
  nseindia.com before trading". It never invents a holiday.
- Special sessions (Muhurat trading, Sun 2026-11-08, 14:20 to 15:30) are
  scanned only when asked for: `--special`, or `--now` with that date.
  india-risk blocks new entries in them unless `"allow_special_sessions":
  true`.

## Steps

1. Once a week or so: `scan.py universe`.
2. Before the open: `scan.py daily`. Tell the user the counts (fetched,
   liquid, scanned, hits) and the top few by setup.
3. After 9:35 AM IST (10:00 is the reel's): `scan.py orb`. Tell the user how
   many usable charts broke their range and when, and that a later hit is
   closer to the price now. Mention a tight band when a hit has one.
4. The user picks one chart. Hand it on: `/india-news SYMBOL` (what is moving
   it), then `/india-entry SYMBOL` (the entry bar by bar). Never pick for them.

## Example: the reel

Monday 2026-10-26, scan at 10:00 AM IST over the NIFTY 100
(`tests/fixtures/india-scanner/nifty100-5m-2026-10-26-1000.json.gz`, real
Yahoo charts cut to the bars that had started by 10:00):

```
python scan.py orb --offline nifty100-5m-2026-10-26-1000.json.gz --now "2026-10-26 10:00"
Opening-range breakout scan, Mon 2026-10-26 10:00 AM IST: 15-minute range (9:15 to 9:30), 5-minute bars, Yahoo.
Fetched 100 of 100 charts; 99 usable (1 under the price / volume floor); 89 candidates, 89 confirmed with real highs and lows.
63 of 99 charts closed above their opening-range high by 10:00 AM IST:
   9:35 AM  BOSCHLTD        ₹44,100  range 43,685.00 to 44,000.00  +0.23% over
  ...
  10:00 AM  RELIANCE      ₹1,193.60  range 1,175.30 to 1,192.00  +0.13% over
```

A strong morning: 63 of 99. IDEA (₹11.39) is the one under the ₹50 floor.
RELIANCE's range is the 9:15 to 9:25 bars, high 1,192.00 (the 9:15 bar), low
1,175.30 (the 9:25 bar); the 9:55 bar closed at 1,193.60, 0.13 % over the
high, at 10:00 AM. The user picks RELIANCE and Claude hands it to
`/india-news RELIANCE`, then `/india-entry RELIANCE`.

The pre-market scan that morning (closes through Fri Oct 23, the same 100)
tags INFY, BSE and HCLTECH a bull flag, ONGC a reclaim of the 20-day average,
and 15 names oversold, with the default floors (19 hits; 6 charts skipped
offline for a day spark left out).

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data (IST).
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a gain figure.
- A hit is stale once the price has moved on. Re-run before acting on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data live to the second, and never promises that a setup works.

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
