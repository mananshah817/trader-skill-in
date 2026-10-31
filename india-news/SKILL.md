---
name: india-news
description: >-
  News Monitor for NSE stocks. Tracks what moves every stock you watch: the
  company's own NSE corporate announcements (financial results, board
  meetings, dividends, bonus, split, buyback, credit ratings, acquisitions
  and SAST disclosures, resignations and appointments, pledges, trading
  window closures, news clarifications), Yahoo Finance headlines for the .NS
  symbol, and how far each stock is from yesterday's close. Flags RESULTS DAY
  when a board meeting on financial results falls on the day of your trade.
  Shows only what is new since the last check, newest first, in India time
  (IST), tags each item and never repeats one. Use when the user says "any
  news on RELIANCE", "why is INFY moving", "what's the news on my
  watchlist", "morning briefing", "pre-open news", "monitor the news", "did
  anything break", "any announcements", "check the NSE filings", "are
  results due", or after /india-scanner finds setups.
argument-hint: "[symbols]"
---

# india-news

A chart can look perfect and still be about to jump on a results
announcement, a board meeting outcome or a rumour clarification. Before a
setup becomes a trade, this skill reads the two feeds that carry those first,
for every stock on the watchlist, and tells you only what is new. It also
prints how far each stock has moved today, so a filing and a move sit side
by side.

Tool in this folder (standard library Python, no keys). The commands use
`python` (Windows); on macOS/Linux use `python3`.

```bash
python "${CLAUDE_SKILL_DIR}/news.py"                              # watchlist + plan + portfolio
python "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE INFY --hours 12
python "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE --trade-date 2026-10-30
python "${CLAUDE_SKILL_DIR}/news.py" --no-nse --json
```

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | the scanner's hits (india-scanner) |
| `trader/plan.json` | the trade you are planning (india-exit); its `date` is the trade date for the RESULTS DAY flag |
| `trader/portfolio.json` | what you hold (india-broker records fills there) |
| NSE corporate announcements | `https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=RELIANCE&from_date=12-10-2026&to_date=26-10-2026` (the window is at least 14 days, so a results date announced last week is still seen) |
| Yahoo Finance headline RSS | `https://feeds.finance.yahoo.com/rss/2.0/headline?s=RELIANCE.NS&region=US&lang=en-US` |
| Yahoo Finance chart endpoint | the last price against yesterday's close (unofficial, can lag) |
| `trader/state/news.json` | what has been shown (kept 30 days) |

Symbols are NSE trading symbols (RELIANCE, M&M, BAJAJ-AUTO); `RELIANCE.NS` is
accepted. `--no-nse` skips the announcements, `--no-moves` the price lines.

## The two sources, honestly

- **NSE announcements are the primary source.** They are what the company
  told the exchange, with the PDF linked. The endpoint is NSE's own website
  API: no key, but it is not an official public API. It answers a browser
  User-Agent with `Referer: https://www.nseindia.com/`; NSE's Akamai front
  can still answer 403 at any time. The tool then visits the NSE home page
  once for cookies and retries once; if that fails too it says "NSE refused
  the request ... try again in a minute". It never loops. On 2026-10-31 it
  answered a JSON list for RELIANCE, INFY, HDFCBANK and M&M (fields `an_dt`,
  `desc`, `attchmntText`, `attchmntFile`, `sm_name`, `seq_id`...), and `[]`
  for a symbol that does not exist.
- **Yahoo headlines are often empty for Indian stocks.** On 2026-10-31 the
  feed for RELIANCE.NS, HDFCBANK.NS and TATASTEEL.NS had no items while
  INFY.NS, TCS.NS, SBIN.NS and M&M.NS had 15 or 16. An empty feed is normal:
  the tool prints a note and carries on. It is not an error, and it is not
  "no news": the NSE announcements still are.

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story or the filing PDF and read it. Quote the source
  and link it. Never summarise a paywalled story you could not read: say so.
- **Filings beat headlines.** If an NSE announcement and a headline
  disagree, the announcement is what the company told the exchange.
