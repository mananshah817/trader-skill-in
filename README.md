# The India Trader skill

11 Claude skills for intraday trading on NSE: scan thousands of charts, track NSE filings and news, time every entry and exit, and replay every outcome before you risk a rupee.

This is a port of Jake Schincariol's
[Day Trader skill](https://github.com/Jakeschincariol/day-trader-skill) (MIT)
from the US market to the Indian cash market: NSE equities, intraday (MIS),
long side only, broker Zerodha Kite. Times are IST, money is in rupees,
prices round to NSE's tick, and every rupee of Zerodha's charges is counted
in your max loss.

Free, no signup, no API key for the data. The tools are plain Python that
read free public data: Yahoo Finance charts for `.NS` symbols, NSE's equity
list, corporate announcements, results filings, bulk and block deals and
SEBI insider-trading disclosures.

No trade goes through until you say yes. Nothing in this pack can place an
order on its own. Paper trading is the default.

## Install

In Claude Code, as a plugin:

```
/plugin marketplace add mananshah817/trader-skill-in
/plugin install india-trader-skill@india-trader-skill
```

Installed as a plugin the skills show up as `/india-trader-skill:india-scanner`
and so on. Copy the folders instead if you want plain `/india-scanner`:

```powershell
git clone https://github.com/mananshah817/trader-skill-in.git
Copy-Item -Recurse trader-skill-in\india-* $HOME\.claude\skills\
```

macOS / Linux: `cp -r trader-skill-in/india-* ~/.claude/skills/`.

Project-local instead of global: copy the same folders into your project's
`.claude\skills\`. The tools need Python 3.8 or newer (`python` on Windows,
`python3` on macOS and Linux). Nothing to pip install.

## The eleven

| command | job | what it does |
| --- | --- | --- |
| `/india-scanner` | Chart Scanner | Scans the NSE equity list for setups: opening-range breakouts, 20-day breakouts, bull flags, oversold bounces. |
| `/india-news` | News Monitor | Yahoo headlines and NSE corporate announcements for every stock you watch, tagged (results, dividend, bonus, board meeting...), plus the results date if it falls inside your trade. |
| `/india-entry` | Entry Timer | Watches a setup bar by bar and tells you the moment to get in. |
| `/india-exit` | Exit Planner | Sets your stop and your target before you enter, on the NSE tick. |
| `/india-timemachine` | Time Machine | Replays the rest of the session 10,000 times from the stock's own bars, plus every past day the setup fired. |
| `/india-risk` | Risk Manager | Checks every order against your limits, NSE hours and holidays, and stops anything over them. |
| `/india-budget` | Budget Manager | Sizes every position to your budget and your max loss, Zerodha's charges included. |
| `/india-broker` | Order Executor | Builds Kite orders and sends them through the Kite MCP server, paper by default, never without your yes. |
| `/india-alerts` | Price Alerts | Rings on the stop, the target, the opening bell and your flat-by time. |
| `/india-analyst` | Results Analyst | Reads quarterly results filed on NSE and sums up what changed, year on year. |
| `/india-insider` | Insider Tracker | Bulk and block deals and SEBI insider-trading (PIT) disclosures by promoters and directors. |

## How to use it

Everything lands in a `trader\` folder in your project, and each skill reads
what the last one wrote:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker   (one trade)
alerts: stop, target, flat by 15:00                                           (while it is open)
news / analyst / insider                                                      (on a schedule)
```

The worked example, real data: RELIANCE, Monday 26 October 2026.

1. **`/india-scanner`** at 10:00 AM IST: of the NIFTY 100, 63 charts had
   closed above their 15-minute opening range. RELIANCE is one: range
   1175.30 to 1192.00, the 9:55 candle closed at 1193.60, 0.13 % over the top.
2. **`/india-news`**: the morning's announcements and headlines, in IST, and
   whether a results board meeting falls inside the trade.
3. **`/india-entry`** at 9:58 said waiting (the 9:55 bar was still forming).
   At 10:00 it said triggered: the first 5-minute close above the range, 1193.60.
4. **`/india-exit`** set the plan before the entry: stop at the range's
   midpoint, 1183.65, rounded down to the 0.10 tick, so 1183.60 and 10.00 a
   share at risk; target one times that risk, 1203.60; flat by 3:00 PM either way.
5. **`/india-timemachine`** replayed the rest of the day 10,000 times from
   RELIANCE's own 5-minute candles over the previous 54 complete sessions
   (seed 19): target first 21.8 %, stop first 30.0 %, neither by 3:00 PM
   48.2 %. On those 54 real sessions the same rule fired 24 times: 11 target,
   10 stop, 3 neither. Its verdict: leans toward the stop. That is the point
   of looking before you risk a rupee.
6. **`/india-budget`** with a ₹1,00,000 account and a ₹1,000 max loss: the
   entry is a limit at 1194.80 (plan plus a 0.1 % buffer), and the 75 %
   position cap binds at 62 shares (₹74,077.60). At the stop the loss is
   62 x 11.20 = ₹694.40 plus ₹73.29 of Zerodha charges = ₹767.69. It writes
   one trade: a BUY LIMIT entry and its SL stop-loss order.
7. **`/india-risk`** checks it against your limits: max loss, daily loss,
   trades per day, reward to risk, the entry window, NSE hours and holidays,
   position size and cash. PASS or BLOCK, with the reason.
8. **`/india-broker`** shows you the ticket in plain words. You type yes for
   that trade. In paper mode the fill is recorded locally; live, Claude calls
   your self-hosted Kite MCP server and Claude's own "Allow once / Deny" card
   asks you one more time. The stop order goes in only after the entry fills.
9. **`/india-alerts`** rings on the stop, on the target (the target is
   watched, not placed: Zerodha has no bracket orders) and at 3:00 PM to
   flatten whatever is still open.

## The tools

All standard-library Python. The data tools read public endpoints and are
polite about it.

```powershell
python india-scanner\scan.py universe                              # NSE EQ series, ~2,350 stocks
python india-scanner\scan.py daily                                 # setups from daily closes
python india-scanner\scan.py orb --at 10:00                        # opening-range breakouts so far
python india-news\news.py --tickers RELIANCE INFY --hours 12       # headlines, NSE announcements, moves
python india-entry\entry.py check RELIANCE --setup orb             # waiting / triggered / expired
python india-entry\entry.py watch RELIANCE --every 30              # bar by bar until it triggers
python india-exit\exit.py plan --stop or-mid --target 1R --write   # -> trader\plan.json
python india-timemachine\timemachine.py run --write                # 10,000 replays + the history
python india-budget\size.py --write                                # -> trader\orders.json
python india-risk\check.py                                         # PASS / BLOCK per order
python india-broker\ticket.py show                                 # the tickets, READY or not
python india-alerts\alerts.py add --from-plan                      # stop, target, flat by 15:00
python india-alerts\alerts.py check                                # rings what hit, once
python india-analyst\results.py RELIANCE                           # quarters, growth, EPS
python india-insider\insider.py deals --min-crore 200              # big bulk and block deals
python india-insider\insider.py insider --category promoter --read # promoter trades, in detail
```

```powershell
python -m unittest discover -s tests -v
```

The tests run offline, on fixtures cut from real data fetched on
31 October 2026: Yahoo's 5-minute bars for the NIFTY 100, RELIANCE's 60
days to 26 October, NSE's equity list, announcements, results XBRL, bulk
and block deals and PIT filings. They reproduce every number in the example
above. The shared rules every skill follows are in [docs/SPEC.md](docs/SPEC.md).

## Zerodha in one page

- **No bracket orders.** Zerodha removed them in 2020, and GTT does not work
  with MIS. So a trade here is a BUY LIMIT entry plus an SL stop-loss order
  placed after the fill. The target is watched by `/india-alerts`; taking it
  is a flatten ticket (cancel the stop, then sell).
- **Limits, not market orders.** Kite wants market protection on market
  orders and the Kite MCP tool cannot send it, so every order is a limit.
- **Flat by 15:00.** Zerodha squares off MIS itself at 15:12 for F&O stocks
  and 15:25 for the rest, at ₹50 + GST an order, and F&O stocks go into the
  closing auction at 15:15, where stop-loss orders are not allowed.
- **Paper is local.** Zerodha has no paper account, so paper mode never calls
  an order tool: fills are recorded in `trader\portfolio.json`.
- **Live needs setup.** The hosted Kite MCP (`https://mcp.kite.trade/mcp`) is
  read-only. To place orders you self-host the
  [Kite MCP server](https://github.com/zerodha/kite-mcp-server) with your own
  API key in environment variables (never in the chat), and since 1 April 2026
  Zerodha needs a registered static IP for API orders. Set place_order,
  modify_order and cancel_order to "Needs approval" in Claude, never
  "Always allow".

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation and it cannot see the future.
SEBI's own study found that about 7 in 10 individual intraday traders in the
cash segment lost money in FY23. You can lose money. Decide for yourself, and
talk to a SEBI-registered adviser if you need advice.

**No trade goes through until you say yes.** Only `/india-broker` can lead to
an order, and only after `/india-risk` passes that exact trade and you type
yes for it in the chat. No F&O, no short selling and no margin: this pack is
cash equity, long only, paid in full.

**Paper first.** `"paper": true` is the default in `config.example.json`.

**A stop is a plan, not a guarantee.** The stop is a stop-limit order. In a
fast fall it can fill down to its limit (the example's loss at the limit is
₹1,139.58, over the ₹1,000 max loss, which is measured at the trigger), and
a gap below the limit leaves it unfilled. Circuit limits can stop trading
in a stock altogether.

**The Time Machine is a simulation, not a prediction.** It is built from the
stock's own recent 5-minute bars. Results days, news, circuits and halts can
do things those sessions never did.

**The data is free and unofficial.** Yahoo's chart endpoint and NSE's website
APIs are not official APIs: they can lag, change or refuse you (NSE's Akamai
protection answers 403 at times; the tools retry once and then say so).
Yahoo's `.NS` bars have holes, and its 9:15 bar carries no volume. Check
anything that matters in Kite before you act on it.

**The calendar.** NSE's 2026 holidays and the 8 November 2026 Muhurat session
are built in. NSE has not published 2027 yet; until this pack is updated the
tools say so instead of guessing. The tick-size schedule is NSE's, reviewed
monthly.

**API trading rules.** SEBI's retail algo framework applies to orders sent
through your own API key. This pack sends one order at a time, after a typed
yes, far under the 10 orders a second threshold.

**Taxes.** Intraday gains are speculative business income in India. Nothing
here is tax advice.

## Files

```
india-scanner\      SKILL.md, scan.py
india-news\         SKILL.md, news.py
india-entry\        SKILL.md, entry.py
india-exit\         SKILL.md, exit.py
india-timemachine\  SKILL.md, timemachine.py
india-risk\         SKILL.md, check.py
india-budget\       SKILL.md, size.py, config.example.json, targets.example.json
india-broker\       SKILL.md, ticket.py, claude_desktop_config.example.json
india-alerts\       SKILL.md, alerts.py, alerts.example.json
india-analyst\      SKILL.md, results.py, metrics.example.json
india-insider\      SKILL.md, insider.py
docs\SPEC.md        the shared contract: IST, NSE calendar, tick, charges, the order shape
tests\              unittest suite + offline fixtures
```

MIT, like the original. Original pack by [Jake Schincariol](https://opusjake.ai).