- **Say what it touches.** Every item lists the symbols it touches (by
  "(INFY)", "NSE: INFY", "INFY.NS" or the company's name from its NSE
  filings) and its tags: results, board meeting, board outcome, dividend,
  bonus, split, buyback, credit rating, acquisition (SAST, mergers, schemes),
  people (resignations, appointments, MD / CEO / CFO changes), pledge,
  trading window, fund raising (QIP, IPO, NCDs, preferential issues),
  clarification (news / rumour verification), analyst, legal, risk, halt,
  regulatory, macro. Filings marked `!` carry something important: financial
  results, a board meeting outcome, an MD / CEO / CFO / auditor change or a
  resignation, default or insolvency, fraud, delisting, a pledge, a buyback,
  bonus or split, a cyber incident, a trading suspension.
- **RESULTS DAY.** When an announcement intimates a board meeting on
  financial results (or says when results are announced) and that date is
  the day of the trade (the plan's `date`, `--trade-date`, else today), the
  tool prints `RESULTS DAY` first and marks the announcement `!`. Results
  can come out during the session or after it; either way the price can jump
  past any stop. A results date that is coming but not on the trade day is
  printed as a plain `results` line. Not every company writes the date into
  the announcement text: when none is found the tool says so, and the board
  meetings list on nseindia.com has them all.
- **MOVING** marks a stock 3 % or more from yesterday's close (`--move-pct`).
  A move with no announcement and no headline is worth saying out loud too.
- **No predictions.** Report what was published and how far the price is from
  yesterday's close. Never say what the stock will do next.

## Steps

1. Run the check: `python "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives everything
   machine readable (moves, results, items, notes), `--dry-run` leaves the
   state alone.
2. Read what matters first: a `RESULTS DAY` line, every `!` filing, anything
   tagged results, board meeting, clarification, halt, legal, regulatory or
   risk, and every MOVING symbol.
3. Write a short brief, per symbol: what happened, the source and link, the
   move, and one line on why it matters to the setup (for example "the board
   meets on results today: the price can jump past any stop").
4. If something changes the picture, say so before `/india-entry` and
   `/india-exit`: results on the trade day, a rumour clarification, a pledge
   invocation, a trading window closure ahead of results. Never act on a
   headline alone.

During the session: `/loop 15m /india-news`, or a scheduled task each
morning before the 09:00 pre-open.

## Example: the reel

Monday 2026-10-26, RELIANCE broke its opening range at 10:00 IST. The check
at 10:00 (48 hours back):

```
RELIANCE   ₹1,193.60 +0.30% vs yesterday's close ₹1,190 at 10:00 IST
No results date in NSE announcements of the last 14 days for RELIANCE (not every company writes it
there: the board meetings list on nseindia.com has them all).

  2026-10-24 21:48 IST  RELIANCE    NSE           General Updates: Initial Public Offer of Jio Platforms Limited
      tags: fund raising  https://nsearchives.nseindia.com/corporate/PVIVINMA_24102026214355_RIL_SE_241026.pdf
note: Yahoo Finance has no headlines for RELIANCE.NS (its feed is often empty for Indian stocks: normal, not an error)
```

The brief: "One NSE announcement over the weekend: an update on the Jio
Platforms IPO. No Yahoo headlines (normal for RELIANCE). No results date
announced. The stock is up 0.30%: nothing unusual." The next evening
(27 Oct, 19:17 IST) Reliance announced its board meets on Friday 30 Oct on
the quarter's results. A check for a trade planned on 30 Oct then leads with:

```
RESULTS DAY  RELIANCE: financial results due Fri 2026-10-30, the day of your trade (NSE announcement
2026-10-27 19:17 IST). Results can come out during the session, and the price can jump past any stop.
```

(The results came out that evening at 19:40 IST, after the close.) When a
symbol has no news, say so rather than padding the brief.

## What it never does

- Never invents or embellishes a story, a quote or a number.
- Never trades on news, and never tells the user to buy or sell because of a
  headline or a filing.
- Never hammers a server: one request per feed per symbol, half a second
  between calls, and at most one retry at NSE.

## Safety

Not financial advice: this is software that reads public data and reports
it. This skill only reads and reports. Nothing places an order except
`/india-broker`, after `/india-risk` passes and the user types an explicit
yes for that order.
